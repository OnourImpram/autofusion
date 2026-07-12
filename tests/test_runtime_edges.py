from __future__ import annotations

import asyncio
import io
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError

import pytest

from autofusion.budget import BudgetLedger
from autofusion.config import load_config
from autofusion.credentials import CredentialBroker
from autofusion.errors import BudgetExceeded, GroundingError, PolicyError, ProviderError
from autofusion.github_annotations import format_github_annotation
from autofusion.grounding import GroundingResult
from autofusion.models import CallStatus, ProviderRequest, ProviderResult, RunBudget
from autofusion.providers.http import (
    AnthropicHttpProvider,
    HttpResponse,
    OpenAICompatibleHttpProvider,
    UrllibHttpClient,
)
from autofusion.providers.process import CommandOutcome, CommandRunner
from autofusion.reconcile import (
    FindingDisposition,
    GroundingLink,
    attach_grounding,
    automatic_external_dispositions,
    reconcile_analysis,
)
from autofusion.router import AdaptiveSignals, is_route_at_least, resolve_adaptive_route
from autofusion.sandbox import LinuxUnshareGroundingRunner, WslUnshareGroundingRunner
from autofusion.topologies import run_advisor, run_dual_review, run_panel_rank, run_review


def _request(handle: str = "reviewer", call_id: str = "call-1") -> ProviderRequest:
    return ProviderRequest(
        run_id="run-1",
        call_id=call_id,
        handle=handle,
        prompt="review",
        response_schema={"type": "object"},
        working_directory=Path.cwd(),
        timeout_s=1.0,
        max_output_chars=128,
        metadata={"packet_hash": "a" * 64},
    )


def _result(
    request: ProviderRequest,
    *,
    status: CallStatus = CallStatus.COMPLETED,
    output: dict[str, object] | None = None,
) -> ProviderResult:
    return ProviderResult(
        call_id=request.call_id,
        handle=request.handle,
        requested_model=request.handle,
        effective_model=request.handle,
        vendor="test",
        family=request.handle,
        mode="test",
        compound=False,
        worker_visibility="transparent",
        status=status,
        duration_ms=1,
        output_text=json.dumps(output or {}),
        structured_output=output or {},
        output_hash="b" * 64,
    )


class Dispatcher:
    def __init__(self, results: dict[str, ProviderResult] | None = None) -> None:
        self.results = results or {}
        self.calls: list[ProviderRequest] = []

    async def dispatch(self, request: ProviderRequest) -> ProviderResult:
        self.calls.append(request)
        if request.call_id in self.results:
            return self.results[request.call_id]
        return _result(request)


class HttpClient:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout_s: float,
        max_bytes: int,
    ) -> HttpResponse:
        del url, headers, body, timeout_s, max_bytes
        return self.response


class BrokenProcess:
    stdin = None
    stdout = None
    stderr = None
    pid = 1234
    returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = -9
        return -9

    def kill(self) -> None:
        self.returncode = -9


