"""Bounded ACP stdio client with a Grok launch policy for read-only review.

Gemini CLI reuse: NOT_RUN. Its launch and identity contracts are not verified.
"""

from __future__ import annotations

import json
import math
import os
import queue
import stat
import subprocess
import threading
import time
import tomllib
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from autofusion.errors import NonCallableProviderError, ProviderError
from autofusion.models import CallStatus, ModelProfile, ProviderRequest, ProviderResult
from autofusion.providers._job import WindowsJob
from autofusion.providers.base import (
    CostResolver,
    assert_effective_identity,
    build_result,
    no_cost_evidence,
)
from autofusion.providers.process import minimal_environment
from autofusion.util import JsonObject, canonical_json_bytes

_ENVIRONMENT = (
    "PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "GROK_HOME",
)
_CONFIG_LIMIT = 262_144
_SYSTEM_CONFIG_DIRECTORY = Path("/etc/grok") if os.name != "nt" else None
_BLOCKED_CONFIG_KEYS = frozenset({
    "api_key", "auth_provider", "auth_provider_command", "base_url",
    "cli_chat_proxy_base_url", "endpoints", "env_http_headers", "env_key", "extra_headers",
    "hooks", "hooks_paths", "lsp_servers", "mcp_servers", "model", "models_base_url",
    "models_list_url", "plugins", "query_params", "xai_api_base_url",
})


def _metadata(path: Path, label: str) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if (stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & 0x400):
        raise ProviderError(f"ACP {label} cannot use links or reparse points: {path}")
    return metadata


def _check_config(path: Path) -> None:
    metadata = _metadata(path, "configuration")
    if metadata is None:
        return
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _CONFIG_LIMIT:
        raise ProviderError(f"ACP configuration must be a bounded regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_dev != metadata.st_dev
                or opened.st_ino != metadata.st_ino or opened.st_size != metadata.st_size):
            raise ProviderError(f"ACP configuration changed during inspection: {path}")
        raw = stream.read(_CONFIG_LIMIT + 1)
    if len(raw) > _CONFIG_LIMIT:
        raise ProviderError(f"ACP configuration exceeds its size limit: {path}")
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        raise ProviderError(f"ACP configuration is not valid bounded TOML: {path}") from exc
    pending: list[object] = [parsed]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if any(key.casefold().replace("-", "_") in _BLOCKED_CONFIG_KEYS for key in item):
                raise ProviderError(
                    f"ACP configuration contains an executable or routing selector: {path}"
                )
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def _empty_extension(path: Path) -> None:
    metadata = _metadata(path, "extension")
    if metadata is None:
        return
    if stat.S_ISREG(metadata.st_mode) and metadata.st_size == 0:
        return
    if stat.S_ISDIR(metadata.st_mode):
        with os.scandir(path) as entries:
            if next(entries, None) is None:
                return
    raise ProviderError(f"ACP extension location must be empty or absent: {path}")


def _preflight_grok(environment: dict[str, str], cwd: Path) -> None:
    """Read configuration only; never enumerate or open identity/session records."""
    configured_home = environment.get("GROK_HOME")
    if not configured_home:
        home_name = "USERPROFILE" if os.name == "nt" else "HOME"
        user_home = environment.get(home_name)
        if not user_home:
            raise ProviderError("ACP Grok configuration home is unavailable")
        configured_home = str(Path(user_home) / ".grok")
    home = Path(configured_home).absolute()
    try:
        metadata = _metadata(home, "configuration home")
        if metadata is None or not stat.S_ISDIR(metadata.st_mode):
            raise ProviderError("ACP Grok configuration home must be an existing directory")
        for name in ("config.toml", "managed_config.toml", "requirements.toml"):
            _check_config(home / name)
        for name in ("hooks", "hooks-paths", "plugins"):
            _empty_extension(home / name)
        if _SYSTEM_CONFIG_DIRECTORY is not None:
            for name in ("managed_config.toml", "requirements.toml"):
                _check_config(_SYSTEM_CONFIG_DIRECTORY / name)
        # Installed Grok 1.0.13 docs/user-guide/10-hooks.md: native SessionStart
        # hooks run outside tool permission decisions. Reject their discovery sources.
        current = cwd.resolve(strict=True)
        while True:
            project = current / ".grok"
            project_metadata = _metadata(project, "configuration directory")
            if project_metadata is not None and not stat.S_ISDIR(project_metadata.st_mode):
                raise ProviderError(f"ACP configuration directory is not a directory: {project}")
            _check_config(project / "config.toml")
            for name in ("hooks", "hooks-paths", "plugins"):
                _empty_extension(project / name)
            _empty_extension(current / ".mcp.json")
            # Installed configuration reference lists session.load_envrc; no verified
            # launcher override disables it, so refuse its executable input instead.
            _empty_extension(current / ".envrc")
            if (current / ".git").exists() or current.parent == current:
                break
            current = current.parent
    except OSError as exc:
        raise ProviderError("ACP configuration preflight could not inspect its sources") from exc


