"""agy names the session model in its init event, bound to the result by conversation id.

Measured on agy 1.5.9 (print mode, stream-json): the terminal result carries no model
field; the init event does, and both the init event and the result carry the same
conversation id. The adapter accepts that statement as the observed identity only when
the two ids are equal. Every weaker shape is still "no identity".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from autofusion.errors import ProviderError
from autofusion.models import CallStatus, ModelProfile, ProviderRequest, ProviderResult

MODEL = "gemini-4-hydrogen-xhigh"
CONVERSATION = "7888bfeb-c586-4bf7-b02b-9d1156981376"


def profile() -> ModelProfile:
    return ModelProfile(
        handle="gemini-hydrogen",
        transport="agy-headless",
        callable=True,
        model=MODEL,
        canonical_model=MODEL,
        vendor="google",
        family="gemini",
        effort="xhigh",
        context="packet",
    )


def request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        run_id="run",
        call_id="call",
        handle="gemini-hydrogen",
        prompt="Review this.\nReturn JSON.",
        response_schema={"type": "object", "required": ["answer"]},
        working_directory=tmp_path,
        timeout_s=3,
        max_output_chars=16000,
    )


def invoke(tmp_path: Path, wire: str) -> ProviderResult:
    from autofusion.providers.agy import AgyProvider

    peer = tmp_path / "peer.py"
    peer.write_text(
        "import sys\nsys.stdin.read()\n" + f"sys.stdout.write({wire!r})\nsys.stdout.flush()\n",
        encoding="utf-8",
    )
    return AgyProvider(profile(), executable=sys.executable, arguments=(str(peer),)).invoke(
        request(tmp_path)
    )


def init(model: object = MODEL, conversation: object = CONVERSATION) -> str:
    payload: dict[str, object] = {
        "cwd": "C:/work",
        "tools": ["ask_custom_permission"],
        "permission_mode": "request-review",
    }
    if model is not None:
        payload["model"] = model
    event: dict[str, object] = {"event": "init", "init": payload}
    if conversation is not None:
        event["conversation_id"] = conversation
    return json.dumps(event) + "\n"


def step(conversation: str = CONVERSATION) -> str:
    update = {
        "conversation_id": conversation,
        "state": "DONE",
        "step_index": 0,
        "step_type": "agent_response",
        "text_delta": "partial",
    }
    return json.dumps({"event": "step_update", "step_update": update}) + "\n"


def result(conversation: object = CONVERSATION, **changes: object) -> str:
    value: dict[str, object] = {
        "status": "SUCCESS",
        "response": '{"answer":"ok"}',
        "duration_seconds": 1.5,
        "num_turns": 1,
        "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
    }
    if conversation is not None:
        value["conversation_id"] = conversation
    value.update(changes)
    return json.dumps({"event": "result", "result": value}) + "\n"


def test_session_model_bound_by_conversation_is_the_observed_identity(tmp_path: Path) -> None:
    done = invoke(tmp_path, init() + step() + result())
    assert done.status == CallStatus.COMPLETED
    assert done.effective_model == MODEL
    assert done.observed_model == MODEL
    assert done.identity_evidence == "provider-response"
    assert done.structured_output == {"answer": "ok"}


def test_a_different_session_model_is_an_identity_mismatch(tmp_path: Path) -> None:
    # The substitution this control exists for: the session ran on another model.
    with pytest.raises(ProviderError, match="identity mismatch"):
        invoke(tmp_path, init(model="gemini-3.8-flash-low") + step() + result())


@pytest.mark.parametrize(
    "wire",
    [
        pytest.param(init(conversation="another-conversation") + result(), id="other-conversation"),
        pytest.param(init(conversation=None) + result(), id="init-without-conversation"),
        pytest.param(init() + result(conversation=None), id="result-without-conversation"),
        pytest.param(init(model=None) + result(), id="init-without-model"),
        pytest.param(result(), id="no-init"),
    ],
)
def test_an_unbound_or_silent_init_is_no_identity(tmp_path: Path, wire: str) -> None:
    with pytest.raises(ProviderError, match="omitted effective model identity"):
        invoke(tmp_path, wire)


def test_two_init_events_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProviderError, match="more than one init"):
        invoke(tmp_path, init() + init() + result())


def test_terminal_and_session_model_must_agree(tmp_path: Path) -> None:
    with pytest.raises(ProviderError, match="disagree"):
        invoke(tmp_path, init() + result(model="gemini-3.8-flash-low"))


@pytest.mark.parametrize("model", [7, "", ["gemini-4-hydrogen-xhigh"]])
def test_a_malformed_session_model_is_rejected(tmp_path: Path, model: object) -> None:
    with pytest.raises(ProviderError, match="invalid init"):
        invoke(tmp_path, init(model=model) + result())


def test_a_malformed_conversation_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProviderError, match="invalid init"):
        invoke(tmp_path, init(conversation=17) + result())


def test_terminal_model_alone_still_identifies(tmp_path: Path) -> None:
    # The older shape: no init, the result names the model itself.
    done = invoke(tmp_path, result(conversation=None, model=MODEL))
    assert done.effective_model == MODEL
    assert done.identity_evidence == "provider-response"
