"""A real fake executable exercises the headless stream and isolation contract."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from autofusion.errors import ProviderError
from autofusion.models import CallStatus, ModelProfile, ProviderRequest, ProviderResult


def profile() -> ModelProfile:
    return ModelProfile(
        handle="gemini-flash",
        transport="agy-headless",
        callable=True,
        model="gemini-3.8-flash-high",
        canonical_model="gemini-3.8-flash-high",
        vendor="google",
        family="gemini",
        effort="high",
        context="packet",
    )


def request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        run_id="run",
        call_id="call",
        handle="gemini-flash",
        prompt='Review a quoted "value".\nReturn JSON.',
        response_schema={"type": "object", "required": ["answer"]},
        working_directory=tmp_path,
        timeout_s=3,
        max_output_chars=16000,
    )


def invoke(tmp_path: Path, body: str, req: ProviderRequest | None = None) -> ProviderResult:
    # Import inside tests so reversal exercises the entire suite, without a collection abort.
    from autofusion.providers.agy import AgyProvider

    peer = tmp_path / "peer.py"
    peer.write_text(
        "import json, os, sys, time\nfrom pathlib import Path\n" + body, encoding="utf-8"
    )
    return AgyProvider(profile(), executable=sys.executable, arguments=(str(peer),)).invoke(
        req or request(tmp_path)
    )


def terminal(**changes: object) -> str:
    value: dict[str, object] = {
        "status": "SUCCESS",
        "response": '{"answer":"ok"}',
        "model": "gemini-3.8-flash-high",
        "usage": {
            "input_tokens": 12,
            "output_tokens": 3,
            "thinking_tokens": 1,
            "cache_read_tokens": 0,
            "total_tokens": 15,
        },
    }
    value.update(changes)
    return json.dumps({"event": "result", "result": value}) + "\n"


def emit(wire: str) -> str:
    return f"sys.stdin.read()\nsys.stdout.write({wire!r})\nsys.stdout.flush()\n"


def test_agy_framing_sandbox_home_cleanup_and_terminal_usage(tmp_path: Path) -> None:
    body = """
event = json.loads(sys.stdin.readline())
assert event['event'] == 'user'
assert 'Review a quoted "value".\\nReturn JSON.' in event['message']['content']
assert 'required' in event['message']['content']
assert sys.stdin.read() == ''
assert '--sandbox' in sys.argv and '--disable-slash-commands' in sys.argv
assert '--dangerously-skip-permissions' not in sys.argv
assert sys.argv[sys.argv.index('--input-format') + 1] == 'stream-json'
assert sys.argv[sys.argv.index('--output-format') + 1] == 'stream-json'
assert sys.argv[sys.argv.index('--model') + 1] == 'gemini-3.8-flash-high'
assert sys.argv[sys.argv.index('--effort') + 1] == 'high'
assert os.environ['HOME'] == os.environ['USERPROFILE']
assert os.environ['AGY_CLI_DISABLE_AUTO_UPDATE'] == 'true'
assert os.environ['AGY_CLI_HIDE_ACCOUNT_INFO'] == '1'
home = Path(os.environ['HOME'])
settings = json.loads((home / '.gemini/antigravity-cli/settings.json').read_text())
denied = set(settings['permissions']['deny'])
assert {'write_file(*)', 'command(*)', 'unsandboxed(*)', 'mcp(*)'} <= denied
assert json.loads((home / '.gemini/config/mcp_config.json').read_text()) == {'mcpServers': {}}
assert not (home / '.gemini/oauth_creds.json').exists()
Path('observed-home.txt').write_text(str(home))
if os.name != 'nt':
    assert home.stat().st_mode & 0o777 == 0o700
