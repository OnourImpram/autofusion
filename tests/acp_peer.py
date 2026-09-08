"""ACP subprocess fixture. This file is executed by tests, never by the application."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

scenario, log_name = sys.argv[1:3]
log_path = Path(log_name)
if scenario == "environment":
    names = ("GROK_CONFIG", "GROK_CONFIG_PATH", "XAI_API_KEY", "XAI_BASE_URL",
             "GROK_DEFAULT_MODEL", "GROK_ALWAYS_APPROVE")
    environment: dict[str, object] = {name: "present" for name in names if name in os.environ}
    for name in ("GROK_DEFAULT_SELECTED_PERMISSION", "GROK_DISABLE_API_KEY_AUTH",
                 "GROK_CODEX_HOOKS_ENABLED", "GROK_DISABLE_AUTOUPDATER"):
        environment[name] = os.environ.get(name)
    log_path.write_text(json.dumps({"environment": environment}) + "\n", encoding="utf-8")


def receive() -> dict[str, Any]:
    line = sys.stdin.buffer.readline()
    if not line:
        raise SystemExit(0)
    message: dict[str, Any] = json.loads(line)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(message) + "\n")
    return message


def send(message: object, *, fragmented: bool = False) -> None:
    raw = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
    if fragmented:
        for index in range(0, len(raw), 3):
            os.write(sys.stdout.fileno(), raw[index:index + 3])
    else:
        os.write(sys.stdout.fileno(), raw)


def update(kind: str, **kwargs: object) -> None:
    send({"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": "wrong" if scenario == "wrong_session" else "session-test",
        "update": {"sessionUpdate": kind, **kwargs},
    }}, fragmented=True)


def permission(identifier: str = "permission") -> None:
    options = [{"optionId": "allow", "kind": "allow_once", "name": "Allow"}]
    if scenario != "no_reject_option":
        options.append({"optionId": "deny", "kind": (
            "reject_always" if scenario == "reject_always" else "reject_once"), "name": "Deny"})
    send({"jsonrpc": "2.0", "id": identifier, "method": "session/request_permission",
          "params": {"sessionId": "session-test", "options": options,
                     "toolCall": {"toolCallId": "tool-test", "kind": "execute"}}})


def model_options(model: str = "grok-test") -> list[dict[str, object]]:
    return [{"id": "model", "name": "Model", "category": "model", "type": "select",
             "currentValue": model, "options": [{"value": model, "name": model}]}]


for phase in ("initialize", "session/new", "session/prompt"):
    if phase == "session/prompt" and scenario == "blocked_stdin":
        time.sleep(10)
    request = receive()
    assert request["method"] == phase
    identifier = request["id"]
    if scenario == "eof:" + phase:
        raise SystemExit(0)
    if scenario.startswith("rpc:" + phase + ":"):
        send({"jsonrpc": "2.0", "id": identifier,
              "error": {"code": int(scenario.rsplit(":", 1)[1]), "message": "fixture failure"}})
        raise SystemExit(0)
    if scenario == "timeout:" + phase:
        if phase == "session/prompt":
            cancelled = receive()
            assert cancelled["method"] == "session/cancel"
            send({"jsonrpc": "2.0", "id": identifier, "result": {"stopReason": "cancelled"}})
        time.sleep(10)
    if phase == "initialize":
        if scenario == "descendant_eof":
            child_program = (
                "import pathlib,time; time.sleep(0.6); "
                "pathlib.Path('descendant-finished').write_text('finished')"
            )
            subprocess.Popen(
                [sys.executable, "-c", child_program], cwd=log_path.parent,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            os._exit(0)
        raw_cases = {
            "bad_json": b"invalid\n", "bad_utf8": b"\xff\n", "array": b"[]\n",
            "duplicate_key": b'{"jsonrpc":"2.0","id":1,"id":1,"result":{}}\n',
            "unfinished_frame": b'{"jsonrpc":',
            "frame_limit": b"x" * 2_000_000,
            "nonfinite_json": b'{"jsonrpc":"2.0","id":1,"result":NaN}\n',
            "huge_integer": b'{"jsonrpc":"2.0","id":' + b"9" * 5000 + b',"result":{}}\n',
        }
        if scenario in raw_cases:
            os.write(sys.stdout.fileno(), raw_cases[scenario])
            raise SystemExit(0)
        response: dict[str, Any] = {"jsonrpc": "2.0", "id": identifier,
                                    "result": {"protocolVersion": 1, "agentCapabilities": {}}}
        if scenario == "bad_rpc":
            response["jsonrpc"] = "1.0"
        if scenario == "wrong_id":
            response["id"] = "not-requested"
        if scenario == "null_id":
            response["id"] = None
        if scenario == "bad_params":
            response = {"jsonrpc": "2.0", "method": "notification", "params": []}
        if scenario == "both_result_error":
            response["error"] = {"code": -32603, "message": "bad"}
        if scenario == "invalid_error":
            response.pop("result")
            response["error"] = {"code": "invalid", "message": "bad"}
        if scenario == "bad_protocol":
            response["result"]["protocolVersion"] = 99
        if scenario in {"grok_model_state", "catalog_only"}:
            state: dict[str, object] = {"availableModels": [{"modelId": "grok-test"}]}
            if scenario == "grok_model_state":
                state["currentModelId"] = "grok-test"
            response["result"]["_meta"] = {"modelState": state}
        send(response)
    elif phase == "session/new":
        session: dict[str, Any] = {"sessionId": "session-test"}
        if scenario not in {"model_missing", "grok_model_state", "catalog_only"}:
            session["configOptions"] = model_options(
                "unexpected" if scenario == "model_mismatch" else "grok-test")
        if scenario == "bad_session":
            session["sessionId"] = 7
        send({"jsonrpc": "2.0", "id": identifier, "result": session})
    else:
        if scenario == "duplicate_permission_id":
            send({"jsonrpc": "2.0", "id": "ambiguous-permission",
                  "method": "session/request_permission", "params": {
                      "sessionId": "session-test", "toolCall": {}, "options": [
                          {"kind": "allow_once", "optionId": "same", "name": "Allow"},
                          {"kind": "reject_once", "optionId": "same", "name": "Deny"},
                      ],
                  }})
            receive()
        if scenario == "cancel_during_permission":
            permission()
            messages = [receive(), receive()]
            reply = next(m for m in messages if m.get("id") == "permission")
            assert reply["result"]["outcome"]["outcome"] == "cancelled"
            send({"jsonrpc": "2.0", "id": identifier, "result": {"stopReason": "cancelled"}})
            raise SystemExit(0)
        if scenario == "object_permission_kind":
            send({"jsonrpc": "2.0", "id": "invalid-permission",
                  "method": "session/request_permission", "params": {
                      "sessionId": "session-test", "toolCall": {},
                      "options": [{"kind": {}, "optionId": "invalid"}],
                  }})
            receive()
        if scenario in {"reject_once", "reject_always", "no_reject_option"}:
            permission()
            reply = receive()
            assert reply["result"]["outcome"].get("optionId") != "allow"
        if scenario.startswith("request:"):
            send({"jsonrpc": "2.0", "id": "forbidden", "method": scenario[8:],
                  "params": {"sessionId": "session-test",
                             "path": str(log_path.parent / "unapproved.txt"),
                             "content": "not permitted", "command": "not permitted"}})
            reply = receive()
            assert "error" in reply
        if scenario == "cancel_permission":
            cancellation = receive()
            assert cancellation["method"] == "session/cancel"
            permission("late-permission")
            reply = receive()
            assert reply["result"]["outcome"]["outcome"] == "cancelled"
            send({"jsonrpc": "2.0", "id": identifier, "result": {"stopReason": "cancelled"}})
            raise SystemExit(0)
        if scenario == "rpc_cancelled":
            send({"jsonrpc": "2.0", "id": identifier,
                  "error": {"code": -32800, "message": "cancelled"}})
            raise SystemExit(0)
        update("plan", entries=[])
        update("agent_thought_chunk", content={"type": "text", "text": "ignored"})
        if scenario == "model_update_mismatch":
            update("config_option_update", configOptions=model_options("unexpected"))
        if scenario == "model_update_no_category":
            options = model_options("unexpected")
            options[0].pop("category")
            update("config_option_update", configOptions=options)
        content = {"answer": "caf\u00e9"}
        output = json.dumps(content, ensure_ascii=False)
        if scenario == "output_limit":
            output = "x" * 4096
        if scenario == "invalid_structured":
            output = "not structured"
        if scenario == "schema_failure":
            output = "{}"
        if scenario == "final_duplicate":
            output = '{"answer":"first","answer":"second"}'
        if scenario == "final_nonfinite":
            output = '{"answer":NaN}'
        if scenario == "final_huge_integer":
            output = '{"answer":' + "9" * 5000 + '}'
        if scenario == "bad_content":
            update("agent_message_chunk", content={"type": "text", "text": 23})
        else:
            for part in [output[:7], output[7:]]:
                update("agent_message_chunk", content={"type": "text", "text": part})
        stop = "cancelled" if scenario == "peer_cancelled" else "end_turn"
        if scenario.startswith("stop:"):
            stop = scenario[5:]
        if scenario == "unknown_stop":
            stop = "success"
        terminal: dict[str, Any] = {"stopReason": stop, "usage": {
            "inputTokens": 12, "outputTokens": 5, "totalTokens": 17,
        }}
        if scenario == "missing_stop":
            terminal.pop("stopReason")
        if scenario == "object_stop":
            terminal["stopReason"] = {}
        if scenario == "bad_usage":
            terminal["usage"]["inputTokens"] = True
        send({"jsonrpc": "2.0", "id": identifier, "result": terminal})
