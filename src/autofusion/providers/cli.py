"""Injectable argv-only adapters for Codex and Claude command line transports."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Protocol

from autofusion.errors import OutputValidationError, ProviderError
from autofusion.models import CallStatus, ModelProfile, ProviderRequest, ProviderResult
from autofusion.providers.base import (
    CostResolver,
    assert_effective_identity,
    build_result,
    decode_json_object,
    no_cost_evidence,
)
from autofusion.providers.process import CommandOutcome
from autofusion.util import JsonObject, atomic_write_bytes, bounded_text, canonical_json_bytes

_COMMON_CLI_ENVIRONMENT = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "TEMP",
    "TMP",
)


class CliAdapter(Protocol):
    """Build provider-specific argv without executing or quoting shell text."""

    def build_argv(
        self,
        profile: ModelProfile,
        request: ProviderRequest,
        *,
        output_schema_path: Path,
        output_last_message_path: Path,
    ) -> tuple[str, ...]:
        """Build a complete argv sequence. The prompt is always passed through stdin."""

    def requires_output_last_message(self) -> bool:
        """Return whether trusted structured output is written to the supplied path."""

    def required_environment(self) -> tuple[str, ...]:
        """Declare the minimal extra environment needed for this adapter."""

    def resolve_effective_model(
        self, profile: ModelProfile, events: tuple[JsonObject, ...]
    ) -> object:
        """Resolve the effective model from trusted CLI routing evidence."""


class CodexCapabilityProbe(Protocol):
    """Runtime capability evidence for nonstandard Codex reasoning effort values."""

    def supports_reasoning_effort(self, effort: str) -> bool:
        """Return true only after probing the installed CLI's accepted effort values."""


@dataclass(frozen=True, slots=True)
class StaticCodexCapabilityProbe:
    """Capability values loaded from a separately captured doctor attestation."""

    supported_efforts: frozenset[str]

    def supports_reasoning_effort(self, effort: str) -> bool:
        return effort in self.supported_efforts