class FakeCompleted:
    def __init__(self, returncode: int, stdout: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout


class Recorder:
    def __init__(self) -> None:
        self.argv: tuple[str, ...] | None = None

    def run(
        self,
        argv: Sequence[str],
        *,
        input_text: str,
        cwd: Path,
        timeout_s: float,
        max_output_chars: int,
        environment_allowlist: Sequence[str],
    ) -> CommandOutcome:
        del input_text, cwd, timeout_s, max_output_chars, environment_allowlist
        self.argv = tuple(argv)
        return CommandOutcome(tuple(argv), 0, "ok", "", 1, False, False)


def test_http_client_rejects_unsafe_endpoint_before_network() -> None:
    with pytest.raises(ProviderError, match="HTTPS"):
        UrllibHttpClient().post(
            "http://provider.example/v1",
            headers={},
            body=b"{}",
            timeout_s=1,
            max_bytes=100,
        )
    with pytest.raises(ProviderError, match="authority"):
        UrllibHttpClient().post(
            "https://user:pass@provider.example/v1",
            headers={},
            body=b"{}",
            timeout_s=1,
            max_bytes=100,
        )


def test_http_client_bounds_http_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    class Opener:
        def open(self, request: object, timeout: float) -> object:
            del request, timeout
            raise HTTPError(
                "https://provider.example",
                500,
                "bad",
                cast(Any, {}),
                io.BytesIO(b"x" * 20),
            )

    monkeypatch.setattr("autofusion.providers.http.build_opener", lambda _: Opener())
    with pytest.raises(ProviderError, match="error response exceeded"):
        UrllibHttpClient().post(
            "https://provider.example/v1",
            headers={},
            body=b"{}",
            timeout_s=1,
            max_bytes=2,
        )


def test_http_client_wraps_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class Opener:
        def open(self, request: object, timeout: float) -> object:
            del request, timeout
            raise URLError("dns failed")

    monkeypatch.setattr("autofusion.providers.http.build_opener", lambda _: Opener())
    with pytest.raises(ProviderError, match="transport failed"):
        UrllibHttpClient().post(
            "https://provider.example/v1",
            headers={},
            body=b"{}",
            timeout_s=1,
            max_bytes=100,
        )


def test_http_providers_fail_closed_on_missing_credentials(tmp_path: Path) -> None:
    request = _request()
    profile = load_config().model("gpt-sol")
    provider = OpenAICompatibleHttpProvider(
        profile,
        "https://provider.example",
        "AUTOFUSION_MISSING_KEY",
        HttpClient(HttpResponse(200, b"{}")),
    )
    with pytest.raises(ProviderError, match="credential is missing"):
        provider.invoke(request)

    claude = load_config().model("claude-opus")
    anthropic = AnthropicHttpProvider(
        claude,
        "https://provider.example",
        "AUTOFUSION_MISSING_KEY",
        HttpClient(HttpResponse(200, b"{}")),
    )
    with pytest.raises(ProviderError, match="credential is missing"):
        anthropic.invoke(request)


def test_anthropic_rejects_invalid_max_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_path
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "redacted")
    profile = load_config(
        overrides={"models": {"claude-opus": {"params": {"max_tokens": 0}}}}
    ).model("claude-opus")
    provider = AnthropicHttpProvider(
        profile,
        "https://provider.example",
        "TEST_ANTHROPIC_KEY",
        HttpClient(HttpResponse(200, b"{}")),
    )
    with pytest.raises(ProviderError, match="max_tokens"):
        provider.invoke(_request())


def test_command_runner_kills_process_without_required_streams(tmp_path: Path) -> None:
    killed: list[int] = []

    def popen(args: Sequence[str], **kwargs: Any) -> BrokenProcess:
        del args, kwargs
        return BrokenProcess()

    def kill(process: Any) -> None:
        killed.append(process.pid)

    runner = CommandRunner(popen_factory=cast(Any, popen), kill_tree=kill)
    with pytest.raises(ProviderError, match="standard streams"):
        runner.run(
            ("provider",),
            input_text="",
            cwd=tmp_path,
            timeout_s=1.0,
            max_output_chars=10,
            environment_allowlist=(),
        )
    assert killed == [1234]


def test_sandbox_runners_translate_and_wrap_network_isolation_commands(
    tmp_path: Path,
) -> None:
    linux_recorder = Recorder()
    linux = LinuxUnshareGroundingRunner(linux_recorder, executable="unshare")
    assert linux.run(("python", "-m", "pytest"), tmp_path, 5).stdout == "ok"
    assert linux_recorder.argv == ("unshare", "-Urn", "--", "python", "-m", "pytest")

    wsl_recorder = Recorder()
    wsl = WslUnshareGroundingRunner(
        wsl_recorder,
        distro="Ubuntu",
        executable="wsl.exe",
        path_translator=lambda path: "/mnt/c/repo",
    )
    assert wsl.run(("pytest", "-q"), tmp_path, 5).exit_code == 0
    assert wsl_recorder.argv == (
        "wsl.exe",
        "-d",
        "Ubuntu",
        "--cd",
        "/mnt/c/repo",
        "--",
        "unshare",
        "-Urn",
        "--",
        "pytest",
        "-q",
    )


