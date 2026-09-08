"""Antigravity headless transport with isolated configuration and terminal evidence."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile

from autofusion.errors import OutputValidationError, ProviderError
from autofusion.models import CallStatus, ModelProfile, ProviderRequest, ProviderResult
from autofusion.providers._job import WindowsJob
from autofusion.providers.base import (
    CostResolver,
    assert_effective_identity,
    build_result,
    no_cost_evidence,
)
from autofusion.providers.process import (
    CommandOutcome,
    _BoundedBytes,
    _read_pipe,
    minimal_environment,
)
from autofusion.util import JsonObject


def _unique_fields(pairs: list[tuple[str, object]]) -> JsonObject:
    fields: JsonObject = {}
    for key, value in pairs:
        if key in fields:
            raise OutputValidationError("agy JSON contains duplicate fields")
        fields[key] = value
    return fields


def _invalid_number(_: str) -> object:
    raise OutputValidationError("agy JSON contains a nonfinite number")


def _decode(text: str) -> JsonObject:
    try:
        value: object = json.loads(
            text, object_pairs_hook=_unique_fields, parse_constant=_invalid_number
        )
    except (ValueError, RecursionError) as exc:
        raise OutputValidationError("agy output is not valid bounded JSON") from exc
    if not isinstance(value, dict):
        raise OutputValidationError("agy JSON must be an object")
    return dict(value)


def _failure_kind(signal: str) -> str:
    """Classify provider diagnostics without retaining provider-controlled text."""
    for pattern, kind in (
        (r"quota|resource.exhausted|rate.limit|too many requests", "rate_limited"),
        (r"oauth|authenticat|login|sign.in", "auth_required"),
        (
            r"model.*(?:not available|unknown|invalid|not recognized)|"
            r"unsupported.model|invalid.model.selection",
            "model_unavailable",
        ),
        (r"permission.denied|access.denied|forbidden|auto.denied", "permission_denied"),
    ):
        if re.search(pattern, signal, re.IGNORECASE):
            return kind
    return "terminal_failure"


def _terminal(stdout: str) -> tuple[JsonObject, str | None]:
    result: JsonObject | None = None
    failure: str | None = None
    for line in stdout.split("\n"):
        if not line.strip():
            continue
        if result is not None:
            raise OutputValidationError("agy emitted data after the terminal result")
        event = _decode(line)
        name = event.get("event")
        if not isinstance(name, str):
            raise OutputValidationError("agy stream event name must be a string")
        if name in {"init", "step_update", "result"}:
            payload = event.get(name)
            if not isinstance(payload, dict):
                raise OutputValidationError(f"agy invalid {name} event")
            if name == "result":
                result = dict(payload)
            elif name == "step_update":
                if "step_index" in payload and (
                    type(payload["step_index"]) is not int or payload["step_index"] < 0
                ):
                    raise OutputValidationError("agy invalid step_update step_index")
                for field in ("state", "step_type"):
                    if field in payload and (
                        not isinstance(payload[field], str) or not payload[field]
                    ):
                        raise OutputValidationError(f"agy invalid step_update {field}")
                _usage(payload.get("usage"))
                delta = payload.get("text_delta", "")
                if not isinstance(delta, str):
                    raise OutputValidationError("agy invalid step_update text_delta")
                # Progress is decoded but cannot replace the terminal response or usage.
        elif name == "error":
            error = event.get("error", event.get("message"))
            if not isinstance(error, str):
                raise OutputValidationError("agy invalid error event")
            failure = _failure_kind(error)
        else:
            raise OutputValidationError("agy unknown stream event")
    if result is None:
        if failure is not None:
            return {"status": "ERROR", "response": ""}, failure
        raise OutputValidationError("agy stream omitted terminal result")
    if not isinstance(result.get("status"), str) or not isinstance(result.get("response"), str):
        raise OutputValidationError("agy invalid terminal result")
    if "error" in result and not isinstance(result["error"], str):
        raise OutputValidationError("agy invalid terminal error")
    return result, failure


def _usage(value: object) -> JsonObject | None:
    if value is None:
        return None
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or type(count) is not int or count < 0
        for key, count in value.items()
    ):
        raise OutputValidationError("agy invalid terminal usage")
    return dict(value)


@dataclass(frozen=True, slots=True)
class AgyProvider:
    profile: ModelProfile
    executable: str = "agy"
    arguments: tuple[str, ...] = ()
    cost_resolver: CostResolver = no_cost_evidence

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        if not self.profile.enabled or not self.profile.callable:
            raise ProviderError("agy profile is disabled or non-callable")
        if any(
            name.casefold().startswith(("claude-fable", "fable"))
            for name in (self.profile.model, self.profile.canonical_model)
        ):
            raise ProviderError("Fable is restricted to the non-callable self session")
        if not math.isfinite(request.timeout_s) or request.timeout_s <= 0:
            raise ProviderError("agy deadline must be finite and positive")
        if request.max_output_chars <= 0:
            raise ProviderError("agy output limit must be positive")
        started = time.monotonic()
        deadline = started + request.timeout_s
        if request.deadline_monotonic is not None:
            if not math.isfinite(request.deadline_monotonic):
                raise ProviderError("execution deadline must be finite")
            deadline = min(deadline, request.deadline_monotonic)
        prompt = (
            request.prompt
            + "\nReturn only JSON matching this schema:\n"
            + json.dumps(request.response_schema, ensure_ascii=True)
        )
        # Source: https://www.antigravity.google/docs/cli/headless/#streaming-input
        wire = (json.dumps({"event": "user", "message": {"content": prompt}}) + "\n").encode()
        if len(wire) > 32 * 1024 * 1024:
            raise ProviderError("agy input exceeds the bounded stdin limit")
        with TemporaryDirectory(prefix="autofusion-agy-") as temporary:
            home = Path(temporary)
            if os.name != "nt":
                home.chmod(0o700)
            settings_dir = home / ".gemini" / "antigravity-cli"
            settings_dir.mkdir(parents=True)
            # Sandbox covers terminal execution; workspace writes need explicit denial.
            # Source: https://www.antigravity.google/docs/cli/permissions/
            settings = {
                "permissions": {
                    "allow": [],
                    "deny": [
                        "write_file(*)",
                        "command(*)",
                        "unsandboxed(*)",
                        "mcp(*)",
                        "read_file(*)",
                        "read_url(*)",
                        "execute_url(*)",
                        "browser(*)",
                    ],
                }
            }
            (settings_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
            mcp_dir = home / ".gemini" / "config"
            mcp_dir.mkdir()
            (mcp_dir / "mcp_config.json").write_text('{"mcpServers":{}}', encoding="utf-8")
            environment = minimal_environment(("PATH", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"))
            environment.update(
                {
                    "HOME": str(home),
                    "USERPROFILE": str(home),
                    "AGY_CLI_HIDE_ACCOUNT_INFO": "1",
                    "AGY_CLI_DISABLE_AUTO_UPDATE": "true",
                }
            )
            # HOME is exclusively configuration. Existing OAuth stays in agy's AppData store.
            argv = (
                self.executable,
                *self.arguments,
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--model",
                self.profile.model,
                "--effort",
                self.profile.effort,
                "--sandbox",
                "--disable-slash-commands",
                "--print-timeout",
                f"{request.timeout_s}s",
            )
            try:
                outcome = _run(argv, wire, request, environment, home, started, deadline)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ProviderError("agy process isolation or cleanup failed") from exc
        if outcome.timed_out:
            return self._failed(request, outcome, CallStatus.TIMEOUT, "timeout")
        if outcome.truncated:
            raise OutputValidationError("agy exceeded the output limit")
        if not outcome.stdout.strip() and outcome.returncode != 0:
            kind = _failure_kind(outcome.stderr)
            if kind == "terminal_failure":
                kind = f"process_exit:{outcome.returncode}"
            return self._failed(request, outcome, CallStatus.FAILED, kind)
        terminal, failure = _terminal(outcome.stdout)
        status = str(terminal["status"]).upper()
        if status in {"CANCELED", "CANCELLED", "INTERRUPTED"}:
            return self._failed(request, outcome, CallStatus.CANCELLED, "cancelled")
        if failure is not None or status != "SUCCESS" or terminal.get("error"):
            return self._failed(
                request,
                outcome,
                CallStatus.FAILED,
                failure or _failure_kind(str(terminal.get("error", ""))),
            )
        if outcome.returncode != 0:
            return self._failed(
                request, outcome, CallStatus.FAILED, f"process_exit:{outcome.returncode}"
            )
        actual = assert_effective_identity(
            expected=self.profile.canonical_model, actual=terminal.get("model")
        )
        response = str(terminal["response"])
        if not response.strip():
            raise OutputValidationError("agy terminal response is empty")
        return build_result(
            profile=self.profile,
            request=request,
            effective_model=actual,
            duration_ms=outcome.duration_ms,
            output_text=response,
            structured_output=_decode(response),
            usage=_usage(terminal.get("usage")),
            cost_resolver=self.cost_resolver,
        )

    def _failed(
        self, request: ProviderRequest, outcome: CommandOutcome, status: CallStatus, error: str
    ) -> ProviderResult:
        return ProviderResult(
            call_id=request.call_id,
            handle=self.profile.handle,
            requested_model=self.profile.model,
            effective_model=None,
            configured_model=self.profile.canonical_model,
            observed_model=None,
            identity_evidence="unavailable",
            quota_group=self.profile.quota_group,
            vendor=self.profile.vendor,
            family=self.profile.family,
            mode=self.profile.effort,
            compound=self.profile.compound,
            worker_visibility=self.profile.worker_visibility,
            status=status,
            duration_ms=outcome.duration_ms,
            output_text="",
            structured_output=None,
            output_hash=None,
            error=f"agy:{error}",
            truncated=outcome.truncated,
        )


class _StrictBytes(_BoundedBytes):
    def text(self) -> str:
        if self.truncated:
            return ""
        try:
            return bytes(self._data).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OutputValidationError("agy stream is not UTF-8") from exc


def _run(
    argv: tuple[str, ...],
    wire: bytes,
    request: ProviderRequest,
    environment: dict[str, str],
    home: Path,
    started: float,
    deadline: float,
) -> CommandOutcome:
    stdout = _StrictBytes(request.max_output_chars)
    stderr = _BoundedBytes(request.max_output_chars)
    timed_out = False
    # File-backed stdin cannot block on an unresponsive child. It is call-scoped and removed.
    # Source: https://docs.python.org/3.11/library/subprocess.html#subprocess.Popen
    with TemporaryFile(dir=home) as input_file:
        input_file.write(wire)
        input_file.seek(0)
        if time.monotonic() >= deadline:
            return CommandOutcome(argv, None, "", "", 0, True, False)
        job = WindowsJob()
        try:
            process = subprocess.Popen(
                argv,
                stdin=input_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=request.working_directory,
                env=environment,
                shell=False,
                start_new_session=os.name != "nt",
                creationflags=job.creationflags
                | (getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
            )
        except OSError as exc:
            job.close()
            raise ProviderError("agy failed to start provider command") from exc
        try:
            job.assign_and_resume(process.pid)
        except BaseException:
            process.kill()
            process.wait(timeout=1)
            job.close()
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            raise
        assert process.stdout is not None and process.stderr is not None
        readers = [
            threading.Thread(target=_read_pipe, args=(stream, sink), daemon=True)
            for stream, sink in ((process.stdout, stdout), (process.stderr, stderr))
        ]
        started_readers: list[threading.Thread] = []
        try:
            for reader in readers:
                try:
                    reader.start()
                except RuntimeError as exc:
                    raise ProviderError("agy I/O worker could not start") from exc
                started_readers.append(reader)
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                if stdout.truncated or stderr.truncated:
                    break
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        finally:
            try:
                if os.name == "nt":
                    job.close()
                else:
                    # A process group survives its leader. Kill remaining members even on success.
                    with suppress(ProcessLookupError):
                        killpg = getattr(os, "killpg", None)
                        assert killpg is not None
                        killpg(process.pid, 9)
            finally:
                process.wait(timeout=1)
                for reader in started_readers:
                    reader.join(timeout=1)
                if not any(reader.is_alive() for reader in started_readers):
                    process.stdout.close()
                    process.stderr.close()
        if any(reader.is_alive() for reader in started_readers):
            raise ProviderError("agy standard streams did not close")
    return CommandOutcome(
        argv,
        process.returncode,
        stdout.text(),
        stderr.text(),
        int((time.monotonic() - started) * 1000),
        timed_out,
        stdout.truncated or stderr.truncated,
    )
