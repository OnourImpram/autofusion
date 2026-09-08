from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from autofusion.errors import AutofusionError
from autofusion.util import JsonObject, sha256_json


def _bundle() -> JsonObject:
    from autofusion.models import Packet, SnapshotManifest
    from autofusion.prompts import load_response_schema, review_prompt

    schema = load_response_schema("reviewer-output.schema.json")
    manifest: JsonObject = {"snapshot_hash": "a" * 64, "manifest_hash": "b" * 64,
                            "entries": [{"path": "app.py", "sha256": "c" * 64, "size": 10}]}
    packet = {"artifact": "VALUE = 1", "snapshot": manifest}
    frozen = Packet(packet, sha256_json(packet), SnapshotManifest(
        root=Path("."), source_root=Path("."), snapshot_hash=manifest["snapshot_hash"],
        manifest_hash=manifest["manifest_hash"], entries=tuple(manifest["entries"]),
    ))
    requests = []
    for call, handle, model, family, delegate in (
        ("call-" + "a" * 16, "gpt-sol", "gpt-5.6-sol", "gpt-5.6", "sol-delege"),
        ("call-" + "b" * 16, "astra-ultra", "gpt-6-astra", "gpt-6", "astra-delege"),
    ):
        requests.append({
            "schema_version": "1", "run_id": "run-workflow", "call_id": call,
            "packet_hash": sha256_json(packet), "packet": packet,
            "artifact_manifest": manifest, "artifact_manifest_hash": sha256_json(manifest),
            "reviewer_schema_id": schema["$id"], "reviewer_schema_hash": sha256_json(schema),
            "reviewer_schema": schema, "role": "reviewer", "dispatchable": True,
            "requested_identity": {"handle": handle, "model": model, "vendor": "openai",
                                   "family": family, "delegate": delegate},
            "prompt": review_prompt(frozen, role="reviewer"), "max_output_chars": 120000,
        })
    return {"schema_version": "1", "run_id": "run-workflow", "topology": "dual-review",
            "requests": requests}


def _result(request: JsonObject) -> JsonObject:
    result = {key: request[key] for key in (
        "schema_version", "run_id", "call_id", "packet_hash", "artifact_manifest_hash",
        "reviewer_schema_id", "reviewer_schema_hash",
    )}
    result.update({
        "status": "completed", "actual_identity": {
            key: request["requested_identity"][key]
            for key in ("model", "vendor", "family", "delegate")
        }, "error": None,
        "output": {"summary": "Reviewed", "coverage": ["artifact"], "blind_spots": [],
                   "recommendation": "ship", "findings": []},
    })
    return result


def _output(bundle: JsonObject) -> JsonObject:
    return {"schema_version": "1", "run_id": bundle["run_id"], "topology": "dual-review",
            "results": [{"call_id": request["call_id"], "result": _result(request)}
                        for request in bundle["requests"]]}


def test_workflow_schemas_and_literal_metadata_match_packaged_contract() -> None:
    from autofusion.workflow_bridge import validate_workflow_package

    validate_workflow_package(Path(__file__).resolve().parents[1])


def test_workflow_validates_same_packet_dispatch_and_output() -> None:
    from autofusion.workflow_bridge import validate_dual_review_input, validate_dual_review_output

    bundle = _bundle()
    validate_dual_review_input(bundle)
    results = validate_dual_review_output(bundle, _output(bundle))
    assert [result["call_id"] for result in results] == ["call-" + "a" * 16, "call-" + "b" * 16]
    bundle["requests"][1]["packet"] = {
        "artifact": "different", "snapshot": bundle["requests"][1]["artifact_manifest"],
    }
    bundle["requests"][1]["packet_hash"] = sha256_json(bundle["requests"][1]["packet"])
    with pytest.raises(AutofusionError):
        validate_dual_review_input(bundle)


def test_workflow_null_result_retains_incomplete_call() -> None:
    from autofusion.workflow_bridge import validate_dual_review_output

    bundle = _bundle()
    output = _output(bundle)
    output["results"][1]["result"] = None
    results = validate_dual_review_output(bundle, output)
    assert results[1]["call_id"] == "call-" + "b" * 16
    assert results[1]["status"] == "cancelled"
    assert results[1]["output"] is None
    assert results[1]["actual_identity"] is None
    assert "null" in results[1]["error"]


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "claude-fable-5-1", "claude-fable-5"])
def test_workflow_rejects_forced_override_or_inherited_model(model: str) -> None:
    from autofusion.workflow_bridge import validate_dual_review_output

    bundle = _bundle()
    output = _output(bundle)
    output["results"][1]["result"]["actual_identity"]["model"] = model
    with pytest.raises(AutofusionError):
        validate_dual_review_output(bundle, output)


@pytest.mark.parametrize("field", ["call_id", "packet_hash", "reviewer_schema_hash",
                                   "artifact_manifest_hash", "run_id"])
def test_workflow_rejects_result_correlation_changes(field: str) -> None:
    from autofusion.workflow_bridge import validate_dual_review_output

    bundle = _bundle()
    output = _output(bundle)
    output["results"][1]["result"][field] = "e" * 64
    with pytest.raises(AutofusionError):
        validate_dual_review_output(bundle, output)


def test_workflow_rejects_duplicate_call_and_partial_wrapper() -> None:
    from autofusion.workflow_bridge import validate_dual_review_output

    bundle = _bundle()
    output = _output(bundle)
    output["results"][1] = copy.deepcopy(output["results"][0])
    with pytest.raises(AutofusionError):
        validate_dual_review_output(bundle, output)
    output["results"].pop()
    with pytest.raises(AutofusionError):
        validate_dual_review_output(bundle, output)


def test_workflow_replay_rejected_by_durable_batch_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autofusion.config import load_config
    from autofusion.engine import FusionEngine, FusionRunRequest
    from autofusion.registry import ProviderRegistry
    from autofusion.session_bridge import SessionBridge
    from autofusion.workflow_bridge import import_dual_review_output

    monkeypatch.setenv("AUTOFUSION_GLOBAL_CONFIG", str(tmp_path / "absent.json"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    overlay = tmp_path / "overlay.json"
    overlay.write_text(json.dumps({"panels": {"dual-session": {
        "topology": "dual-review", "drafter": "self", "reviewers": ["gpt-sol", "astra-ultra"],
    }}}), encoding="utf-8")
    config = load_config(explicit_path=overlay)
    engine = FusionEngine(config, ProviderRegistry({}), work_root=tmp_path / "work")
    bridge = SessionBridge(engine)
    bundle = bridge.export(FusionRunRequest(
        task="Review", repo_root=repo, artifact_kind="plan", artifact_paths=("app.py",),
        panel="dual-session", self_model="claude-fable-5-1", run_grounding=False,
    ), {"gpt-sol": "sol-delege", "astra-ultra": "astra-delege"})
    output = _output(bundle)
    override = copy.deepcopy(output)
    override["results"][1]["result"]["actual_identity"]["model"] = "gpt-5.6-sol"
    with pytest.raises(AutofusionError):
        import_dual_review_output(bridge, bundle["run_id"], override)
    # Rejection must not consume the valid left response.
    import_dual_review_output(bridge, bundle["run_id"], output)
    with pytest.raises(AutofusionError):
        import_dual_review_output(bridge, bundle["run_id"], output)