def test_wsl_translate_rejects_invalid_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "autofusion.sandbox.subprocess.run",
        lambda *args, **kwargs: FakeCompleted(0, b"not-absolute\n"),
    )
    runner = WslUnshareGroundingRunner(Recorder(), executable="wsl.exe")
    with pytest.raises(GroundingError, match="invalid snapshot path"):
        runner._translate(tmp_path)

    def timeout(*args: object, **kwargs: object) -> FakeCompleted:
        del args, kwargs
        raise subprocess.TimeoutExpired("wslpath", 1)

    monkeypatch.setattr("autofusion.sandbox.subprocess.run", timeout)
    with pytest.raises(GroundingError, match="unable to translate"):
        runner._translate(tmp_path)


def test_reconcile_blocks_rejecting_grounded_findings_and_handles_deadlocks() -> None:
    analysis = {
        "context_complete": True,
        "findings": [
            {
                "id": "f01",
                "severity": "blocker",
                "status": "open",
                "evidence": {"strength": "model"},
            }
        ],
        "grounding_results": [],
    }
    result = GroundingResult(
        verification_id="pytest",
        verdict="confirmed",
        execution_status="completed",
        exit_code=1,
        failure_class="assertion",
        matched_expected_failure=True,
        output="",
        output_truncated=False,
        output_hash="c" * 64,
        invocation_hash="d" * 64,
    )
    grounded = attach_grounding(analysis, (GroundingLink("f01", result, "e" * 64),))
    with pytest.raises(PolicyError, match="execution-confirmed"):
        reconcile_analysis(
            grounded,
            (FindingDisposition("f01", "rejected", "model disagreement"),),
        )
    reconciled = reconcile_analysis(
        grounded,
        (FindingDisposition("f01", "accepted", "execution confirmed it"),),
    )
    assert reconciled["decision_impact"]["effect"] == "blocked"

    ungrounded = {
        "context_complete": True,
        "findings": [{"id": "f02", "severity": "major", "status": "open", "evidence": {}}],
    }
    automatic = automatic_external_dispositions(ungrounded)
    assert automatic[0].disposition == "deadlock"
    assert (
        reconcile_analysis(ungrounded, automatic)["decision_impact"]["effect"]
        == "human-required"
    )


def test_topologies_convert_budget_and_identity_failures_to_degradation() -> None:
    request = _request()
    exhausted = BudgetLedger(RunBudget(0, 10, None, 128))
    outcome = asyncio.run(run_review(Dispatcher(), request, budget=exhausted))
    assert outcome.degraded
    assert outcome.results[0].status is CallStatus.FAILED
    assert "call budget" in str(outcome.results[0].error)

    mismatch = replace(_result(request), handle="other")
    dual = asyncio.run(
        run_dual_review(
            Dispatcher({"call-1": mismatch, "call-2": _result(_request("two", "call-2"))}),
            (request, _request("two", "call-2")),
            budget=BudgetLedger(RunBudget(2, 10, None, 128)),
        )
    )
    assert dual.degraded

    advisor = asyncio.run(
        run_advisor(
            Dispatcher({"call-1": _result(request, status=CallStatus.FAILED)}),
            request,
            budget=BudgetLedger(RunBudget(1, 10, None, 128)),
        )
    )
    assert advisor.degraded
    assert not advisor.fused


def test_panel_rank_degrades_for_incomplete_or_tied_comparisons() -> None:
    first = _request("a", "p1")
    second = _request("b", "p2")

    def pairwise(
        left_id: str,
        left: ProviderResult,
        right_id: str,
        right: ProviderResult,
    ) -> ProviderRequest:
        del left_id, left, right_id, right
        return _request("judge", "judge")

    failed = asyncio.run(
        run_panel_rank(
            Dispatcher({"p2": _result(second, status=CallStatus.FAILED)}),
            (first, second),
            pairwise_request=pairwise,
            budget=BudgetLedger(RunBudget(3, 10, None, 128)),
        )
    )
    assert failed.degraded
    assert failed.abstained