class AcpProtocolError(ProviderError):
    """The peer violated its framing, lifecycle, or message contract."""


class AcpAuthenticationError(ProviderError):
    """The peer explicitly returned ACP authentication-required code -32000."""


class _Stopped(Exception):
    def __init__(self, status: CallStatus) -> None:
        self.status = status


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise AcpProtocolError(f"ACP {label} must be an object")
    return dict(value)


def _unique_object(pairs: list[tuple[str, object]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise AcpProtocolError("ACP message has duplicate object keys")
        result[key] = value
    return result


def _invalid_constant(_: str) -> object:
    raise AcpProtocolError("ACP malformed JSON number")


def _strict_json_object(text: str, label: str) -> JsonObject:
    try:
        value: object = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant,
        )
    except (ValueError, RecursionError) as exc:
        raise AcpProtocolError(f"ACP malformed JSON {label}") from exc
    return _object(value, label)


def _message(raw: bytes) -> JsonObject:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AcpProtocolError("ACP message is not UTF-8") from exc
    message = _strict_json_object(text, "message")
    if message.get("jsonrpc") != "2.0":
        raise AcpProtocolError("ACP message requires JSON-RPC 2.0")
    if "id" in message and (
        isinstance(message["id"], bool) or not isinstance(message["id"], (int, str))
    ):
        raise AcpProtocolError("ACP message has invalid response ID")
    if "method" in message:
        if not isinstance(message["method"], str) or not message["method"]:
            raise AcpProtocolError("ACP message method must be a nonempty string")
        if "result" in message or "error" in message:
            raise AcpProtocolError("ACP method message cannot contain a response")
        _object(message.get("params", {}), "params")
    elif (
        "id" not in message or ("result" in message) == ("error" in message)
    ):
        raise AcpProtocolError("ACP response requires an ID and one result or error")
    return message


def _close_owned_process(process: subprocess.Popen[bytes], job: WindowsJob) -> None:
    try:
        if os.name == "nt":
            job.close()
        else:
            # A direct child exit does not mean that its process group is empty.
            killpg = getattr(os, "killpg", None)
            if killpg is None:
                raise ProviderError("ACP process group termination is unavailable")
            with suppress(ProcessLookupError):
                killpg(process.pid, 9)
    except OSError as exc:
        raise ProviderError("ACP process tree cleanup failed") from exc
    finally:
        if process.poll() is None:
            process.kill()
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=0.25)


