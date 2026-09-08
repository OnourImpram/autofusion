"""Portable transports preserve merged identity and snapshot admission contracts."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from test_acp import _provider as acp_provider
from test_acp import _request as acp_request
from test_admission import NeverProvider
from test_agy import profile as agy_profile
from test_agy import request as agy_request

from autofusion.config import FusionConfig, load_config
from autofusion.errors import PolicyError
from autofusion.models import CallStatus, ProviderRequest
from autofusion.providers.acp import AcpProvider
from autofusion.providers.agy import AgyProvider
from autofusion.registry import ProviderRegistry


@pytest.mark.parametrize("transport", ["agy", "acp"])
@pytest.mark.parametrize("status", [CallStatus.TIMEOUT, CallStatus.FAILED])
def test_portable_transport_failure_keeps_configured_identity_and_quota(
    tmp_path: Path, transport: str, status: CallStatus,
) -> None:
    if transport == "acp":
        scenario = "timeout:initialize" if status == CallStatus.TIMEOUT else "stop:refusal"
        adapter = cast(AcpProvider, acp_provider(tmp_path, scenario))
        provider: AcpProvider | AgyProvider = replace(
            adapter, profile=replace(adapter.profile, quota_group="test-portable-quota"),
        )
        request = acp_request(tmp_path)
    else:
        body = "time.sleep(10)" if status == CallStatus.TIMEOUT else "sys.exit(2)"
        peer = tmp_path / "peer.py"
        peer.write_text("import sys, time\nsys.stdin.read()\n" + body + "\n", encoding="utf-8")
        provider = AgyProvider(
            replace(agy_profile(), quota_group="test-portable-quota"),
            executable=sys.executable, arguments=(str(peer),),
        )
        request = agy_request(tmp_path)
    result = provider.invoke(replace(request, timeout_s=0.2 if status == CallStatus.TIMEOUT else 3))
    assert result.status == status
    assert result.effective_model is None
    assert result.configured_model == provider.profile.canonical_model
    assert result.observed_model is None
    assert result.identity_evidence == "unavailable"
    assert result.quota_group == "test-portable-quota"


@pytest.mark.parametrize("handle", ["gemini-flash", "grok"])
def test_packet_declaration_does_not_bypass_portable_snapshot_dlp(
    tmp_path: Path, handle: str,
) -> None:
    data = load_config(repo_root=tmp_path, global_path=tmp_path / "no-global.json").data
    data["models"][handle].update({"enabled": True, "context": "packet"})
    data["guardrails"]["model_allowlist"].append(handle)
    config = FusionConfig(data, ())
    provider = NeverProvider(config.model(handle))
    registry = ProviderRegistry({handle: provider}, config=config)
    (tmp_path / "selected.txt").write_text("token=syntheticcredential12345", encoding="utf-8")
    with pytest.raises(PolicyError, match="snapshot DLP"):
        registry.invoke(ProviderRequest(
            "run", "call", handle, "Review selected artifact", {}, tmp_path, 1, 100,
        ))
    assert provider.calls == 0
