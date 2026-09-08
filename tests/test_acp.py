"""Real subprocess peers exercise ACP lifecycle and the reviewer policy boundary."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import threading
import time
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from autofusion.errors import ProviderError
from autofusion.models import CallStatus, ModelProfile, ProviderRequest


def _profile() -> ModelProfile:
    return ModelProfile(
        handle="grok", transport="acp", callable=True, model="grok-test",
        canonical_model="grok-test", vendor="xai", family="grok", effort="xhigh",
        context="test",
    )


def _request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        run_id="acp-run", call_id="acp-call", handle="grok", prompt="review\ncode",
        response_schema={"type": "object", "required": ["answer"]},
        working_directory=tmp_path, timeout_s=5.0, max_output_chars=1024,
    )


def _provider(tmp_path: Path, scenario: str = "success", **kwargs: Any) -> Any:
    spec = importlib.util.find_spec("autofusion.providers.acp")
    assert spec is not None, "step 18 ACP transport is absent"
    module = importlib.import_module("autofusion.providers.acp")
    grok_home = tmp_path / "grok-home"
    grok_home.mkdir(exist_ok=True)
    (tmp_path / ".git").mkdir(exist_ok=True)
    return module.AcpProvider(
        replace(_profile(), params={"grok_home": str(grok_home)}), executable=sys.executable,
        arguments=(str(Path(__file__).with_name("acp_peer.py")), scenario,
                   str(tmp_path / "peer.jsonl")), **kwargs,
    )


def _messages(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (tmp_path / "peer.jsonl").read_text().splitlines()]


def test_acp_handshake_prompt_and_interleaved_fragmented_notifications(tmp_path: Path) -> None:
    result = _provider(tmp_path).invoke(_request(tmp_path))
    assert result.status == CallStatus.COMPLETED
    assert result.effective_model == "grok-test"
    assert result.structured_output == {"answer": "caf\u00e9"}
    assert result.input_tokens == 12
    assert result.output_tokens == 5
    assert result.cost_usd is None and not result.cost_verified
    sent = _messages(tmp_path)
    assert [m["method"] for m in sent if "method" in m] == [
        "initialize", "session/new", "session/prompt",
    ]
    initialize, session, prompt = sent[:3]
    assert initialize["params"]["protocolVersion"] == 1
    assert initialize["params"]["clientCapabilities"] == {
        "fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False,
    }
    assert session["params"]["cwd"] == str(tmp_path.resolve())
    assert session["params"]["mcpServers"] == []
    assert prompt["params"]["sessionId"] == "session-test"
    assert "review\ncode" in prompt["params"]["prompt"][0]["text"]
    assert "schema" in prompt["params"]["prompt"][0]["text"].lower()


@pytest.mark.parametrize("scenario", ["reject_once", "reject_always", "no_reject_option"])
def test_acp_denies_permission_even_when_peer_offers_allow(tmp_path: Path, scenario: str) -> None:
    result = _provider(tmp_path, scenario).invoke(_request(tmp_path))
    assert result.status == CallStatus.COMPLETED
    reply = next(m for m in _messages(tmp_path) if m.get("id") == "permission")
    expected = ({"outcome": "cancelled"} if scenario == "no_reject_option" else
                {"outcome": "selected", "optionId": "deny"})
    assert reply["result"]["outcome"] == expected
    assert not (tmp_path / "unapproved.txt").exists()


@pytest.mark.parametrize("method", [
    "fs/write_text_file", "terminal/create", "x.ai/terminal/create", "fs/read_text_file",
    "unrecognized/request",
])
def test_acp_unsolicited_client_requests_cannot_execute(tmp_path: Path, method: str) -> None:
    result = _provider(tmp_path, "request:" + method).invoke(_request(tmp_path))
    assert result.status == CallStatus.COMPLETED
    reply = next(m for m in _messages(tmp_path) if m.get("id") == "forbidden")
    assert reply["error"]["code"] == -32601
    assert not (tmp_path / "unapproved.txt").exists()


@pytest.mark.parametrize("scenario, message", [
    ("bad_json", "malformed"), ("bad_utf8", "UTF-8"), ("bad_rpc", "JSON-RPC"),
    ("array", "object"), ("duplicate_key", "duplicate"), ("wrong_id", "response ID"),
    ("both_result_error", "response"), ("invalid_error", "error"),
    ("bad_protocol", "protocol version"), ("bad_session", "session ID"),
    ("wrong_session", "session ID"), ("bad_content", "content"),
    ("missing_stop", "stop reason"), ("unknown_stop", "stop reason"),
    ("unfinished_frame", "incomplete"), ("model_missing", "identity"),
    ("model_mismatch", "identity mismatch"), ("model_update_mismatch", "identity mismatch"),
    ("output_limit", "output limit"), ("frame_limit", "message limit"),
    ("invalid_structured", "JSON"), ("schema_failure", "schema"),
    ("object_stop", "stop reason"), ("object_permission_kind", "permission"),
    ("bad_usage", "token count"), ("nonfinite_json", "JSON"),
    ("bad_params", "params"), ("null_id", "response ID"),
    ("model_update_no_category", "identity mismatch"),
    ("duplicate_permission_id", "duplicate permission"),
    ("huge_integer", "malformed"),
])
def test_acp_rejects_malformed_or_unattested_evidence(
    tmp_path: Path, scenario: str, message: str,
) -> None:
    with pytest.raises(ProviderError, match=message):
        _provider(tmp_path, scenario).invoke(_request(tmp_path))


@pytest.mark.parametrize("phase", ["initialize", "session/new", "session/prompt"])
def test_acp_eof_is_explicit_in_every_phase(tmp_path: Path, phase: str) -> None:
    with pytest.raises(ProviderError, match="EOF"):
        _provider(tmp_path, "eof:" + phase).invoke(_request(tmp_path))


@pytest.mark.parametrize("phase", ["initialize", "session/new", "session/prompt"])
@pytest.mark.parametrize("code, message", [(-32000, "authentication"), (-32601, "protocol")])
def test_acp_remote_errors_keep_auth_and_protocol_distinct(
    tmp_path: Path, phase: str, code: int, message: str,
) -> None:
    with pytest.raises(ProviderError, match=message):
        _provider(tmp_path, f"rpc:{phase}:{code}").invoke(_request(tmp_path))


@pytest.mark.parametrize("scenario", ["peer_cancelled", "rpc_cancelled"])
def test_acp_peer_cancellation_has_explicit_status(tmp_path: Path, scenario: str) -> None:
    result = _provider(tmp_path, scenario).invoke(_request(tmp_path))
    assert result.status == CallStatus.CANCELLED
    assert result.structured_output is None


@pytest.mark.parametrize("reason", ["max_tokens", "max_turn_requests", "refusal"])
def test_acp_incomplete_stop_is_not_success(tmp_path: Path, reason: str) -> None:
    result = _provider(tmp_path, "stop:" + reason).invoke(_request(tmp_path))
    assert result.status == CallStatus.FAILED
    assert reason in result.error
    assert result.structured_output is None


@pytest.mark.parametrize("phase", ["initialize", "session/new", "session/prompt"])
def test_acp_deadline_covers_every_lifecycle_phase(tmp_path: Path, phase: str) -> None:
    started = time.monotonic()
    result = _provider(tmp_path, "timeout:" + phase).invoke(
        replace(_request(tmp_path), timeout_s=1.0),
    )
    assert result.status == CallStatus.TIMEOUT
    assert time.monotonic() - started < 2.5
    if phase == "session/prompt":
        assert any(m.get("method") == "session/cancel" for m in _messages(tmp_path))


def test_acp_caller_cancel_sends_cancel_and_cancels_pending_permission(tmp_path: Path) -> None:
    event = threading.Event()
    stop = threading.Event()
    provider = _provider(tmp_path, "cancel_permission", cancel_event=event)

    def cancel_after_prompt() -> None:
        until = time.monotonic() + 4.0
        while not stop.is_set() and time.monotonic() < until:
            with suppress(FileNotFoundError, json.JSONDecodeError):
                if any(m.get("method") == "session/prompt" for m in _messages(tmp_path)):
                    event.set()
                    return
            stop.wait(0.01)

    worker = threading.Thread(target=cancel_after_prompt)
    worker.start()
    try:
        result = provider.invoke(_request(tmp_path))
    finally:
        stop.set()
        worker.join(timeout=1.0)
    assert event.is_set()
    assert result.status == CallStatus.CANCELLED
    sent = _messages(tmp_path)
    assert any(m.get("method") == "session/cancel" for m in sent)
    reply = next(m for m in sent if m.get("id") == "late-permission")
    assert reply["result"]["outcome"] == {"outcome": "cancelled"}


def test_acp_cancellation_before_spawn(tmp_path: Path) -> None:
    event = threading.Event()
    event.set()
    result = _provider(tmp_path, cancel_event=event).invoke(_request(tmp_path))
    assert result.status == CallStatus.CANCELLED
    assert not (tmp_path / "peer.jsonl").exists()


def test_acp_cancellation_between_receiving_and_dispatching_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = threading.Event()
    provider = _provider(tmp_path, "cancel_during_permission", cancel_event=event)
    module = importlib.import_module("autofusion.providers.acp")
    receive = module._Connection.receive

    def cancel_at_receive(connection: Any, timeout: float) -> Any:
        message = receive(connection, timeout)
        if message is not None and message.get("method") == "session/request_permission":
            event.set()
        return message

    monkeypatch.setattr(module._Connection, "receive", cancel_at_receive)
    result = provider.invoke(_request(tmp_path))
    assert result.status == CallStatus.CANCELLED
    reply = next(m for m in _messages(tmp_path) if m.get("id") == "permission")
    assert reply["result"]["outcome"] == {"outcome": "cancelled"}


def test_acp_blocked_stdin_obeys_deadline(tmp_path: Path) -> None:
    started = time.monotonic()
    result = _provider(tmp_path, "blocked_stdin").invoke(replace(
        _request(tmp_path), prompt="x" * 2_000_000, timeout_s=1.0,
    ))
    assert result.status == CallStatus.TIMEOUT
    assert time.monotonic() - started < 2.5


def test_acp_spawn_failure_is_provider_error(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    provider = replace(provider, executable=str(tmp_path / "missing.exe"))
    with pytest.raises(ProviderError, match="start"):
        provider.invoke(_request(tmp_path))


def test_acp_cleans_descendant_after_parent_eof(tmp_path: Path) -> None:
    with pytest.raises(ProviderError, match="EOF"):
        _provider(tmp_path, "descendant_eof").invoke(_request(tmp_path))
    time.sleep(0.8)
    assert not (tmp_path / "descendant-finished").exists()


def test_acp_grok_argv_pins_identity_and_denies_all_local_tools(tmp_path: Path) -> None:
    provider = replace(_provider(tmp_path), executable="grok", arguments=None)
    argv = provider.build_argv()
    # Installed Grok 1.0.13 help: --deny is a root option, agent rejects it.
    assert argv == (
        "grok", "--deny", "*", "agent", "--model", "grok-test",
        "--reasoning-effort", "xhigh", "--no-leader", "stdio",
    )


def test_acp_drops_ambient_and_requested_routing_or_permission_selectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selectors = ("GROK_CONFIG", "GROK_CONFIG_PATH", "XAI_API_KEY", "XAI_BASE_URL",
                 "GROK_DEFAULT_MODEL", "GROK_ALWAYS_APPROVE")
    for selector in selectors:
        monkeypatch.setenv(selector, "test-placeholder")
    result = _provider(tmp_path, "environment").invoke(replace(
        _request(tmp_path), environment_allowlist=selectors,
    ))
    assert result.status == CallStatus.COMPLETED
    environment = next(m["environment"] for m in _messages(tmp_path) if "environment" in m)
    assert not set(selectors).intersection(environment)
    assert environment["GROK_DEFAULT_SELECTED_PERMISSION"] == "reject"
    assert environment["GROK_DISABLE_API_KEY_AUTH"] == "1"


def test_acp_does_not_accept_catalog_as_selected_model(tmp_path: Path) -> None:
    with pytest.raises(ProviderError, match="identity"):
        _provider(tmp_path, "catalog_only").invoke(_request(tmp_path))


def test_acp_observes_grok_model_state_extension(tmp_path: Path) -> None:
    result = _provider(tmp_path, "grok_model_state").invoke(_request(tmp_path))
    assert result.effective_model == "grok-test"


@pytest.mark.parametrize("profile", [
    replace(_profile(), enabled=False), replace(_profile(), callable=False),
    replace(_profile(), model="claude-fable-5"),
    replace(_profile(), canonical_model="Claude-Fable-5-1"), replace(_profile(), model="fable"),
])
def test_acp_admission_blocks_disabled_noncallable_and_fable(
    tmp_path: Path, profile: ModelProfile,
) -> None:
    provider = replace(_provider(tmp_path), profile=profile)
    with pytest.raises(ProviderError):
        provider.invoke(_request(tmp_path))
    assert not (tmp_path / "peer.jsonl").exists()


@pytest.mark.parametrize("failed_thread", [0, 1, 2, 3])
def test_acp_io_startup_failure_reaps_child_and_closes_all_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_thread: int,
) -> None:
    provider = _provider(tmp_path)
    processes: list[subprocess.Popen[bytes]] = []
    popen = subprocess.Popen
    start_thread = threading.Thread.start
    started = 0

    def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    def fail_start(thread: threading.Thread) -> None:
        nonlocal started
        started += 1
        if started == failed_thread:
            raise RuntimeError("thread creation unavailable")
        start_thread(thread)

    monkeypatch.setattr(subprocess, "Popen", capture_process)
    monkeypatch.setattr(threading.Thread, "start", fail_start)
    if failed_thread == 0:
        def fail_connection(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("connection initialization unavailable")
        module = importlib.import_module("autofusion.providers.acp")
        monkeypatch.setattr(module, "_Connection", fail_connection)
    try:
        with pytest.raises(ProviderError, match="I/O startup"):
            provider.invoke(_request(tmp_path))
        assert processes and processes[0].poll() is not None
        assert all(stream is not None and stream.closed for stream in (
            processes[0].stdin, processes[0].stdout, processes[0].stderr,
        ))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=1)


@pytest.mark.parametrize("filename", ["config.toml", "managed_config.toml", "requirements.toml"])
@pytest.mark.parametrize("text", [
    '[hooks.SessionStart]\ncommand = "blocked"\n',
    '["ho\\u006fks".SessionStart]\ncommand = "blocked"\n',
    '[models]\nbase_url = "https://example.invalid"\n',
    '[auth_provider]\ncommand = "blocked"\n',
    '[mcp_servers.fixture]\ncommand = "blocked"\n',
    '[model.fixture]\nmodel = "alternate"\n',
    '[plugins]\nenabled = true\n',
])
def test_acp_preflight_denies_executable_or_routing_config(
    tmp_path: Path, filename: str, text: str,
) -> None:
    provider = _provider(tmp_path)
    (tmp_path / "grok-home" / filename).write_text(text, encoding="utf-8")
    with pytest.raises(ProviderError, match="configuration"):
        provider.invoke(_request(tmp_path))
    assert not (tmp_path / "peer.jsonl").exists()


@pytest.mark.parametrize("relative", [
    "grok-home/hooks/entry", "grok-home/hooks-paths", "grok-home/plugins/entry",
    ".grok/hooks/entry", ".grok/hooks-paths", ".grok/plugins/entry", ".mcp.json", ".envrc",
])
def test_acp_preflight_denies_discovered_extensions_before_spawn(
    tmp_path: Path, relative: str,
) -> None:
    provider = _provider(tmp_path)
    entry = tmp_path / relative
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("blocked", encoding="utf-8")
    with pytest.raises(ProviderError, match="extension"):
        provider.invoke(_request(tmp_path))
    assert not (tmp_path / "peer.jsonl").exists()


@pytest.mark.parametrize("content", ["invalid = [", "#" * 262_145], ids=["malformed", "oversized"])
def test_acp_preflight_rejects_malformed_or_oversized_config(
    tmp_path: Path, content: str,
) -> None:
    provider = _provider(tmp_path)
    (tmp_path / "grok-home" / "config.toml").write_text(content, encoding="utf-8")
    with pytest.raises(ProviderError, match="configuration"):
        provider.invoke(_request(tmp_path))
    assert not (tmp_path / "peer.jsonl").exists()


def test_acp_preflight_checks_project_ancestors(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    config = tmp_path / ".grok" / "config.toml"
    config.parent.mkdir()
    config.write_text('[hooks.SessionStart]\ncommand="blocked"\n', encoding="utf-8")
    nested = tmp_path / "child"
    nested.mkdir()
    with pytest.raises(ProviderError, match="configuration"):
        provider.invoke(replace(_request(tmp_path), working_directory=nested))
    assert not (tmp_path / "peer.jsonl").exists()


def test_acp_preflight_accepts_safe_config_and_empty_extensions(tmp_path: Path) -> None:
    provider = _provider(tmp_path, "environment")
    home = tmp_path / "grok-home"
    (home / "config.toml").write_text(
        '# hooks are only mentioned in a comment\n[ui]\npermission_mode="ask"\n',
        encoding="utf-8",
    )
    (home / "hooks").mkdir()
    (home / "hooks-paths").write_bytes(b"")
    (home / "plugins").mkdir()
    (tmp_path / ".mcp.json").write_bytes(b"")
    result = provider.invoke(_request(tmp_path))
    assert result.status == CallStatus.COMPLETED
    environment = next(m["environment"] for m in _messages(tmp_path) if "environment" in m)
    assert environment["GROK_CODEX_HOOKS_ENABLED"] == "0"
    assert environment["GROK_DISABLE_AUTOUPDATER"] == "1"


def test_acp_preflight_checks_system_managed_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider(tmp_path)
    system = tmp_path / "system-grok"
    system.mkdir()
    (system / "managed_config.toml").write_text(
        '[hooks.SessionStart]\ncommand="blocked"\n', encoding="utf-8",
    )
    module = importlib.import_module("autofusion.providers.acp")
    monkeypatch.setattr(module, "_SYSTEM_CONFIG_DIRECTORY", system, raising=False)
    with pytest.raises(ProviderError, match="configuration"):
        provider.invoke(_request(tmp_path))
    assert not (tmp_path / "peer.jsonl").exists()


@pytest.mark.parametrize("scenario", ["final_duplicate", "final_nonfinite", "final_huge_integer"])
def test_acp_final_output_requires_strict_json(tmp_path: Path, scenario: str) -> None:
    with pytest.raises(ProviderError):
        _provider(tmp_path, scenario).invoke(replace(_request(tmp_path), max_output_chars=10_000))


@pytest.mark.parametrize("cancelled", [False, True], ids=["deadline", "cancel"])
def test_acp_preflight_exhaustion_does_not_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancelled: bool,
) -> None:
    event = threading.Event()
    provider = _provider(tmp_path, cancel_event=event)
    module = importlib.import_module("autofusion.providers.acp")

    def delayed_preflight(*args: Any, **kwargs: Any) -> None:
        if cancelled:
            event.set()
        else:
            time.sleep(0.03)

    def forbidden_spawn(*args: Any, **kwargs: Any) -> None:
        pytest.fail("ACP spawned after preflight exhausted the call")

    monkeypatch.setattr(module, "_preflight_grok", delayed_preflight)
    monkeypatch.setattr(subprocess, "Popen", forbidden_spawn)
    result = provider.invoke(replace(_request(tmp_path), timeout_s=0.01))
    assert result.status == (CallStatus.CANCELLED if cancelled else CallStatus.TIMEOUT)