class _Connection:
    """Keep pipe I/O off the deadline thread and bound queued protocol data."""

    def __init__(self, process: subprocess.Popen[bytes], limit: int, job: WindowsJob) -> None:
        self.process = process
        self.job = job
        self.incoming: queue.Queue[bytes | ProviderError | None] = queue.Queue(maxsize=8)
        self.outgoing: queue.Queue[bytes] = queue.Queue(maxsize=8)
        self.closed = threading.Event()
        self.frame_limit = max(65_536, min(limit * 4, 4_194_304))
        self.total_limit = max(1_048_576, min(limit * 32, 67_108_864))
        self.threads: list[threading.Thread] = []
        assert process.stdin is not None and process.stdout is not None
        assert process.stderr is not None
    def start(self) -> None:
        assert self.process.stdin is not None and self.process.stdout is not None
        assert self.process.stderr is not None
        for target, stream in (
            (self._read, self.process.stdout), (self._write, self.process.stdin),
            (self._drain, self.process.stderr),
        ):
            thread = threading.Thread(target=target, args=(stream,), daemon=True)
            thread.start()
            self.threads.append(thread)

    def _publish(self, value: bytes | ProviderError | None) -> None:
        while not self.closed.is_set():
            try:
                self.incoming.put(value, timeout=0.05)
                return
            except queue.Full:
                continue

    def _read(self, stream: BinaryIO) -> None:
        pending = bytearray()
        total = 0
        try:
            while not self.closed.is_set():
                chunk = os.read(stream.fileno(), 8192)
                if not chunk:
                    if pending:
                        self._publish(AcpProtocolError("ACP incomplete message at EOF"))
                    else:
                        self._publish(None)
                    return
                total += len(chunk)
                if total > self.total_limit:
                    self._publish(AcpProtocolError("ACP cumulative message limit exceeded"))
                    return
                pending.extend(chunk)
                while b"\n" in pending:
                    boundary = pending.index(b"\n")
                    if boundary > self.frame_limit:
                        self._publish(AcpProtocolError("ACP message limit exceeded"))
                        return
                    self._publish(bytes(pending[:boundary]))
                    del pending[:boundary + 1]
                if len(pending) > self.frame_limit:
                    self._publish(AcpProtocolError("ACP message limit exceeded"))
                    return
        except OSError:
            if not self.closed.is_set():
                self._publish(AcpProtocolError("ACP stdout failed before terminal response"))
        finally:
            stream.close()

    def _write(self, stream: BinaryIO) -> None:
        try:
            while not self.closed.is_set():
                try:
                    data = self.outgoing.get(timeout=0.05)
                except queue.Empty:
                    continue
                stream.write(data)
                stream.flush()
        except OSError:
            if not self.closed.is_set():
                self._publish(AcpProtocolError("ACP stdin closed before terminal response"))
        finally:
            stream.close()

    def _drain(self, stream: BinaryIO) -> None:
        # Untrusted diagnostics can contain identity data. Drain without retaining.
        try:
            while not self.closed.is_set() and os.read(stream.fileno(), 8192):
                pass
        except OSError:
            pass
        finally:
            stream.close()

    def send(self, message: JsonObject) -> None:
        try:
            self.outgoing.put_nowait(canonical_json_bytes(message) + b"\n")
        except queue.Full as exc:
            raise AcpProtocolError("ACP peer is not consuming protocol replies") from exc

    def receive(self, timeout: float) -> JsonObject | None:
        try:
            value = self.incoming.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None
        if value is None:
            raise AcpProtocolError("ACP EOF before terminal response")
        if isinstance(value, ProviderError):
            raise value
        return _message(value)

    def close(self) -> None:
        self.closed.set()
        try:
            _close_owned_process(self.process, self.job)
        finally:
            for thread in self.threads:
                thread.join(timeout=0.1)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()


