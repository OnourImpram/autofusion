"""Merged run deadlines reach both portable transports and bound their children."""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_acp import _provider as acp_provider
from test_acp import _request as acp_request
from test_agy import invoke as invoke_agy
from test_agy import request as agy_request
from test_engine import _repo

from autofusion.config import FusionConfig, load_config
from autofusion.engine import FusionEngine, FusionRunRequest, PendingRun
from autofusion.errors import ProviderError
from autofusion.models import ModelProfile, ProviderRequest, ProviderResult
from autofusion.providers.acp import AcpProvider
from autofusion.providers.agy import AgyProvider
from autofusion.registry import ProviderRegistry


@pytest.mark.parametrize("transport", ["agy", "acp"])
@pytest.mark.parametrize(
    "deadline", [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_portable_transport_rejects_nonfinite_absolute_deadline_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transport: str, deadline: float,
) -> None:
    def unexpected_spawn(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid deadline reached process spawn")

    monkeypatch.setattr(subprocess, "Popen", unexpected_spawn)
    request = acp_request(tmp_path) if transport == "acp" else agy_request(tmp_path)
    request = replace(request, deadline_monotonic=deadline)
    with pytest.raises(ProviderError, match="execution deadline must be finite"):
        if transport == "acp":
            acp_provider(tmp_path).invoke(request)
        else:
            invoke_agy(tmp_path, "sys.stdin.read()\n", request)


@pytest.mark.parametrize("transport", ["agy", "acp"])
def test_portable_transport_stops_at_absolute_request_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transport: str,
) -> None:
    children: list[subprocess.Popen[bytes]] = []
    original_popen = subprocess.Popen

    def record_spawn(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        child: subprocess.Popen[bytes] = original_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", record_spawn)
    provider = acp_provider(tmp_path, "timeout:initialize") if transport == "acp" else None
    request = acp_request(tmp_path) if transport == "acp" else agy_request(tmp_path)
    started = time.monotonic()
    request = replace(request, timeout_s=1.5, deadline_monotonic=started + 0.2)
    if provider is None:
        result = invoke_agy(tmp_path, "sys.stdin.read()\ntime.sleep(10)\n", request)
    else:
        result = provider.invoke(request)
    elapsed = time.monotonic() - started
    assert result.status.value == "timeout"
    assert children and all(child.poll() is not None for child in children)
    assert elapsed < 1.0, f"{transport} reset the absolute deadline: {elapsed:.3f}s"


@pytest.mark.parametrize("handle", ["gemini-flash", "grok"])
def test_engine_supplies_remaining_run_allowance_to_portable_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, handle: str,
) -> None:
    from autofusion import engine as engine_module
    from autofusion.snapshot import build_snapshot as original_snapshot

    (tmp_path / ".git").mkdir()
    grok_home = tmp_path / "grok-home"
    grok_home.mkdir()
    data = load_config(repo_root=tmp_path, global_path=tmp_path / "no-global.json").data
    data["models"][handle]["enabled"] = True
    data["models"][handle]["capabilities"].update({
        "context_window_tokens": 200_000,
        "prompt_overhead_tokens": 10,
        "reserved_output_tokens": 10,
    })
    if handle == "grok":
        data["models"][handle]["params"] = {"grok_home": str(grok_home)}
    data["guardrails"]["model_allowlist"].append(handle)
    data["guardrails"]["max_wallclock_s"] = 1
    data["panels"]["portable-review"] = {
        "topology": "review", "drafter": "self", "reviewers": [handle],
    }
    config = FusionConfig(data, ())
    profile = config.model(handle)
    peer = tmp_path / "sleeping-peer.py"
    peer.write_text("import sys, time\nsys.stdin.read()\ntime.sleep(10)\n", encoding="utf-8")
    adapter = (
        AgyProvider(profile, executable=sys.executable, arguments=(str(peer),))
        if handle == "gemini-flash"
        else AcpProvider(
            profile, executable=sys.executable,
            arguments=(str(Path(__file__).with_name("acp_peer.py")),
                       "timeout:initialize", str(tmp_path / "peer.jsonl")),
        )
    )
    requests: list[ProviderRequest] = []
    results: list[ProviderResult] = []

    class RecordingTransport:
        profile: ModelProfile = adapter.profile

        def invoke(self, request: ProviderRequest) -> ProviderResult:
            requests.append(request)
            result = adapter.invoke(request)
            results.append(result)
            return result

    def slow_snapshot(*args: Any, **kwargs: Any) -> Any:
        time.sleep(0.25)
        return original_snapshot(*args, **kwargs)

    monkeypatch.setattr(engine_module, "build_snapshot", slow_snapshot)
    started = time.monotonic()
    artifacts = FusionEngine(
        config, ProviderRegistry({handle: RecordingTransport()}), work_root=tmp_path / "work",
    ).run(FusionRunRequest(
        task="Review under the shared execution deadline", repo_root=_repo(tmp_path),
        artifact_kind="plan", artifact_paths=("app.py",), panel="portable-review",
        self_model="claude-opus-5", run_grounding=False,
    ))
    assert time.monotonic() - started < 2.0
    assert len(requests) == len(results) == 1
    assert 0 < requests[0].timeout_s < 0.8
    assert requests[0].deadline_monotonic is not None
    assert started < requests[0].deadline_monotonic < started + 1.1
    assert results[0].status.value == "timeout"
    assert not isinstance(artifacts, PendingRun)
    assert artifacts.receipt["verdict"] == "degraded"
