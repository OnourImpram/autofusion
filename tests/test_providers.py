"""Adversarial tests for the strict provider trust boundary."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from autofusion.errors import NonCallableProviderError, OutputValidationError, ProviderError
from autofusion.models import CallStatus, ModelProfile, ProviderRequest
from autofusion.providers.base import CostEvidence
from autofusion.providers.cli import ClaudeCliAdapter, CliTransportProvider, CodexExecAdapter
from autofusion.providers.fake import DeterministicFakeProvider
from autofusion.providers.http import (
    AnthropicHttpProvider,
    HttpResponse,
    OpenAICompatibleHttpProvider,
)
from autofusion.providers.process import CommandOutcome, CommandRunner
from autofusion.registry import ProviderRegistry
from autofusion.util import JsonObject


def _profile(
    *,
    handle: str = "test",
    transport: str = "codex-exec",
    model: str = "model-1",
    canonical_model: str | None = None,
    effort: str = "xhigh",
    params: JsonObject | None = None,
) -> ModelProfile:
    return ModelProfile(
        handle=handle,
        transport=transport,
        callable=transport != "self",
        model=model,
        canonical_model=canonical_model or model,
        vendor="test-vendor",
        family="test-family",
        effort=effort,
        context="test",
        params=params or {},
    )


def _request(tmp_path: Path, *, schema: JsonObject | None = None) -> ProviderRequest:
    return ProviderRequest(
        run_id="run-1",
        call_id="call-1",
        handle="test",
        prompt="review this",
        response_schema=schema or {"type": "object", "required": ["answer"]},
        working_directory=tmp_path,
        timeout_s=2.0,
        max_output_chars=512,
    )


@dataclass
class RecordingRunner:
    outcome: CommandOutcome
    last_message: str = '{"answer":"ok"}'
    argv: tuple[str, ...] | None = None
    input_text: str | None = None
    environment_allowlist: tuple[str, ...] | None = None

    def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandOutcome:
        self.argv = argv
        input_text = kwargs["input_text"]
        assert isinstance(input_text, str)
        self.input_text = input_text
        environment_allowlist = kwargs["environment_allowlist"]
        assert isinstance(environment_allowlist, tuple)
        self.environment_allowlist = environment_allowlist
        last_message_index = argv.index("--output-last-message") + 1
        Path(argv[last_message_index]).write_text(self.last_message, encoding="utf-8")
        return self.outcome


@dataclass(frozen=True)
class UltraProbe:
    supported: bool

    def supports_reasoning_effort(self, effort: str) -> bool:
        return effort == "ultra" and self.supported


@dataclass
class FakeHttpClient:
    response: HttpResponse
    calls: list[tuple[str, dict[str, str], bytes, float, int]] = field(default_factory=list)

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout_s: float,
        max_bytes: int,
    ) -> HttpResponse:
        self.calls.append((url, dict(headers), body, timeout_s, max_bytes))
        return self.response


def _outcome(stdout: str, *, returncode: int = 0, timed_out: bool = False) -> CommandOutcome:
    return CommandOutcome(
        argv=("provider",),
        returncode=returncode,
        stdout=stdout,
        stderr="diagnostic",
        duration_ms=4,
        timed_out=timed_out,
        truncated=False,
    )


def test_command_runner_passes_metacharacters_as_literal_argv(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    argument = f"literal; echo unsafe > {marker} && $(whoami)"
    outcome = CommandRunner().run(
        (sys.executable, "-c", "import sys; print(sys.argv[1])", argument),
        input_text="",
        cwd=tmp_path,
        timeout_s=2.0,
        max_output_chars=512,
        environment_allowlist=(),
    )
    assert outcome.returncode == 0
    assert outcome.stdout.strip() == argument
    assert not marker.exists()


def test_command_runner_bounds_output_while_process_is_running(tmp_path: Path) -> None:
    outcome = CommandRunner().run(
        (sys.executable, "-c", "import sys; sys.stdout.write('x' * 200000)"),
        input_text="",
        cwd=tmp_path,
        timeout_s=2.0,
        max_output_chars=97,
        environment_allowlist=(),
    )
    assert outcome.returncode == 0
    assert outcome.truncated
    assert len(outcome.stdout) <= 97


def test_command_runner_times_out_and_terminates_child(tmp_path: Path) -> None:
    started = time.monotonic()
    outcome = CommandRunner().run(
        (sys.executable, "-c", "import time; time.sleep(30)"),
        input_text="",
        cwd=tmp_path,
        timeout_s=0.1,
        max_output_chars=128,
        environment_allowlist=(),
    )
    assert outcome.timed_out
    assert time.monotonic() - started < 3.0


def test_command_runner_does_not_inherit_secret_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTOFUSION_TEST_SECRET", "must-not-reach-child")
    outcome = CommandRunner().run(
        (sys.executable, "-c", "import os; print(os.getenv('AUTOFUSION_TEST_SECRET'))"),
        input_text="",
        cwd=tmp_path,
        timeout_s=2.0,
        max_output_chars=128,
        environment_allowlist=(),
    )
    assert outcome.returncode == 0
    assert outcome.stdout.strip() == "None"


def test_cli_adapter_is_injectable_and_keeps_prompt_out_of_argv(tmp_path: Path) -> None:
    request = _request(tmp_path)
    request = replace(request, prompt="$(danger); not an argv argument")
    runner = RecordingRunner(_outcome('{"model":"model-1"}'))
    provider = CliTransportProvider(
        _profile(
            params={
                "sandbox": "read-only",
                "ephemeral": True,
                "ignore_user_config": True,
                "ignore_rules": True,
            }
        ),
        CodexExecAdapter(executable="fake-codex"),
        runner,
    )
    result = provider.invoke(request)
    assert result.status is CallStatus.COMPLETED
    assert runner.argv is not None
    assert all(request.prompt not in item for item in runner.argv)
    assert runner.input_text == request.prompt
    assert runner.argv[-1] == "-"
    assert "--output-schema" in runner.argv
    assert "--output-last-message" in runner.argv
    assert "--ephemeral" in runner.argv
    assert "--ignore-user-config" in runner.argv
    assert "--ignore-rules" in runner.argv
    assert "--skip-git-repo-check" in runner.argv
    assert runner.argv[runner.argv.index("--sandbox") + 1] == "read-only"
    assert runner.argv[runner.argv.index("--config") + 1] == "model_reasoning_effort=xhigh"
    assert runner.environment_allowlist is not None
    assert "CODEX_HOME" in runner.environment_allowlist
    assert "PATH" in runner.environment_allowlist


def test_cli_rejects_malformed_json(tmp_path: Path) -> None:
    provider = CliTransportProvider(
        _profile(),
        CodexExecAdapter(),
        RecordingRunner(_outcome('{"model":"model-1"}'), last_message="not-json"),
    )
    with pytest.raises(OutputValidationError, match="valid JSON"):
        provider.invoke(_request(tmp_path))


def test_cli_rejects_identity_mismatch(tmp_path: Path) -> None:
    provider = CliTransportProvider(
        _profile(),
        CodexExecAdapter(),
        RecordingRunner(_outcome('{"model":"other"}')),
    )
    with pytest.raises(ProviderError, match="identity mismatch"):
        provider.invoke(_request(tmp_path))


def test_cli_rejects_schema_violation(tmp_path: Path) -> None:
    provider = CliTransportProvider(
        _profile(),
        CodexExecAdapter(),
        RecordingRunner(_outcome('{"model":"model-1"}'), last_message='{"wrong":"shape"}'),
    )
    with pytest.raises(OutputValidationError, match="violates schema"):
        provider.invoke(_request(tmp_path))


def test_cli_timeout_is_a_failed_result_not_a_trusted_output(tmp_path: Path) -> None:
    provider = CliTransportProvider(
        _profile(),
        CodexExecAdapter(),
        RecordingRunner(_outcome("", timed_out=True)),
    )
    result = provider.invoke(_request(tmp_path))
    assert result.status is CallStatus.TIMEOUT
    assert result.structured_output is None


def test_claude_cli_uses_native_schema_and_read_only_tools(tmp_path: Path) -> None:
    request = _request(tmp_path)
    adapter = ClaudeCliAdapter(executable="fake-claude")
    argv = adapter.build_argv(
        _profile(
            transport="claude-exec",
            effort="xhigh",
            params={
                "permission_mode": "plan",
                "no_session_persistence": True,
                "allowed_tools": ["Read", "Glob", "Grep"],
            },
        ),
        request,
        output_schema_path=tmp_path / "unused-schema.json",
        output_last_message_path=tmp_path / "unused-output.json",
    )
    assert "--safe-mode" in argv
    assert "--json-schema" in argv
    assert json.loads(argv[argv.index("--json-schema") + 1]) == request.response_schema
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep"


def test_codex_ultra_fails_closed_without_runtime_capability_proof(tmp_path: Path) -> None:
    provider = CliTransportProvider(
        _profile(effort="ultra"),
        CodexExecAdapter(),
        RecordingRunner(_outcome('{"model":"model-1"}')),
    )
    with pytest.raises(ProviderError, match="ultra reasoning effort is unproven"):
        provider.invoke(_request(tmp_path))


def test_codex_ultra_requires_explicit_runtime_capability_proof(tmp_path: Path) -> None:
    runner = RecordingRunner(_outcome('{"model":"model-1"}'))
    provider = CliTransportProvider(
        _profile(effort="ultra"),
        CodexExecAdapter(capability_probe=UltraProbe(supported=True)),
        runner,
    )
    assert provider.invoke(_request(tmp_path)).status is CallStatus.COMPLETED
    assert runner.argv is not None
    assert runner.argv[runner.argv.index("--config") + 1] == "model_reasoning_effort=ultra"


def test_openai_compatible_transport_validates_identity_usage_and_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_OPENAI_KEY", "secret-value")
    response = {
        "model": "model-1",
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        "choices": [{"message": {"content": '{"answer":"ok"}'}}],
    }
    client = FakeHttpClient(HttpResponse(200, json.dumps(response).encode("utf-8")))

    def cost(_: ModelProfile, __: JsonObject) -> CostEvidence:
        return CostEvidence(cost_usd=0.12, verified=True, source="provider-usage")

    provider = OpenAICompatibleHttpProvider(
        _profile(transport="openai-compatible"),
        "https://provider.example/v1",
        "TEST_OPENAI_KEY",
        client,
        cost,
    )
    result = provider.invoke(_request(tmp_path))
    assert result.input_tokens == 3
    assert result.output_tokens == 4
    assert result.cost_usd == 0.12
    assert result.cost_verified
    assert result.provider_usage_hash is not None
    assert result.routing_attestation_hash is not None
    _, headers, body, _, _ = client.calls[0]
    assert headers["Authorization"] == "Bearer secret-value"
    assert json.loads(body)["response_format"]["type"] == "json_schema"


def test_anthropic_transport_is_injectable_and_requires_json_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "secret-value")
    response = {
        "model": "claude-1",
        "usage": {"input_tokens": 2, "output_tokens": 5},
        "content": [{"type": "text", "text": '{"answer":"ok"}'}],
    }
    client = FakeHttpClient(HttpResponse(200, json.dumps(response).encode("utf-8")))
    provider = AnthropicHttpProvider(
        _profile(transport="anthropic-http", model="claude-1"),
        "https://provider.example",
        "TEST_ANTHROPIC_KEY",
        client,
    )
    result = provider.invoke(_request(tmp_path))
    assert result.effective_model == "claude-1"
    assert client.calls[0][1]["x-api-key"] == "secret-value"
    body = json.loads(client.calls[0][2])
    assert body["output_config"]["format"]["type"] == "json_schema"


def test_self_provider_is_non_callable_from_registry(tmp_path: Path) -> None:
    from autofusion.providers.base import SelfProvider

    sentinel = SelfProvider(_profile(handle="self", transport="self"))
    registry = ProviderRegistry({"self": sentinel})
    request = replace(_request(tmp_path), handle="self")
    with pytest.raises(NonCallableProviderError):
        registry.invoke(request)


def test_deterministic_fake_has_stable_test_evidence(tmp_path: Path) -> None:
    provider = DeterministicFakeProvider(_profile(), {"answer": "ok"})
    request = _request(tmp_path)
    assert provider.invoke(request).output_hash == provider.invoke(request).output_hash
    assert provider.fingerprint(request) == provider.fingerprint(request)
