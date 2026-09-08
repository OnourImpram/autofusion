"""Python admission for the packaged, bounded Claude Code dual-review Workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from jsonschema import Draft202012Validator

from autofusion.errors import PolicyError
from autofusion.identity import names_fable
from autofusion.prompts import load_response_schema
from autofusion.util import JsonObject, canonical_json_bytes

if TYPE_CHECKING:
    from autofusion.session_bridge import SessionBridge

INPUT_SCHEMA = "workflow-dual-review-input.schema.json"
OUTPUT_SCHEMA = "workflow-dual-review-output.schema.json"
CORRELATION = (
    "schema_version", "run_id", "call_id", "packet_hash", "artifact_manifest_hash",
    "reviewer_schema_id", "reviewer_schema_hash",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PolicyError(message)


def _validate(value: JsonObject, schema_name: str) -> None:
    schema = load_response_schema(schema_name)
    _require(Draft202012Validator(schema).is_valid(value), f"invalid {schema_name} envelope")


def validate_dual_review_input(bundle: JsonObject) -> None:
    """Admit exactly two blind requests with identical frozen artifacts and schemas."""
    _validate(bundle, INPUT_SCHEMA)
    from autofusion.session_bridge import validate_session_request

    requests = bundle["requests"]
    _require(len({request["call_id"] for request in requests}) == 2, "duplicate Workflow call")
    for request in requests:
        validate_session_request(request)
        _require(request["run_id"] == bundle["run_id"], "Workflow run identity mismatch")
    for field in ("packet_hash", "artifact_manifest_hash", "reviewer_schema_hash",
                  "reviewer_schema_id"):
        _require(requests[0][field] == requests[1][field], "Workflow requires the same packet")
    for field in ("handle", "model"):
        _require(requests[0]["requested_identity"][field]
                 != requests[1]["requested_identity"][field],
                 "Workflow requires distinct reviewer identities")


def validate_dual_review_output(bundle: JsonObject, output: JsonObject) -> list[JsonObject]:
    """Validate the entire batch before any call is consumed; keep stopped agents visible."""
    validate_dual_review_input(bundle)
    _validate(output, OUTPUT_SCHEMA)
    _require(output["run_id"] == bundle["run_id"], "Workflow result run mismatch")
    by_call = {request["call_id"]: request for request in bundle["requests"]}
    calls = [item["call_id"] for item in output["results"]]
    _require(len(set(calls)) == 2 and set(calls) == set(by_call),
             "Workflow results must cover both calls exactly once")
    normalized: list[JsonObject] = []
    models: set[str] = set()
    for item in output["results"]:
        request = by_call[item["call_id"]]
        result = item["result"]
        if result is None:
            result = {key: request[key] for key in CORRELATION}
            result.update({
                "status": "cancelled" if request["dispatchable"] else "missing",
                "actual_identity": None, "output": None,
                "error": "stopped agent returned null" if request["dispatchable"]
                         else "delegate is missing",
            })
        _require(all(result[key] == request[key] for key in CORRELATION),
                 "Workflow result correlation mismatch")
        identity = result["actual_identity"]
        if identity is not None:
            _require(not names_fable(identity), "Fable is self-only, never a returned reviewer")
            _require(all(identity[key] == request["requested_identity"][key]
                         for key in ("model", "vendor", "family", "delegate")),
                     "Workflow returned identity mismatch or forced model override")
            _require(identity["model"] not in models,
                     "Workflow returned the same model for both reviewers")
            models.add(identity["model"])
        _require(result["status"] not in {"completed", "abstaining"} or identity is not None,
                 "Workflow completed response requires actual identity")
        normalized.append(dict(result))
    return normalized


def import_dual_review_output(
    bridge: SessionBridge, run_id: str, output: JsonObject,
) -> list[JsonObject]:
    """Import against verified durable requests, never a caller-provided replacement packet."""
    bundle = bridge.requests(run_id)
    results = validate_dual_review_output(bundle, output)
    dispatchable = {request["call_id"] for request in bundle["requests"]
                    if request["dispatchable"]}
    imports = [result for result in results if result["call_id"] in dispatchable]
    return bridge.import_results(run_id, imports) if imports else []


def validate_workflow_package(root: Path) -> None:
    """Check packaging and JSON schemas. This does not execute Claude Code's Workflow tool."""
    path = root / "plugins/autofusion/workflows/dual-review.js"
    script = path.read_text(encoding="utf-8")
    prefix = "export const meta = "
    _require(script.startswith(prefix), "Workflow must start with a literal meta export")
    try:
        meta, end = json.JSONDecoder().raw_decode(script[len(prefix):])
    except json.JSONDecodeError as error:
        raise PolicyError("Workflow meta must be a JSON object literal") from error
    _require(isinstance(meta, dict) and meta.get("name") == "dual-review",
             "Workflow metadata must identify dual-review")
    _require(script[len(prefix) + end:].startswith(";"), "Workflow meta must be literal")
    for name, field in ((INPUT_SCHEMA, "inputSchema"), (OUTPUT_SCHEMA, "outputSchema")):
        schema = load_response_schema(name)
        Draft202012Validator.check_schema(schema)
        marker = f"const {field} = "
        _require(marker in script, f"Workflow is missing {field}")
        embedded, _ = json.JSONDecoder().raw_decode(script.split(marker, 1)[1])
        _require(embedded == schema, f"Workflow {field} does not match packaged schema")
        _require(json.loads((root / "schemas" / name).read_text(encoding="utf-8")) == schema,
                 f"Workflow schema parity failed: {name}")
    input_schema = load_response_schema(INPUT_SCHEMA)
    output_schema = load_response_schema(OUTPUT_SCHEMA)
    _require(input_schema["properties"]["requests"]["items"]
             == load_response_schema("session-request.schema.json"),
             "Workflow input must embed the current session request schema")
    _require(output_schema["properties"]["results"]["items"]["properties"]["result"]["anyOf"][1]
             == load_response_schema("session-result.schema.json"),
             "Workflow output must embed the current session result schema")
    for token in ("Date.now", "process.", "require(", "import ", "fetch(", "eval("):
        _require(token not in script, f"Workflow uses an unavailable runtime API: {token}")
    _require("agent(" in script and "parallel(" in script,
             "Workflow must dispatch blind first passes")
    # Canonical encoding also rejects nonfinite literals in the published metadata.
    canonical_json_bytes(meta)