"""
    wire = (
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "agent_response",
                    "state": "DONE",
                    "text_delta": "untrusted partial",
                    "usage": {"input_tokens": 999},
                },
            }
        )
        + "\n"
        + terminal()
    )
    result = invoke(tmp_path, body + f"sys.stdout.write({wire!r})\n")
    assert result.status == CallStatus.COMPLETED
    assert result.structured_output == {"answer": "ok"}
    assert result.effective_model == profile().canonical_model
    assert (result.input_tokens, result.output_tokens) == (12, 3)
    assert result.cost_usd is None and not result.cost_verified
    assert not Path((tmp_path / "observed-home.txt").read_text()).exists()


def test_agy_fragmented_utf8_events(tmp_path: Path) -> None:
    wire = terminal(response=json.dumps({"answer": "雪"}, ensure_ascii=False)).encode()
    result = invoke(
        tmp_path,
        "sys.stdin.read()\n" + f"for b in {wire!r}:\n"
        "    sys.stdout.buffer.write(bytes([b]))\n    sys.stdout.buffer.flush()\n",
    )
    assert result.structured_output == {"answer": "雪"}


@pytest.mark.parametrize(
    ("wire", "message"),
    [
        ("not JSON\n", "JSON"),
        ("[]\n", "object"),
        (json.dumps({"event": "step_update", "step_update": []}) + "\n", "step_update"),
        (json.dumps({"event": "future"}) + "\n", "event"),
        ("", "terminal"),
        (terminal() + terminal(), "terminal"),
        (terminal() + "malformed\n", "terminal"),
        (terminal(model="other-model"), "identity mismatch"),
        (terminal(model=None), "identity"),
        (terminal(usage={"input_tokens": -1}), "usage"),
        (terminal(usage={"input_tokens": True}), "usage"),
        (terminal(response=""), "empty"),
        (terminal(response='{"wrong":true}'), "schema"),
    ],
)
def test_agy_rejects_invalid_stream(tmp_path: Path, wire: str, message: str) -> None:
    with pytest.raises(ProviderError, match=message):
        invoke(tmp_path, emit(wire))


@pytest.mark.parametrize(
    ("error", "classification"),
    [
        ("quota exhausted", "rate_limited"),
        ("OAuth required", "auth_required"),
        ("model not available", "model_unavailable"),
        ("permission denied", "permission_denied"),
        ("generic failure", "terminal_failure"),
    ],
)
def test_agy_terminal_failure_is_explicit(tmp_path: Path, error: str, classification: str) -> None:
    result = invoke(tmp_path, emit(terminal(status="ERROR", response="", error=error)))
    assert result.status == CallStatus.FAILED
    assert result.error == f"agy:{classification}"
    assert result.structured_output is None and result.effective_model is None


def test_agy_error_event_cannot_be_hidden_by_success(tmp_path: Path) -> None:
    wire = json.dumps({"event": "error", "error": "quota exhausted"}) + "\n" + terminal()
    result = invoke(tmp_path, emit(wire))
    assert result.error == "agy:rate_limited"


def test_agy_nonzero_exit_cannot_complete(tmp_path: Path) -> None:
    result = invoke(tmp_path, emit(terminal()) + "sys.exit(7)\n")
    assert result.status == CallStatus.FAILED
    assert result.error == "agy:process_exit:7"


@pytest.mark.parametrize("status", ["CANCELED", "INTERRUPTED"])
def test_agy_cancelled_terminal(tmp_path: Path, status: str) -> None:
    result = invoke(tmp_path, emit(terminal(status=status, response="")))
    assert result.status == CallStatus.CANCELLED


def test_agy_timeout_includes_child_not_reading_stdin(tmp_path: Path) -> None:
    req = replace(request(tmp_path), prompt="x" * 2_000_000, timeout_s=0.4)
    started = time.monotonic()
    result = invoke(
        tmp_path, "Path('observed-home.txt').write_text(os.environ['HOME'])\ntime.sleep(30)\n", req
    )
    assert result.status == CallStatus.TIMEOUT
    assert time.monotonic() - started < 3
    assert not Path((tmp_path / "observed-home.txt").read_text()).exists()


def test_agy_output_limit(tmp_path: Path) -> None:
    with pytest.raises(ProviderError, match="output limit"):
        invoke(tmp_path, emit("x" * 100000), replace(request(tmp_path), max_output_chars=100))


def test_agy_ambient_selectors_are_not_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "synthetic-selector")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://invalid.example")
    req = replace(request(tmp_path), environment_allowlist=("GOOGLE_API_KEY", "OPENAI_BASE_URL"))
    result = invoke(
        tmp_path,
        "assert 'GOOGLE_API_KEY' not in os.environ\n"
        "assert 'OPENAI_BASE_URL' not in os.environ\n" + emit(terminal()),
        req,
    )
    assert result.status == CallStatus.COMPLETED


def test_agy_missing_binary_is_provider_error(tmp_path: Path) -> None:
    from autofusion.providers.agy import AgyProvider

    with pytest.raises(ProviderError, match="start"):
        AgyProvider(profile(), executable=str(tmp_path / "absent")).invoke(request(tmp_path))


@pytest.mark.parametrize("status", ["disabled", "non-callable", "fable"])
def test_agy_admission_before_spawn(tmp_path: Path, status: str) -> None:
    from autofusion.providers.agy import AgyProvider

    p = profile()
    if status == "disabled":
        p = replace(p, enabled=False)
    elif status == "non-callable":
        p = replace(p, callable=False)
    else:
        p = replace(p, model="alias", canonical_model="claude-fable-5-1")
    with pytest.raises(ProviderError, match=r"disabled|non-callable|Fable"):
        AgyProvider(p, executable=str(tmp_path / "must-not-start")).invoke(request(tmp_path))


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
def test_agy_invalid_deadline_before_spawn(tmp_path: Path, timeout: float) -> None:
    from autofusion.providers.agy import AgyProvider

    with pytest.raises(ProviderError, match="deadline"):
        AgyProvider(profile(), executable=str(tmp_path / "must-not-start")).invoke(
            replace(request(tmp_path), timeout_s=timeout)
        )


def test_agy_authentication_exit_without_terminal_is_explicit(tmp_path: Path) -> None:
    result = invoke(tmp_path, "sys.stderr.write('OAuth required')\nsys.exit(1)\n")
    assert result.status == CallStatus.FAILED
    assert result.error == "agy:auth_required"


def test_agy_non_utf8_output_is_rejected(tmp_path: Path) -> None:
    wire = terminal().encode().replace(b"ok", b"\xff")
    with pytest.raises(ProviderError, match="UTF-8"):
        invoke(tmp_path, f"sys.stdout.buffer.write({wire!r})\n")


@pytest.mark.parametrize(
    "event",
    [
        {"event": []},
        {"event": "step_update", "step_update": {"step_index": -1}},
        {"event": "step_update", "step_update": {"state": []}},
        {"event": "step_update", "step_update": {"step_type": False}},
        {"event": "step_update", "step_update": {"usage": "invalid"}},
    ],
)
def test_agy_malformed_event_fields_are_provider_errors(tmp_path: Path, event: object) -> None:
    with pytest.raises(ProviderError):
        invoke(tmp_path, emit(json.dumps(event) + "\n" + terminal()))


def test_agy_terminal_error_cannot_be_hidden_by_success(tmp_path: Path) -> None:
    result = invoke(tmp_path, emit(terminal(error="quota exhausted")))
    assert result.status == CallStatus.FAILED
    assert result.error == "agy:rate_limited"


def test_agy_documented_unknown_model_error(tmp_path: Path) -> None:
    result = invoke(
        tmp_path,
        emit(
            terminal(
                status="ERROR",
                response="",
                error="invalid model selection: model is not recognized as a known model",
            )
        ),
    )
    assert result.error == "agy:model_unavailable"


@pytest.mark.parametrize("inherit_pipes", [False, True])
def test_agy_descendants_stop_before_home_cleanup(tmp_path: Path, inherit_pipes: bool) -> None:
    child = "import time; from pathlib import Path; time.sleep(0.8); " + (
        "Path('descendant-survived').write_text('survived')"
    )
    pipe_setting = (
        ""
        if inherit_pipes
        else (", stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL")
    )
    result = invoke(
        tmp_path,
        "import subprocess\n"
        + f"subprocess.Popen([sys.executable, '-c', {child!r}]{pipe_setting})\n"
        + emit(terminal()),
    )
    time.sleep(1)
    assert result.status == CallStatus.COMPLETED
    assert not (tmp_path / "descendant-survived").exists()


@pytest.mark.parametrize(
    "wire",
    [
        '{"event":"init","init":{"count":' + "1" * 5000 + "}}\n" + terminal(),
        terminal(response='{"answer":' + "1" * 5000 + "}"),
        terminal().replace('"status": "SUCCESS"', '"status":"ERROR","status":"SUCCESS"'),
        terminal(response='{"answer":NaN}'),
    ],
    ids=["oversized-event-number", "oversized-response-number", "duplicate-status", "nonfinite"],
)
def test_agy_invalid_json_values_are_provider_errors(tmp_path: Path, wire: str) -> None:
    with pytest.raises(ProviderError):
        invoke(tmp_path, emit(wire))


def test_agy_thread_start_failure_cleans_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autofusion.providers import agy
    from autofusion.providers._job import WindowsJob

    processes: list[subprocess.Popen[bytes]] = []
    jobs: list[WindowsJob] = []
    real_popen = subprocess.Popen
    real_job = WindowsJob
    real_start = threading.Thread.start
    count = 0

    def capture_process(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    def capture_job() -> WindowsJob:
        job = real_job()
        jobs.append(job)
        return job

    def fail_second_start(thread: threading.Thread) -> None:
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("simulated thread exhaustion")
        real_start(thread)

    monkeypatch.setattr(subprocess, "Popen", capture_process)
    monkeypatch.setattr(agy, "WindowsJob", capture_job)
    monkeypatch.setattr(threading.Thread, "start", fail_second_start)
    try:
        with pytest.raises(ProviderError, match="I/O"):
            invoke(tmp_path, "time.sleep(30)\n")
        assert processes[0].poll() is not None
        assert jobs[0]._handle is None
    finally:
        for job in jobs:
            job.close()
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)


def test_agy_default_disabled_and_registered(tmp_path: Path) -> None:
    from autofusion.config import load_config
    from autofusion.registry import ProviderRegistry

    config = load_config(repo_root=tmp_path)
    configured = config.model("gemini-flash")
    assert not configured.enabled
    assert configured.capabilities["read_only"] is True
    assert configured.capabilities["identity_source"] == "terminal-result"
    assert "gemini-flash" in ProviderRegistry.from_config(config).providers


def test_agy_doctor_available_without_auth_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autofusion import doctor
    from autofusion.config import FusionConfig

    config = FusionConfig(
        data={
            "models": {
                "gemini-flash": {
                    "transport": "agy-headless",
                    "model": "gemini-3.8-flash-high",
                    "params": {"executable": sys.executable},
                }
            }
        },
        source_paths=(),
    )
    monkeypatch.setattr(doctor, "_version", lambda path: "fake binary version")
    checks = doctor.inspect_environment(config, grounding_runner=None)["checks"]
    assert isinstance(checks, list)
    assert checks[0]["status"] == "available"