class _Session:
    def __init__(
        self, connection: _Connection, request: ProviderRequest, profile: ModelProfile,
        deadline: float, cancel_event: threading.Event | None,
    ) -> None:
        self.connection = connection
        self.request = request
        self.profile = profile
        self.deadline = deadline
        self.cancel_event = cancel_event
        self.session_id: str | None = None
        self.effective_model: str | None = None
        self.model_option_ids: set[str] = set()
        self.parts: list[str] = []
        self.output_chars = 0
        self.next_id = 1
        self.prompt_id: int | None = None

    def check_deadline(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise _Stopped(CallStatus.CANCELLED)
        if time.monotonic() >= self.deadline:
            raise _Stopped(CallStatus.TIMEOUT)

    def observe_model(self, value: JsonObject) -> None:
        # Model state is peer-reported routing evidence, never inferred from argv.
        # ACP standard PromptResponse contains no effective upstream model field.
        # Source: https://agentclientprotocol.com/protocol/v1/session-config-options
        candidates: list[object] = []
        options = value.get("configOptions")
        if options is not None:
            if not isinstance(options, list):
                raise AcpProtocolError("ACP configOptions must be an array")
            for option in options:
                entry = _object(option, "config option")
                option_id = entry.get("id")
                if not isinstance(option_id, str) or not option_id:
                    raise AcpProtocolError("ACP config option omitted its ID")
                if entry.get("category") == "model" or option_id in self.model_option_ids:
                    self.model_option_ids.add(option_id)
                    candidates.append(entry.get("currentValue"))
        # Grok 1.0.13 extension, encoded by the supplied local ACP bridge contract.
        meta = value.get("_meta")
        if isinstance(meta, dict) and isinstance(meta.get("modelState"), dict):
            state = meta["modelState"]
            if "currentModelId" in state:
                candidates.append(state["currentModelId"])
        for candidate in candidates:
            self.effective_model = assert_effective_identity(
                expected=self.profile.canonical_model, actual=candidate,
            )

    def dispatch(self, message: JsonObject, *, cancelling: bool = False) -> None:
        method = message["method"]
        params = _object(message.get("params", {}), "params")
        if "id" in message:
            if method == "session/request_permission":
                if params.get("sessionId") != self.session_id or self.session_id is None:
                    raise AcpProtocolError("ACP permission has unexpected session ID")
                options = params.get("options")
                if not isinstance(options, list):
                    raise AcpProtocolError("ACP permission options must be an array")
                _object(params.get("toolCall"), "permission toolCall")
                outcome: JsonObject = {"outcome": "cancelled"}
                # Source: https://agentclientprotocol.com/protocol/v1/tool-calls
                if not cancelling:
                    seen_options: set[str] = set()
                    for option in options:
                        entry = _object(option, "permission option")
                        option_id = entry.get("optionId")
                        option_kind = entry.get("kind")
                        if (not isinstance(option_kind, str) or option_kind not in {
                            "allow_once", "allow_always", "reject_once", "reject_always",
                        }):
                            raise AcpProtocolError("ACP permission option has invalid kind")
                        if not isinstance(option_id, str) or not option_id:
                            raise AcpProtocolError("ACP permission option has invalid ID")
                        if option_id in seen_options:
                            raise AcpProtocolError("ACP duplicate permission option ID")
                        seen_options.add(option_id)
                        if (option_kind in {"reject_once", "reject_always"}
                                and outcome["outcome"] == "cancelled"):
                            outcome = {"outcome": "selected", "optionId": option_id}
                self.connection.send({"jsonrpc": "2.0", "id": message["id"],
                                      "result": {"outcome": outcome}})
            else:
                # No fs or terminal implementation exists, even for unsolicited calls.
                self.connection.send({"jsonrpc": "2.0", "id": message["id"], "error": {
                    "code": -32601, "message": "Method unavailable under read-only policy",
                }})
            return
        if method != "session/update":
            return
        if params.get("sessionId") != self.session_id or self.session_id is None:
            raise AcpProtocolError("ACP update has unexpected session ID")
        update = _object(params.get("update"), "session update")
        kind = update.get("sessionUpdate")
        if not isinstance(kind, str) or not kind:
            raise AcpProtocolError("ACP session update omitted its type")
        self.observe_model(update)
        if kind == "agent_message_chunk":
            content = _object(update.get("content"), "message content")
            if content.get("type") != "text" or not isinstance(content.get("text"), str):
                raise AcpProtocolError("ACP review content must be text")
            text = content["text"]
            self.output_chars += len(text)
            if self.output_chars > self.request.max_output_chars:
                raise AcpProtocolError("ACP output limit exceeded")
            self.parts.append(text)

    def rpc(self, method: str, params: JsonObject) -> JsonObject:
        self.check_deadline()
        identifier = self.next_id
        self.next_id += 1
        if method == "session/prompt":
            self.prompt_id = identifier
        self.connection.send({"jsonrpc": "2.0", "id": identifier,
                              "method": method, "params": params})
        while True:
            self.check_deadline()
            message = self.connection.receive(min(0.025, self.deadline - time.monotonic()))
            if message is None:
                continue
            try:
                self.check_deadline()
            except _Stopped:
                # The request has left the queue, so cancellation must still answer it.
                if "method" in message and "id" in message:
                    self.dispatch(message, cancelling=True)
                raise
            if "method" in message:
                self.dispatch(message)
                continue
            if message["id"] != identifier:
                raise AcpProtocolError("ACP unexpected response ID")
            if "error" in message:
                error = _object(message["error"], "error")
                code = error.get("code")
                if (isinstance(code, bool) or not isinstance(code, int)
                        or not isinstance(error.get("message"), str)):
                    raise AcpProtocolError("ACP error response has invalid fields")
                if code == -32000:
                    raise AcpAuthenticationError(f"ACP authentication required during {method}")
                if code == -32800:
                    raise _Stopped(CallStatus.CANCELLED)
                raise AcpProtocolError(f"ACP remote protocol error {code} during {method}")
            return _object(message["result"], "result")

    def cancel(self) -> None:
        if self.session_id is None or self.prompt_id is None:
            return
        # The grace window only delivers cancellation and declines pending permissions.
        # Source: https://agentclientprotocol.com/protocol/v1/prompt-turn#cancellation
        self.connection.send({"jsonrpc": "2.0", "method": "session/cancel",
                              "params": {"sessionId": self.session_id}})
        until = time.monotonic() + 0.1
        while time.monotonic() < until:
            message = self.connection.receive(min(0.01, until - time.monotonic()))
            if message is None:
                continue
            if "method" in message:
                self.dispatch(message, cancelling=True)
            elif message.get("id") == self.prompt_id:
                return


@dataclass(frozen=True, slots=True)
class AcpProvider:
    """One session per invocation; all client-side tool requests are denied."""

    profile: ModelProfile
    executable: str = "grok"
    arguments: tuple[str, ...] | None = None
    cancel_event: threading.Event | None = None
    cost_resolver: CostResolver = no_cost_evidence

    def build_argv(self) -> tuple[str, ...]:
        if self.arguments is not None:
            return (self.executable, *self.arguments)
        # Grok 1.0.13 installed docs/user-guide/{15-agent-mode,22-permissions-and-safety}.md:
        # the bare '*' deny rule covers every tool and wins over persisted allow rules.
        # Help-only probes on 2026-09-08 confirm --deny belongs before 'agent'.
        return (self.executable, "--deny", "*", "agent", "--model", self.profile.model,
                "--reasoning-effort", self.profile.effort,
                "--no-leader", "stdio")

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        if not self.profile.enabled or not self.profile.callable:
            raise NonCallableProviderError("ACP profile is disabled or non-callable")
        if any(value.casefold() == "fable" or value.casefold().startswith("claude-fable")
               for value in (self.profile.model, self.profile.canonical_model)):
            raise NonCallableProviderError("Fable is reserved for the non-callable self provider")
        if not math.isfinite(request.timeout_s) or request.timeout_s <= 0:
            raise ProviderError("ACP timeout must be positive and finite")
        if request.max_output_chars < 0:
            raise ProviderError("ACP output limit must be nonnegative")
        argv = self.build_argv()
        if any(not isinstance(part, str) or not part for part in argv):
            raise ProviderError("ACP command must be a nonempty argv sequence")
        started = time.monotonic()
        if self.cancel_event is not None and self.cancel_event.is_set():
            return self._failed(request, started, CallStatus.CANCELLED, "ACP call cancelled")
        # Caller allowlists cannot expand the transport's reviewed routing boundary.
        environment = minimal_environment(_ENVIRONMENT)
        configured_home = self.profile.params.get("grok_home")
        if isinstance(configured_home, str) and configured_home:
            environment["GROK_HOME"] = configured_home
        environment["GROK_DEFAULT_SELECTED_PERMISSION"] = "reject"
        environment["GROK_DISABLE_API_KEY_AUTH"] = "1"
        environment["GROK_OAUTH_ENABLED"] = "1"
        # Installed Grok 1.0.13 docs/user-guide/26-config-reference.md, cli.auto_update.
        environment["GROK_DISABLE_AUTOUPDATER"] = "1"
        # Disable ambient compatibility extensions; OAuth remains in its existing store.
        for source in ("CLAUDE", "CURSOR", "CODEX"):
            for feature in ("SKILLS", "RULES", "AGENTS", "MCPS", "HOOKS", "SESSIONS"):
                environment[f"GROK_{source}_{feature}_ENABLED"] = "0"
        for feature in ("MEMORY", "SUBAGENTS", "WORKFLOWS", "WEB_FETCH", "IMAGE_GEN", "VIDEO_GEN"):
            environment[f"GROK_{feature}"] = "0"
        _preflight_grok(environment, request.working_directory)
        if self.cancel_event is not None and self.cancel_event.is_set():
            return self._failed(request, started, CallStatus.CANCELLED, "ACP call cancelled")
        if time.monotonic() >= started + request.timeout_s:
            return self._failed(request, started, CallStatus.TIMEOUT, "ACP deadline exceeded")
        try:
            job = WindowsJob()
        except OSError as exc:
            raise ProviderError("ACP failed to create process ownership") from exc
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=request.working_directory, env=environment, shell=False,
                creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                               | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                               | job.creationflags)
                if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
            job.assign_and_resume(process.pid)
        except OSError as exc:
            if process is not None:
                process.kill()
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=0.25)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
            with suppress(OSError):
                job.close()
            raise ProviderError("ACP failed to start provider command") from exc
        connection: _Connection | None = None
        session: _Session | None = None
        try:
            try:
                connection = _Connection(process, request.max_output_chars, job)
                connection.start()
                session = _Session(connection, request, self.profile,
                                   started + request.timeout_s, self.cancel_event)
            except (RuntimeError, OSError) as exc:
                raise ProviderError("ACP I/O startup failed") from exc
            return self._run(session, request, started)
        except _Stopped as stopped:
            if session is not None:
                with suppress(ProviderError):
                    session.cancel()
            error = "ACP deadline exceeded" if stopped.status == CallStatus.TIMEOUT else (
                "ACP call cancelled")
            return self._failed(request, started, stopped.status, error)
        finally:
            if connection is not None:
                connection.close()
            else:
                try:
                    _close_owned_process(process, job)
                finally:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            stream.close()

    def _run(self, session: _Session, request: ProviderRequest, started: float) -> ProviderResult:
        from autofusion import __version__

        # Source: https://agentclientprotocol.com/protocol/v1/initialization
        initialized = session.rpc("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                   "terminal": False},
            "clientInfo": {"name": "autofusion", "version": __version__},
        })
        if type(initialized.get("protocolVersion")) is not int or (
            initialized["protocolVersion"] != 1
        ):
            raise AcpProtocolError("ACP unsupported protocol version")
        session.observe_model(initialized)
        created = session.rpc("session/new", {
            "cwd": str(request.working_directory.resolve()), "mcpServers": [],
        })
        identifier = created.get("sessionId")
        if not isinstance(identifier, str) or not identifier:
            raise AcpProtocolError("ACP response omitted a valid session ID")
        session.session_id = identifier
        session.observe_model(created)
        prompt = ("Review in read-only analysis mode. Return only a JSON object matching this "
                  "response schema:\n" + canonical_json_bytes(request.response_schema).decode()
                  + "\n\n" + request.prompt)
        terminal = session.rpc("session/prompt", {
            "sessionId": identifier, "prompt": [{"type": "text", "text": prompt}],
        })
        stop = terminal.get("stopReason")
        if not isinstance(stop, str):
            raise AcpProtocolError("ACP missing or unknown stop reason")
        if stop == "cancelled":
            return self._failed(request, started, CallStatus.CANCELLED, "ACP peer cancelled")
        if stop in {"max_tokens", "max_turn_requests", "refusal"}:
            return self._failed(request, started, CallStatus.FAILED, f"ACP stopped: {stop}")
        if stop != "end_turn":
            raise AcpProtocolError("ACP missing or unknown stop reason")
        session.observe_model(terminal)
        effective = assert_effective_identity(
            expected=self.profile.canonical_model, actual=session.effective_model,
        )
        output = "".join(session.parts)
        usage: JsonObject | None = None
        supplied_usage = terminal.get("usage")
        if supplied_usage is not None:
            usage = _object(supplied_usage, "usage")
            for source, target in (("inputTokens", "input_tokens"),
                                   ("outputTokens", "output_tokens")):
                count = usage.get(source)
                if type(count) is not int or count < 0:
                    raise AcpProtocolError("ACP usage contains an invalid token count")
                usage[target] = count
        return build_result(
            profile=self.profile, request=request, effective_model=effective,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            output_text=output, structured_output=_strict_json_object(output, "output"),
            usage=usage, cost_resolver=self.cost_resolver,
        )

    def _failed(
        self, request: ProviderRequest, started: float, status: CallStatus, error: str,
    ) -> ProviderResult:
        return ProviderResult(
            call_id=request.call_id, handle=self.profile.handle,
            requested_model=self.profile.model, effective_model=None,
            vendor=self.profile.vendor, family=self.profile.family, mode=self.profile.effort,
            compound=self.profile.compound, worker_visibility=self.profile.worker_visibility,
            status=status, duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            output_text="", structured_output=None, output_hash=None, error=error,
        )