class CommandExecutor(Protocol):
    """Minimal execution seam for unit tests and CLI providers."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str,
        cwd: Path,
        timeout_s: float,
        max_output_chars: int,
        environment_allowlist: Sequence[str],
    ) -> CommandOutcome:
        """Run one command and return bounded output."""


@dataclass(frozen=True, slots=True)
class CodexExecAdapter:
    executable: str = "codex"
    capability_probe: CodexCapabilityProbe | None = None
    allow_live_probe: bool = False

    def build_argv(
        self,
        profile: ModelProfile,
        request: ProviderRequest,
        *,
        output_schema_path: Path,
        output_last_message_path: Path,
    ) -> tuple[str, ...]:
        del request
        effort = profile.effort
        if effort == "ultra" and (
            self.capability_probe is None
            or not self.capability_probe.supports_reasoning_effort("ultra")
        ) and not self.allow_live_probe:
            raise ProviderError(
                "Codex ultra reasoning effort is unproven for this installed CLI and is blocked"
            )
        if effort not in {"low", "medium", "high", "xhigh", "ultra"}:
            raise ProviderError(f"unsupported Codex reasoning effort: {effort}")
        argv = [
            self.executable,
            "exec",
            "--json",
            "--output-schema",
            str(output_schema_path),
            "--output-last-message",
            str(output_last_message_path),
            "--model",
            profile.model,
            "--config",
            f"model_reasoning_effort={effort}",
        ]
        sandbox = profile.params.get("sandbox")
        if isinstance(sandbox, str) and sandbox:
            argv.extend(["--sandbox", sandbox])
        if profile.params.get("ephemeral") is True:
            argv.append("--ephemeral")
        if profile.params.get("ignore_user_config") is True:
            argv.append("--ignore-user-config")
        if profile.params.get("ignore_rules") is True:
            argv.append("--ignore-rules")
        if profile.params.get("skip_git_repo_check", True) is True:
            argv.append("--skip-git-repo-check")
        return (*argv, "-")

    def requires_output_last_message(self) -> bool:
        return True

    def required_environment(self) -> tuple[str, ...]:
        # --ignore-user-config does not disable CODEX_HOME-based authentication.
        return (*_COMMON_CLI_ENVIRONMENT, "CODEX_HOME")

    def resolve_effective_model(
        self, profile: ModelProfile, events: tuple[JsonObject, ...]
    ) -> object:
        observed = _effective_model(events, expected=profile.canonical_model)
        if observed is not None:
            return observed
        # Codex 0.144 JSONL omits model identity. Trust the pinned local route only
        # when the event stream contains the terminal success evidence Codex emits.
        if _codex_turn_completed(events):
            return profile.canonical_model
        return None


@dataclass(frozen=True, slots=True)
class ClaudeCliAdapter:
    executable: str = "claude"

    def build_argv(
        self,
        profile: ModelProfile,
        request: ProviderRequest,
        *,
        output_schema_path: Path,
        output_last_message_path: Path,
    ) -> tuple[str, ...]:
        del output_schema_path, output_last_message_path
        response_schema = dict(request.response_schema)
        response_schema.pop("$schema", None)
        argv = [
            self.executable,
            "--print",
            "--safe-mode",
            "--input-format",
            "text",
            "--output-format",
            "json",
            "--model",
            profile.model,
            "--effort",
            profile.effort,
            "--prompt-suggestions",
            "false",
            "--json-schema",
            canonical_json_bytes(response_schema).decode("utf-8"),
        ]
        permission_mode = profile.params.get("permission_mode")
        if isinstance(permission_mode, str) and permission_mode:
            argv.extend(["--permission-mode", permission_mode])
        if profile.params.get("no_session_persistence") is True:
            argv.append("--no-session-persistence")
        allowed_tools = profile.params.get("allowed_tools")
        if isinstance(allowed_tools, list) and all(
            isinstance(tool, str) and tool for tool in allowed_tools
        ):
            tools = ",".join(allowed_tools)
            argv.extend(["--tools", tools, "--allowedTools", tools])
        return tuple(argv)

    def requires_output_last_message(self) -> bool:
        return False

    def required_environment(self) -> tuple[str, ...]:
        return (*_COMMON_CLI_ENVIRONMENT, "CLAUDE_CONFIG_DIR")

    def resolve_effective_model(
        self, profile: ModelProfile, events: tuple[JsonObject, ...]
    ) -> object:
        return _effective_model(events, expected=profile.canonical_model)


def _response_envelope(text: str) -> JsonObject:
    """Accept one object or a JSONL CLI event stream, retaining the final object."""

    try:
        return decode_json_object(text, source="provider CLI")
    except OutputValidationError as whole_error:
        objects: list[JsonObject] = []
        for line in text.splitlines():
            try:
                objects.append(decode_json_object(line, source="provider CLI event"))
            except OutputValidationError:
                continue
        if not objects:
            raise whole_error
        return objects[-1]


def _response_events(text: str) -> tuple[JsonObject, ...]:
    try:
        return (decode_json_object(text, source="provider CLI"),)
    except OutputValidationError as whole_error:
        events: list[JsonObject] = []
        for line in text.splitlines():
            try:
                events.append(decode_json_object(line, source="provider CLI event"))
            except OutputValidationError:
                continue
        if not events:
            raise whole_error
        return tuple(events)


def _model_output_tokens(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    tokens = value.get("outputTokens", value.get("output_tokens"))
    return tokens if isinstance(tokens, int) and tokens >= 0 else None


def _effective_model(events: tuple[JsonObject, ...], *, expected: str) -> object:
    for event in reversed(events):
        for key in ("effective_model", "model"):
            candidate = event.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        model_usage = event.get("modelUsage", event.get("model_usage"))
        if isinstance(model_usage, dict):
            models = [key for key in model_usage if isinstance(key, str) and key]
            if len(models) == 1:
                return models[0]
            if expected in models:
                token_counts = {
                    model: _model_output_tokens(model_usage[model]) for model in models
                }
                expected_tokens = token_counts[expected]
                other_tokens = [
                    tokens for model, tokens in token_counts.items() if model != expected
                ]
                if (
                    expected_tokens is not None
                    and expected_tokens > 0
                    and all(tokens is not None for tokens in other_tokens)
                    and expected_tokens
                    > sum(tokens for tokens in other_tokens if tokens is not None)
                ):
                    return expected
    return None


def _codex_turn_completed(events: tuple[JsonObject, ...]) -> bool:
    failed = any(event.get("type") in {"error", "turn.failed"} for event in events)
    completed = any(
        event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict)
        for event in events
    )
    return completed and not failed


def _usage_from_events(events: tuple[JsonObject, ...]) -> JsonObject | None:
    for event in reversed(events):
        usage = _usage_from_envelope(event)
        if usage is not None:
            return usage
    return None


def _error_tail(events: tuple[JsonObject, ...], *, limit: int) -> str:
    messages: list[str] = []
    for event in events:
        if event.get("type") == "error" and isinstance(event.get("message"), str):
            messages.append(str(event["message"]))
        if event.get("is_error") is True and isinstance(event.get("result"), str):
            messages.append(str(event["result"]))
        error = event.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            messages.append(str(error["message"]))
        item = event.get("item")
        if (
            isinstance(item, dict)
            and item.get("type") == "error"
            and isinstance(item.get("message"), str)
        ):
            messages.append(str(item["message"]))
    return bounded_text("\n".join(dict.fromkeys(messages)), limit)[0]


def _structured_from_envelope(envelope: JsonObject) -> JsonObject:
    for key in ("structured_output", "result", "response"):
        candidate = envelope.get(key)
        if isinstance(candidate, dict) and all(isinstance(name, str) for name in candidate):
            return dict(candidate)
    output = envelope.get("output")
    if isinstance(output, str):
        return decode_json_object(output, source="provider CLI output")
    return envelope


def _usage_from_envelope(envelope: JsonObject) -> JsonObject | None:
    usage = envelope.get("usage")
    if isinstance(usage, dict) and all(isinstance(name, str) for name in usage):
        return dict(usage)
    return None


@dataclass(slots=True)
class CliTransportProvider:
    profile: ModelProfile
    adapter: CliAdapter
    runner: CommandExecutor
    cost_resolver: CostResolver = no_cost_evidence

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        if not self.profile.callable:
            raise ProviderError(f"provider {self.profile.handle} is marked non-callable")
        deadline = monotonic() + request.timeout_s
        if request.deadline_monotonic is not None:
            deadline = min(deadline, request.deadline_monotonic)
        request = replace(request, deadline_monotonic=deadline)
        request.remaining_timeout_s()
        with TemporaryDirectory(prefix="autofusion-provider-") as directory:
            output_directory = Path(directory)
            schema_path = output_directory / "reviewer-output.schema.json"
            last_message_path = output_directory / "last-message.json"
            atomic_write_bytes(schema_path, canonical_json_bytes(request.response_schema))
            argv = self.adapter.build_argv(
                self.profile,
                request,
                output_schema_path=schema_path,
                output_last_message_path=last_message_path,
            )
            environment_allowlist = _environment_allowlist(
                request.environment_allowlist,
                self.adapter.required_environment(),
            )
            outcome = self.runner.run(
                argv,
                input_text=request.prompt,
                cwd=request.working_directory,
                timeout_s=request.remaining_timeout_s(),
                max_output_chars=request.max_output_chars,
                environment_allowlist=environment_allowlist,
            )
            if outcome.timed_out:
                return self._failed_result(
                    request,
                    outcome,
                    CallStatus.TIMEOUT,
                    "provider command timed out",
                )
            if outcome.returncode != 0:
                return self._failed_result(
                    request,
                    outcome,
                    CallStatus.FAILED,
                    f"provider command exited with status {outcome.returncode}",
                )
            output_text = _trusted_output_text(
                adapter=self.adapter,
                last_message_path=last_message_path,
                stdout=outcome.stdout,
                limit=request.max_output_chars,
            )
            events = _response_events(outcome.stdout)
            effective_model = assert_effective_identity(
                expected=self.profile.canonical_model,
                actual=self.adapter.resolve_effective_model(self.profile, events),
            )
            return build_result(
                profile=self.profile,
                request=request,
                effective_model=effective_model,
                duration_ms=outcome.duration_ms,
                output_text=output_text,
                structured_output=decode_json_object(output_text, source="provider CLI output"),
                usage=_usage_from_events(events),
                cost_resolver=self.cost_resolver,
                stderr_tail=outcome.stderr,
                truncated=outcome.truncated,
            )

    def _failed_result(
        self,
        request: ProviderRequest,
        outcome: CommandOutcome,
        status: CallStatus,
        error: str,
    ) -> ProviderResult:
        stdout, truncated = bounded_text(outcome.stdout, request.max_output_chars)
        try:
            structured_error = _error_tail(
                _response_events(outcome.stdout), limit=request.max_output_chars
            )
        except OutputValidationError:
            structured_error = ""
        diagnostics = [item for item in (outcome.stderr, structured_error) if item]
        stderr_tail = bounded_text(
            "\n".join(dict.fromkeys(diagnostics)), request.max_output_chars
        )[0]
        return ProviderResult(
            call_id=request.call_id,
            handle=self.profile.handle,
            requested_model=self.profile.model,
            effective_model=None,
            vendor=self.profile.vendor,
            family=self.profile.family,
            mode=self.profile.effort,
            compound=self.profile.compound,
            worker_visibility=self.profile.worker_visibility,
            status=status,
            duration_ms=outcome.duration_ms,
            output_text=stdout,
            structured_output=None,
            output_hash=None,
            error=error,
            stderr_tail=stderr_tail,
            truncated=outcome.truncated or truncated,
        )


def _environment_allowlist(
    request_allowlist: Sequence[str], adapter_allowlist: Sequence[str]
) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*request_allowlist, *adapter_allowlist)))


def _trusted_output_text(
    *, adapter: CliAdapter, last_message_path: Path, stdout: str, limit: int
) -> str:
    if not adapter.requires_output_last_message():
        envelope = _response_envelope(stdout)
        return _structured_output_text(envelope)
    try:
        with last_message_path.open("rb") as handle:
            raw = handle.read(limit + 1)
    except FileNotFoundError as exc:
        message = "provider CLI did not create --output-last-message output"
        raise OutputValidationError(message) from exc
    if len(raw) > limit:
        raise OutputValidationError("provider CLI last-message output exceeded configured limit")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OutputValidationError("provider CLI last-message output is not UTF-8") from exc


def _structured_output_text(envelope: JsonObject) -> str:
    structured = _structured_from_envelope(envelope)
    return canonical_json_bytes(structured).decode("utf-8")