def test_router_parallel_is_ranked_as_quality_and_annotations_validate_paths() -> None:
    route = resolve_adaptive_route(
        load_config(),
        signals=AdaptiveSignals(artifact_paths=("app.py",), external_only=True),
        requested_preset="parallel",
    )
    assert is_route_at_least(route, "quality")
    with pytest.raises(ValueError, match="relative"):
        format_github_annotation("error", "bad", path="/absolute.py")
    with pytest.raises(ValueError, match="positive"):
        format_github_annotation("error", "bad", path="relative.py", start_line=0)
    with pytest.raises(ValueError, match="follow"):
        format_github_annotation(
            "error",
            "bad",
            path="relative.py",
            start_line=2,
            end_line=1,
        )
    assert (
        format_github_annotation(
            "notice",
            "message",
            title="ok",
            path="src/example.py",
            start_line=1,
            end_line=2,
        )
        == "::notice title=ok,file=src/example.py,line=1,endLine=2::message"
    )


def test_credential_broker_exposes_only_approved_variable_names() -> None:
    config = load_config()
    assert CredentialBroker(config).for_handle("openrouter-fusion-reference") == (
        "OPENROUTER_API_KEY",
    )
    broker = CredentialBroker(config, globally_allowed=frozenset({"OTHER_KEY"}))
    with pytest.raises(PolicyError, match="not globally approved"):
        broker.for_handle("openrouter-fusion-reference")
    with pytest.raises(PolicyError, match="cannot resolve"):
        CredentialBroker(config).for_handle("missing-model")

    invalid = load_config(overrides={"models": {"gpt-sol": {"key_env": "not-valid"}}})
    with pytest.raises(PolicyError, match="invalid credential"):
        CredentialBroker(invalid).for_handle("gpt-sol")


def test_budget_ledger_rejects_invalid_and_double_settle_paths() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        BudgetLedger(RunBudget(-1, 10, None, 10))
    ledger = BudgetLedger(RunBudget(1, 10, 1.0, 10))
    with pytest.raises(ValueError, match="requested output"):
        ledger.reserve_call(output_chars=-1)
    with pytest.raises(BudgetExceeded, match="per-call"):
        ledger.reserve_call(output_chars=11)
    with pytest.raises(ValueError, match="estimated cost"):
        ledger.reserve_call(output_chars=1, estimated_cost_usd=-0.1)
    with pytest.raises(BudgetExceeded, match="cost budget"):
        ledger.reserve_call(output_chars=1, estimated_cost_usd=2.0)

    reservation = ledger.reserve_call(output_chars=1, estimated_cost_usd=0.1)
    with pytest.raises(ValueError, match="actual cost"):
        ledger.settle_call(reservation, actual_cost_usd=-0.1)
    ledger.settle_call(reservation, actual_cost_usd=0.1)
    with pytest.raises(ValueError, match="already settled"):
        ledger.settle_call(reservation, actual_cost_usd=0.1)


def test_adaptive_router_signal_escalation_and_minimum_comparison() -> None:
    quality = resolve_adaptive_route(
        load_config(),
        signals=AdaptiveSignals(cross_module_scope=True),
        requested_preset="adaptive",
    )
    assert quality.selected_preset == "high"
    assert is_route_at_least(quality, "balanced")

    balanced = resolve_adaptive_route(
        load_config(),
        signals=AdaptiveSignals(low_verification_strength=True),
        requested_preset="adaptive",
    )
    assert balanced.selected_preset == "balanced"

    fast = resolve_adaptive_route(
        load_config(),
        signals=AdaptiveSignals(),
        requested_preset="adaptive",
    )
    assert fast.selected_preset == "fast"
