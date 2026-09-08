"""Isolated CLI, doctor, and registry boundary coverage."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn, cast

import pytest

from autofusion import cli, doctor
from autofusion.config import FusionConfig, load_config
from autofusion.engine import FusionRunRequest, PendingRun
from autofusion.errors import ConfigurationError, ProviderError
from autofusion.grounding import (
    GroundingRunner,
    RunnerOutput,
    VerificationCommand,
)
from autofusion.models import (
    CallStatus,
    ModelProfile,
    ProviderRequest,
    ProviderResult,
    RunArtifacts,
    SnapshotManifest,
)
from autofusion.proof import ProofCapsule, ProofRunner
from autofusion.providers import (
    AnthropicHttpProvider,
    CliTransportProvider,
    CommandRunner,
    OpenAICompatibleHttpProvider,
    SelfProvider,
    UrllibHttpClient,
)
from autofusion.providers.cli import ClaudeCliAdapter, CodexExecAdapter
from autofusion.reconcile import FindingDisposition
from autofusion.registry import ProviderRegistry
from autofusion.util import JsonObject, read_json_object


@pytest.fixture(autouse=True)
def _isolate_global_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTOFUSION_GLOBAL_CONFIG", str(tmp_path / "absent-global.json"))


@pytest.fixture(autouse=True)
def _forbid_live_provider_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_command(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("a test attempted to invoke a live provider command")

    def forbidden_http(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("a test attempted to invoke a live provider HTTP endpoint")

    monkeypatch.setattr(CommandRunner, "run", forbidden_command)
    monkeypatch.setattr(UrllibHttpClient, "post", forbidden_http)


def _json_object(text: str) -> dict[str, object]:
    parsed: object = json.loads(text)
    assert isinstance(parsed, dict)
    assert all(isinstance(key, str) for key in parsed)
    return cast(dict[str, object], parsed)


def _stdout_json(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    captured = capsys.readouterr()
    assert captured.out
    return _json_object(captured.out)


def _object(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in value)
    return cast(dict[str, object], value)


def _profile(handle: str = "gpt-sol") -> ModelProfile:
    return ModelProfile(
        handle=handle,
        transport="controlled",
        callable=True,
        model="controlled-model",
        canonical_model="controlled-model",
        vendor="controlled-vendor",
        family="controlled-family",
        effort="high",
        context="packet",
    )


@dataclass
class RecordingProvider:
    profile: ModelProfile
    status: CallStatus = CallStatus.COMPLETED
    requests: list[ProviderRequest] = field(default_factory=list)

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        self.requests.append(request)
        completed = self.status is CallStatus.COMPLETED
        structured_output: JsonObject | None = {"answer": "controlled"} if completed else None
        return ProviderResult(
            call_id=request.call_id,
            handle=request.handle,
            requested_model=self.profile.model,
            effective_model=self.profile.model if completed else None,
            vendor=self.profile.vendor,
            family=self.profile.family,
            mode="controlled-fake",
            compound=False,
            worker_visibility="not-applicable",
            status=self.status,
            duration_ms=7,
            output_text='{"answer":"controlled"}' if completed else "",
            structured_output=structured_output,
            output_hash="a" * 64 if completed else None,
            error=None if completed else "controlled provider failure",
        )


@dataclass
class RecordingGroundingRunner:
    output: RunnerOutput = field(
        default_factory=lambda: RunnerOutput(exit_code=0, stdout="controlled success")
    )
    network_denied: bool = True
    calls: list[tuple[tuple[str, ...], Path, float]] = field(default_factory=list)

    def run(self, argv: Sequence[str], cwd: Path, timeout_s: float) -> RunnerOutput:
        self.calls.append((tuple(argv), cwd, timeout_s))
        return self.output


@dataclass
class RecordingRunEngine:
    result: PendingRun | RunArtifacts
    requests: list[FusionRunRequest] = field(default_factory=list)

    def run(self, request: FusionRunRequest) -> PendingRun | RunArtifacts:
        self.requests.append(request)
        return self.result


@dataclass
class RecordingFinalizeEngine:
    result: RunArtifacts
    calls: list[tuple[str, tuple[FindingDisposition, ...] | None, bool]] = field(
        default_factory=list
    )

    def finalize(
        self,
        run_id: str,
        *,
        dispositions: tuple[FindingDisposition, ...] | None,
        automatic: bool,
        proof_capsules: tuple[ProofCapsule, ...] = (),
    ) -> RunArtifacts:
        assert proof_capsules == ()
        self.calls.append((run_id, dispositions, automatic))
        return self.result


def _artifacts(tmp_path: Path, run_id: str = "run-complete") -> RunArtifacts:
    return RunArtifacts(
        run_id=run_id,
        analysis_path=tmp_path / run_id / "analysis.json",
        receipt_path=tmp_path / run_id / "receipt.json",
        analysis={"run_id": run_id},
        receipt={
            "state": "signed_off",
            "verdict": "ship",
            "fused": True,
            "receipt_hash": "b" * 64,
        },
    )


def _install_call_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: RecordingProvider,
) -> list[tuple[Path, Path]]:
    registry = ProviderRegistry({provider.profile.handle: provider})
    snapshot_root = tmp_path / f"snapshot-{provider.status.value}"
    snapshot_root.mkdir()
    snapshot_calls: list[tuple[Path, Path]] = []

    def fake_from_config(_config: FusionConfig) -> ProviderRegistry:
        return registry

    def fake_build_snapshot(source_root: Path, destination_root: Path) -> SnapshotManifest:
        snapshot_calls.append((source_root, destination_root))
        return SnapshotManifest(
            root=snapshot_root,
            snapshot_hash="c" * 64,
            manifest_hash="d" * 64,
            entries=(),
            source_root=source_root,
        )

    monkeypatch.setattr(
        ProviderRegistry,
        "from_config",
        staticmethod(fake_from_config),
    )
    monkeypatch.setattr(cli, "build_snapshot", fake_build_snapshot)
    return snapshot_calls


def _observation(*, passed: bool, second: bool = False) -> dict[str, object]:
    return {
        "task_id": "task-1",
        "arm": "solo",
        "input_hash": "e" * 64,
        "budget_hash": "f" * 64,
        "passed": passed,
        "verified_resolved": not second,
        "reported_findings": 0 if second else 2,
        "confirmed_findings": 0 if second else 1,
        "expected_critical": 1,
        "confirmed_critical": 0 if second else 1,
        "regressions": 1 if second else 0,
        "latency_ms": 200 if second else 100,
        "cost_usd": 0.25 if second else 0.5,
        "used_calls": 1,
        "confirmed_finding_ids": [] if second else ["finding-1"],
    }


def test_config_validate_reports_explicit_source_and_catalogs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "controlled-config.json"
    config_path.write_text("{}", encoding="utf-8")

    status = cli.main(
        [
            "config-validate",
            "--repo",
            str(tmp_path),
            "--config",
            str(config_path),
        ]
    )

    assert status == 0
    payload = _stdout_json(capsys)
    assert payload["valid"] is True
    assert payload["sources"] == [str(config_path.resolve())]
    assert "self" in cast(list[object], payload["models"])
    assert "adaptive" in cast(list[object], payload["presets"])
    panels = payload["panels"]
    assert isinstance(panels, list)
    assert all(isinstance(panel, str) for panel in panels)
    panel_names = cast(list[str], panels)
    assert panel_names == sorted(panel_names)


def test_config_validate_rejects_untrusted_repository_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / ".fusion.json").write_text("{}", encoding="utf-8")
    caplog.set_level(logging.ERROR, logger="autofusion")

    status = cli.main(["config-validate", "--repo", str(tmp_path)])

    assert status == 2
    assert capsys.readouterr().out == ""
    assert "trust-repo-config" in caplog.text


def test_config_validate_rejects_invalid_explicit_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_path = tmp_path / "invalid.json"
    config_path.write_text('{"models":{"self":{"callable":true}}}', encoding="utf-8")
    caplog.set_level(logging.ERROR, logger="autofusion")

    status = cli.main(["config-validate", "--config", str(config_path)])

    assert status == 2
    assert capsys.readouterr().out == ""
    assert "self must be a non-callable self transport" in caplog.text


def test_doctor_helpers_resolve_configured_and_fallback_executables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "controlled-tool.exe"
    executable.write_bytes(b"")
    missing = tmp_path / "missing-tool.exe"

    assert doctor._executable({"params": {"executable": "custom"}}, "fallback") == "custom"
    assert doctor._executable({"params": []}, "fallback") == "fallback"
    assert doctor._executable("not-a-mapping", "fallback") == "fallback"
    assert doctor._resolve_executable(str(executable)) == str(executable)
    assert doctor._resolve_executable(str(missing)) is None

    def fake_which(command: str) -> str | None:
        return "C:\\controlled\\tool.exe" if command == "controlled" else None

    monkeypatch.setattr(shutil, "which", fake_which)
    assert doctor._resolve_executable("controlled") == "C:\\controlled\\tool.exe"
    assert doctor._resolve_executable("absent") is None


def test_doctor_version_probe_is_bounded_and_failure_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def successful_run(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, dict(kwargs)))
        return subprocess.CompletedProcess(argv, 0, stdout=b"x" * 300, stderr=b"ignored")

    monkeypatch.setattr(subprocess, "run", successful_run)
    version = doctor._version("controlled")
    assert version == "x" * 256
    assert calls == [
        (
            ["controlled", "--version"],
            {
                "check": False,
                "capture_output": True,
                "shell": False,
                "timeout": 10.0,
            },
        )
    ]

    def empty_run(
        argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 7, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", empty_run)
    assert doctor._version("controlled") == "exit=7"

    def timeout_run(argv: list[str], **_kwargs: object) -> NoReturn:
        raise subprocess.TimeoutExpired(argv, 10.0)

    monkeypatch.setattr(subprocess, "run", timeout_run)
    assert doctor._version("controlled") == "version probe failed"


def test_inspect_environment_covers_model_states_without_exposing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "controlled-secret-that-must-not-appear"
    monkeypatch.setenv("AUTOFUSION_TEST_READY", secret)
    monkeypatch.delenv("AUTOFUSION_TEST_MISSING", raising=False)
    config = FusionConfig(
        data={
            "models": {
                "disabled": {"transport": "codex-exec", "enabled": False},
                "self": {"transport": "self", "callable": False},
                "codex": {
                    "transport": "codex-exec",
                    "params": {"executable": "controlled-codex"},
                },
                "claude": {"transport": "claude-exec"},
                "api-ready": {
                    "transport": "openai-compatible",
                    "key_env": "AUTOFUSION_TEST_READY",
                },
                "api-missing": {
                    "transport": "anthropic-http",
                    "key_env": "AUTOFUSION_TEST_MISSING",
                },
                "unsupported": {"transport": "unsupported-transport"},
            }
        },
        source_paths=(),
    )

    def fake_resolve(command: str) -> str | None:
        return "C:\\controlled\\codex.exe" if command == "controlled-codex" else None

    def fake_version(executable: str) -> str:
        assert executable == "C:\\controlled\\codex.exe"
        return "controlled-codex 1.0"

    monkeypatch.setattr(doctor, "_resolve_executable", fake_resolve)
    monkeypatch.setattr(doctor, "_version", fake_version)
    runner = RecordingGroundingRunner()

    report = doctor.inspect_environment(config, grounding_runner=runner)

    raw_checks = report["checks"]
    assert isinstance(raw_checks, list)
    checks = {
        str(_object(raw)["name"]): _object(raw)
        for raw in cast(list[object], raw_checks)
    }
    assert checks["model:disabled"]["status"] == "disabled"
    assert checks["model:self"]["status"] == "context-required"
    assert checks["model:codex"] == {
        "name": "model:codex",
        "status": "available",
        "detail": "controlled-codex 1.0",
    }
    assert checks["model:claude"]["status"] == "missing"
    assert checks["model:api-ready"]["status"] == "available"
    assert checks["model:api-missing"]["status"] == "credential-missing"
    assert checks["model:unsupported"]["status"] == "unsupported"
    assert checks["grounding-network-isolation"]["detail"] == "RecordingGroundingRunner"
    assert checks["proof-capsule-signing"]["status"] == "credential-missing"
    assert report["blocking"] == ["model:claude", "model:unsupported"]
    assert report["healthy"] is False
    assert secret not in json.dumps(report, sort_keys=True)


def test_doctor_cli_emits_injected_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = RecordingGroundingRunner()
    calls: list[tuple[FusionConfig, GroundingRunner | None]] = []

    def fake_detect() -> RecordingGroundingRunner:
        return runner

    def fake_inspect(
        config: FusionConfig,
        *,
        grounding_runner: GroundingRunner | None,
        proof_runner: ProofRunner | None = None,
    ) -> JsonObject:
        assert proof_runner is None
        calls.append((config, grounding_runner))
        return {"healthy": True, "blocking": [], "checks": []}

    monkeypatch.setattr(cli, "detect_grounding_runner", fake_detect)
    monkeypatch.setattr(cli, "inspect_environment", fake_inspect)

    status = cli.main(["doctor", "--repo", str(tmp_path)])

    assert status == 0
    assert _stdout_json(capsys) == {"blocking": [], "checks": [], "healthy": True}
    assert len(calls) == 1
    assert calls[0][1] is runner


def test_engine_construction_uses_injected_registry_work_root_and_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = FusionConfig(data={"models": {}}, source_paths=())
    registry = ProviderRegistry({})
    runner = RecordingGroundingRunner()

    def fake_config(_args: argparse.Namespace) -> FusionConfig:
        return config

    def fake_registry_from_config(candidate: FusionConfig) -> ProviderRegistry:
        assert candidate is config
        return registry

    def fake_detect() -> RecordingGroundingRunner:
        return runner

    monkeypatch.setattr(cli, "_config", fake_config)
    monkeypatch.setattr(
        ProviderRegistry,
        "from_config",
        staticmethod(fake_registry_from_config),
    )
    monkeypatch.setattr(cli, "detect_grounding_runner", fake_detect)
    work_root = tmp_path / "work"

    engine = cli._engine(argparse.Namespace(work_root=str(work_root)))

    assert engine.config is config
    assert engine.registry is registry
    assert engine.work_root == work_root.resolve()
    assert engine.grounding_runner is runner


def test_registry_builds_every_supported_transport_without_invocation() -> None:
    config = FusionConfig(
        data={
            "models": {
                "self": {"transport": "self", "callable": False},
                "codex": {
                    "transport": "codex-exec",
                    "params": {"executable": "never-run-codex"},
                },
                "claude": {
                    "transport": "claude-exec",
                    "params": {"executable": "never-run-claude"},
                },
                "openai": {
                    "transport": "openai-compatible",
                    "base_url": "https://controlled.invalid/v1",
                    "key_env": "AUTOFUSION_NEVER_SET_OPENAI",
                },
                "anthropic": {
                    "transport": "anthropic-http",
                    "base_url": "https://controlled.invalid",
                    "key_env": "AUTOFUSION_NEVER_SET_ANTHROPIC",
                },
            }
        },
        source_paths=(),
    )

    registry = ProviderRegistry.from_config(config)

    assert set(registry.providers) == {"self", "codex", "claude", "openai", "anthropic"}
    assert isinstance(registry.get("self"), SelfProvider)
    assert isinstance(registry.get("codex"), CliTransportProvider)
    assert isinstance(registry.get("claude"), CliTransportProvider)
    assert isinstance(registry.get("openai"), OpenAICompatibleHttpProvider)
    assert isinstance(registry.get("anthropic"), AnthropicHttpProvider)


def test_registry_uses_platform_defaults_for_cli_transport_executables() -> None:
    config = FusionConfig(
        data={
            "models": {
                "codex": {"transport": "codex-exec"},
                "claude": {"transport": "claude-exec"},
            }
        },
        source_paths=(),
    )

    registry = ProviderRegistry.from_config(config)
    codex = registry.get("codex")
    claude = registry.get("claude")

    assert isinstance(codex, CliTransportProvider)
    assert isinstance(claude, CliTransportProvider)
    assert isinstance(codex.adapter, CodexExecAdapter)
    assert isinstance(claude.adapter, ClaudeCliAdapter)
    expected_codex = "codex.cmd" if os.name == "nt" else "codex"
    expected_claude = (
        "claude.exe" if os.name == "nt" and shutil.which("claude.exe") else "claude"
    )
    if os.name == "nt" and expected_claude == "claude":
        expected_claude = "claude.cmd"
    assert codex.adapter.executable == expected_codex
    assert claude.adapter.executable == expected_claude


def test_registry_rejects_unknown_handle_and_invalid_model_definitions() -> None:
    with pytest.raises(ProviderError, match="unknown provider handle"):
        ProviderRegistry({}).get("missing")

    unsupported = FusionConfig(
        data={"models": {"bad": {"transport": "unsupported"}}},
        source_paths=(),
    )
    with pytest.raises(ConfigurationError, match="unsupported provider transport"):
        ProviderRegistry.from_config(unsupported)

    malformed = FusionConfig(data={"models": {"bad": []}}, source_paths=())
    with pytest.raises(ConfigurationError, match="must be a JSON object"):
        ProviderRegistry.from_config(malformed)

    missing_url = FusionConfig(
        data={
            "models": {
                "bad": {
                    "transport": "openai-compatible",
                    "key_env": "AUTOFUSION_NEVER_SET",
                }
            }
        },
        source_paths=(),
    )
    with pytest.raises(ConfigurationError, match="base_url must be a non-empty string"):
        ProviderRegistry.from_config(missing_url)


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [(CallStatus.COMPLETED, 0), (CallStatus.FAILED, 3)],
)
def test_call_dispatches_through_registry_to_controlled_provider(
    status: CallStatus,
    expected_exit: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = RecordingProvider(load_config().model("gpt-sol"), status=status)
    snapshot_calls = _install_call_fakes(monkeypatch, tmp_path, provider)
    schema_path = tmp_path / "schema.json"
    schema_path.write_text('{"type":"object"}', encoding="utf-8")
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("controlled prompt", encoding="utf-8")
    work_root = tmp_path / "work"
    secret = "unused-secret-must-not-appear"
    monkeypatch.setenv("AUTOFUSION_TEST_UNUSED_SECRET", secret)

    exit_code = cli.main(
        [
            "call",
            "gpt-sol",
            "--repo",
            str(tmp_path),
            "--prompt-file",
            str(prompt_path),
            "--schema",
            str(schema_path),
            "--timeout",
            "12.5",
            "--max-output-chars",
            "321",
            "--work-root",
            str(work_root),
        ]
    )

    assert exit_code == expected_exit
    captured = capsys.readouterr()
    payload = _json_object(captured.out)
    assert payload["status"] == status.value
    assert payload["handle"] == "gpt-sol"
    assert payload["effective_model"] == (
        provider.profile.model if status is CallStatus.COMPLETED else None
    )
    assert "stderr_tail" in payload
    assert "truncated" in payload
    assert secret not in captured.out
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.prompt == "controlled prompt"
    assert request.response_schema == {"type": "object"}
    assert request.working_directory == tmp_path / f"snapshot-{status.value}"
    assert request.timeout_s == 12.5
    assert request.max_output_chars == 321
    assert request.metadata == {"packet_hash": "0" * 64}
    assert request.run_id.startswith("call-")
    assert request.call_id.startswith("call-")
    assert snapshot_calls[0][0] == tmp_path.resolve()
    assert snapshot_calls[0][1].parent.parent == work_root.resolve()


def test_call_missing_prompt_fails_before_provider_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = RecordingProvider(_profile())
    _install_call_fakes(monkeypatch, tmp_path, provider)
    schema_path = tmp_path / "schema.json"
    schema_path.write_text('{"type":"object"}', encoding="utf-8")
    caplog.set_level(logging.ERROR, logger="autofusion")

    exit_code = cli.main(
        [
            "call",
            "gpt-sol",
            "--repo",
            str(tmp_path),
            "--schema",
            str(schema_path),
        ]
    )

    assert exit_code == 2
    assert capsys.readouterr().out == ""
    assert provider.requests == []
    assert "one of --prompt or --prompt-file is required" in caplog.text


def test_ground_uses_only_controlled_network_denied_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = RecordingGroundingRunner(
        output=RunnerOutput(
            exit_code=1,
            stdout="AssertionError: controlled failure",
            failure_class="assertion",
        )
    )
    snapshot_root = tmp_path / "controlled-snapshot"
    snapshot_root.mkdir()
    snapshot_calls: list[tuple[Path, Path]] = []
    command = VerificationCommand(
        verification_id="python.controlled",
        argv=("python", "-m", "pytest", "controlled.py"),
        timeout_s=17,
        kind="test",
        expected_failure=("AssertionError",),
    )

    def fake_detect() -> RecordingGroundingRunner:
        return runner

    def fake_build_snapshot(source_root: Path, destination_root: Path) -> SnapshotManifest:
        snapshot_calls.append((source_root, destination_root))
        return SnapshotManifest(
            root=snapshot_root,
            snapshot_hash="1" * 64,
            manifest_hash="2" * 64,
            entries=(),
            source_root=source_root,
        )

    def fake_resolve(_config: FusionConfig, verification_id: str) -> VerificationCommand:
        assert verification_id == command.verification_id
        return command

    monkeypatch.setattr(cli, "detect_grounding_runner", fake_detect)
    monkeypatch.setattr(cli, "build_snapshot", fake_build_snapshot)
    monkeypatch.setattr(cli, "resolve_verification", fake_resolve)
    work_root = tmp_path / "work"

    exit_code = cli.main(
        [
            "ground",
            command.verification_id,
            "--repo",
            str(tmp_path),
            "--max-output-chars",
            "128",
            "--work-root",
            str(work_root),
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    payload = _json_object(captured.out)
    assert payload["verdict"] == "confirmed"
    assert payload["execution_status"] == "completed"
    assert payload["matched_expected_failure"] is True
    assert "controlled failure" not in captured.out
    assert runner.calls == [(command.argv, snapshot_root.resolve(), 17)]
    assert snapshot_calls[0][0] == tmp_path.resolve()
    assert snapshot_calls[0][1].parent.parent == work_root.resolve()


def test_ground_fails_closed_before_snapshot_without_verified_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    snapshot_called = False

    def no_runner() -> None:
        return None

    def forbidden_snapshot(_source_root: Path, _destination_root: Path) -> SnapshotManifest:
        nonlocal snapshot_called
        snapshot_called = True
        raise AssertionError("snapshot must not run without a verified runner")

    monkeypatch.setattr(cli, "detect_grounding_runner", no_runner)
    monkeypatch.setattr(cli, "build_snapshot", forbidden_snapshot)
    caplog.set_level(logging.ERROR, logger="autofusion")

    exit_code = cli.main(
        ["ground", "python.controlled", "--repo", str(tmp_path)]
    )

    assert exit_code == 2
    assert capsys.readouterr().out == ""
    assert not snapshot_called
    assert "no verified network-denied grounding runner" in caplog.text


def test_receipt_verify_accepts_empty_chain(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["receipt-verify", "--repo", str(tmp_path)])

    assert exit_code == 0
    assert _stdout_json(capsys) == {"count": 0, "runs": [], "valid": True}


def test_receipt_verify_rejects_tampered_receipt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    receipt_path = tmp_path / ".fusion" / "runs" / "run-1" / "receipt.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text('{"run_id":"run-1","receipt_hash":"tampered"}', encoding="utf-8")
    caplog.set_level(logging.ERROR, logger="autofusion")

    exit_code = cli.main(["receipt-verify", "--repo", str(tmp_path)])

    assert exit_code == 2
    assert capsys.readouterr().out == ""
    assert "receipt digest mismatch" in caplog.text


def test_policy_sign_and_verify_round_trip_without_emitting_key_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle_path = tmp_path / "bundle.json"
    signature_path = tmp_path / "signature.json"
    bundle_path.write_text('{"policy":"controlled","version":1}', encoding="utf-8")
    key_env = "AUTOFUSION_TEST_POLICY_KEY"
    signing_key = "controlled-signing-key-material-1234567890"
    monkeypatch.setenv(key_env, signing_key)

    sign_exit = cli.main(
        [
            "policy-sign",
            "--bundle",
            str(bundle_path),
            "--key-id",
            "test-key-1",
            "--key-env",
            key_env,
            "--output",
            str(signature_path),
        ]
    )

    assert sign_exit == 0
    signed_output = capsys.readouterr().out
    signature = _json_object(signed_output)
    assert signature["key_id"] == "test-key-1"
    assert signature["algorithm"] == "hmac-sha256"
    assert read_json_object(signature_path) == signature
    assert signing_key not in signed_output

    verify_exit = cli.main(
        [
            "policy-verify",
            "--bundle",
            str(bundle_path),
            "--signature",
            str(signature_path),
            "--key-env",
            key_env,
        ]
    )

    assert verify_exit == 0
    verified_output = capsys.readouterr().out
    verified = _json_object(verified_output)
    assert verified == {
        "bundle_hash": signature["bundle_hash"],
        "key_id": "test-key-1",
        "valid": True,
    }
    assert signing_key not in verified_output


def test_policy_commands_fail_closed_for_missing_or_revoked_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    bundle_path = tmp_path / "bundle.json"
    signature_path = tmp_path / "signature.json"
    bundle_path.write_text('{"policy":"controlled"}', encoding="utf-8")
    key_env = "AUTOFUSION_TEST_POLICY_KEY"
    signing_key = "controlled-signing-key-material-1234567890"
    monkeypatch.setenv(key_env, signing_key)
    assert (
        cli.main(
            [
                "policy-sign",
                "--bundle",
                str(bundle_path),
                "--key-id",
                "revoked-key",
                "--key-env",
                key_env,
                "--output",
                str(signature_path),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        cli.main(
            [
                "policy-sign",
                "--bundle",
                str(bundle_path),
                "--key-id",
                "stdout-only-key",
                "--key-env",
                key_env,
            ]
        )
        == 0
    )
    stdout_only_signature = capsys.readouterr().out
    assert _json_object(stdout_only_signature)["key_id"] == "stdout-only-key"
    assert signing_key not in stdout_only_signature
    caplog.set_level(logging.ERROR, logger="autofusion")

    revoked_exit = cli.main(
        [
            "policy-verify",
            "--bundle",
            str(bundle_path),
            "--signature",
            str(signature_path),
            "--key-env",
            key_env,
            "--revoked-key-id",
            "revoked-key",
        ]
    )
    assert revoked_exit == 2
    assert capsys.readouterr().out == ""
    assert "policy signature key is revoked" in caplog.text
    assert signing_key not in caplog.text

    caplog.clear()
    monkeypatch.delenv(key_env)
    missing_verify_exit = cli.main(
        [
            "policy-verify",
            "--bundle",
            str(bundle_path),
            "--signature",
            str(signature_path),
            "--key-env",
            key_env,
        ]
    )
    assert missing_verify_exit == 2
    assert capsys.readouterr().out == ""
    assert "policy verification key variable is not set" in caplog.text

    caplog.clear()
    missing_exit = cli.main(
        [
            "policy-sign",
            "--bundle",
            str(bundle_path),
            "--key-id",
            "new-key",
            "--key-env",
            key_env,
        ]
    )
    assert missing_exit == 2
    assert capsys.readouterr().out == ""
    assert "policy signing key variable is not set" in caplog.text


def test_eval_summarize_emits_metrics_for_each_observed_arm(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    input_path = tmp_path / "evaluation.json"
    input_path.write_text(
        json.dumps([_observation(passed=True), _observation(passed=False, second=True)]),
        encoding="utf-8",
    )

    exit_code = cli.main(["eval-summarize", "--input", str(input_path), "-k", "1"])

    assert exit_code == 0
    metrics = _object(_stdout_json(capsys)["solo"])
    assert metrics == {
        "verified_resolution": 0.5,
        "precision": 0.5,
        "critical_misses": 1,
        "regressions": 1,
        "latency_ms": 150.0,
        "cost_usd": 0.75,
        "pass_at_k": 0.5,
        "pass_to_k": 0.5,
        "unique_confirmed_gain": 1,
        "samples": 2,
    }


@pytest.mark.parametrize(
    ("payload", "k", "message"),
    [
        ({"not": "an-array"}, 1, "evaluation input must be an array"),
        (["not-an-object"], 1, "evaluation observation must be an object"),
        ([_observation(passed=True)], 2, "each task requires at least k trials"),
    ],
)
def test_eval_summarize_rejects_invalid_inputs(
    payload: object,
    k: int,
    message: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    input_path = tmp_path / "invalid-evaluation.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    caplog.set_level(logging.ERROR, logger="autofusion")

    exit_code = cli.main(
        ["eval-summarize", "--input", str(input_path), "-k", str(k)]
    )

    assert exit_code == 2
    assert capsys.readouterr().out == ""
    assert message in caplog.text


def test_eval_summarize_rejects_malformed_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    input_path = tmp_path / "malformed.json"
    input_path.write_text("{", encoding="utf-8")
    caplog.set_level(logging.ERROR, logger="autofusion")

    exit_code = cli.main(["eval-summarize", "--input", str(input_path)])

    assert exit_code == 2
    assert capsys.readouterr().out == ""
    assert "Expecting property name" in caplog.text


def test_task_reads_file_preferentially_and_requires_a_value(tmp_path: Path) -> None:
    task_path = tmp_path / "task.txt"
    task_path.write_text("task from file", encoding="utf-8")

    assert cli._task(argparse.Namespace(task="inline", task_file=str(task_path))) == (
        "task from file"
    )
    assert cli._task(argparse.Namespace(task="inline", task_file=None)) == "inline"
    with pytest.raises(ValueError, match="one of --task or --task-file is required"):
        cli._task(argparse.Namespace(task=None, task_file=None))


def test_run_emits_pending_state_and_does_not_emit_session_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pending = PendingRun(
        run_id="run-pending",
        state="analyzed",
        analysis_path=tmp_path / "analysis.json",
        pending_path=tmp_path / "pending.json",
        evidence_path=tmp_path / "evidence.json",
        journal_path=tmp_path / "journal.jsonl",
        result_path=tmp_path / "result.json",
        requires_reconciliation=True,
        fused=False,
        degraded=False,
    )
    engine = RecordingRunEngine(pending)
    fingerprint_env = "AUTOFUSION_TEST_SESSION_FINGERPRINT"
    fingerprint = "controlled-session-fingerprint"
    monkeypatch.setenv(fingerprint_env, fingerprint)

    def fake_engine(_args: argparse.Namespace) -> RecordingRunEngine:
        return engine

    monkeypatch.setattr(cli, "_engine", fake_engine)

    exit_code = cli.main(
        [
            "run",
            "--repo",
            str(tmp_path),
            "--task",
            "review controlled diff",
            "--kind",
            "diff",
            "--path",
            "src/one.py",
            "--path",
            "src/two.py",
            "--external-only",
            "--self-model",
            "self-model",
            "--self-session-fingerprint-env",
            fingerprint_env,
            "--focus",
            "security",
            "--no-grounding",
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    payload = _json_object(captured.out)
    assert payload["run_id"] == "run-pending"
    assert payload["requires_reconciliation"] is True
    assert fingerprint not in captured.out
    assert len(engine.requests) == 1
    request = engine.requests[0]
    assert request.repo_root == tmp_path
    assert request.artifact_paths == ("src/one.py", "src/two.py")
    assert request.external_only
    assert request.self_model == "self-model"
    assert request.self_session_fingerprint == fingerprint
    assert request.focus_role == "security"
    assert not request.run_grounding


def test_run_emits_completed_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = RecordingRunEngine(_artifacts(tmp_path))

    def fake_engine(_args: argparse.Namespace) -> RecordingRunEngine:
        return engine

    monkeypatch.setattr(cli, "_engine", fake_engine)

    exit_code = cli.main(
        [
            "run",
            "--repo",
            str(tmp_path),
            "--task",
            "controlled task",
            "--kind",
            "answer",
        ]
    )

    assert exit_code == 0
    payload = _stdout_json(capsys)
    assert payload["state"] == "signed_off"
    assert payload["verdict"] == "ship"
    assert payload["fused"] is True


def test_run_missing_session_fingerprint_fails_before_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = RecordingRunEngine(_artifacts(tmp_path))
    fingerprint_env = "AUTOFUSION_TEST_MISSING_FINGERPRINT"
    monkeypatch.delenv(fingerprint_env, raising=False)

    def fake_engine(_args: argparse.Namespace) -> RecordingRunEngine:
        return engine

    monkeypatch.setattr(cli, "_engine", fake_engine)
    caplog.set_level(logging.ERROR, logger="autofusion")

    exit_code = cli.main(
        [
            "run",
            "--repo",
            str(tmp_path),
            "--task",
            "controlled task",
            "--kind",
            "plan",
            "--self-session-fingerprint-env",
            fingerprint_env,
        ]
    )

    assert exit_code == 2
    assert capsys.readouterr().out == ""
    assert engine.requests == []
    assert "self session fingerprint variable is not set" in caplog.text


def test_finalize_parses_dispositions_and_emits_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    disposition_path = tmp_path / "dispositions.json"
    disposition_path.write_text(
        json.dumps(
            {
                "dispositions": [
                    {
                        "finding_id": "finding-1",
                        "disposition": "accepted",
                        "rationale": "controlled rationale",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    engine = RecordingFinalizeEngine(_artifacts(tmp_path, "run-final"))

    def fake_engine(_args: argparse.Namespace) -> RecordingFinalizeEngine:
        return engine

    monkeypatch.setattr(cli, "_engine", fake_engine)

    exit_code = cli.main(
        [
            "finalize",
            "--repo",
            str(tmp_path),
            "run-final",
            "--dispositions",
            str(disposition_path),
            "--automatic",
        ]
    )

    assert exit_code == 0
    assert _stdout_json(capsys)["run_id"] == "run-final"
    assert engine.calls == [
        (
            "run-final",
            (
                FindingDisposition(
                    finding_id="finding-1",
                    disposition="accepted",
                    rationale="controlled rationale",
                ),
            ),
            True,
        )
    ]


@pytest.mark.parametrize(
    "payload",
    [{"dispositions": "not-an-array"}, ["not-an-object"]],
)
def test_dispositions_reject_malformed_payloads(tmp_path: Path, payload: object) -> None:
    path = tmp_path / "bad-dispositions.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        cli._dispositions(str(path))


def test_dispositions_allow_omitted_file() -> None:
    assert cli._dispositions(None) is None


@pytest.mark.parametrize("argv", [[], ["unknown-command"]])
def test_parser_errors_raise_system_exit_and_print_usage(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(argv)

    assert raised.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage: autofusion" in captured.err


def test_main_rejects_non_callable_handler(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    parser = argparse.ArgumentParser()

    def parse_args(_argv: list[str] | None = None) -> argparse.Namespace:
        return argparse.Namespace(handler=None)

    def fake_build_parser() -> argparse.ArgumentParser:
        return parser

    monkeypatch.setattr(parser, "parse_args", parse_args)
    monkeypatch.setattr(cli, "build_parser", fake_build_parser)
    caplog.set_level(logging.ERROR, logger="autofusion")

    assert cli.main(["ignored"]) == 2
    assert "CLI handler is unavailable" in caplog.text
