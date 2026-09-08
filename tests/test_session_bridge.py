from __future__ import annotations

import copy
import json
import subprocess
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from autofusion.config import load_config
from autofusion.engine import FusionEngine, FusionRunRequest
from autofusion.errors import PolicyError, ReceiptError, SnapshotError
from autofusion.evidence import EvidenceLedger
from autofusion.grounding import RunnerOutput
from autofusion.reconcile import FindingDisposition
from autofusion.registry import ProviderRegistry
from autofusion.session_bridge import SessionBridge, validate_session_request
from autofusion.util import JsonObject


def bridge_fixture(tmp_path: Path, *, dual: bool = False) -> tuple[SessionBridge, FusionRunRequest]:
    config = load_config()
    if dual:
        config.data["panels"]["bridge-dual"] = {
            "topology": "dual-review",
            "drafter": "self",
            "reviewers": ["gpt-sol", "astra-ultra"],
        }
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    engine = FusionEngine(config, ProviderRegistry({}), work_root=tmp_path / "work")
    return SessionBridge(engine), FusionRunRequest(
        task="Review app.py",
        repo_root=repo,
        artifact_kind="plan",
        artifact_paths=("app.py",),
        preset="fast",
        panel="bridge-dual" if dual else None,
        self_model="claude-fable-5-1",
        run_grounding=False,
    )


def response(request: JsonObject, *, status: str = "completed") -> JsonObject:
    return {
        **{
            key: request[key]
            for key in (
                "schema_version",
                "run_id",
                "call_id",
                "packet_hash",
                "artifact_manifest_hash",
                "reviewer_schema_id",
                "reviewer_schema_hash",
            )
        },
        "status": status,
        "actual_identity": {
            key: request["requested_identity"][key]
            for key in ("model", "vendor", "family", "delegate")
        }
        if status in {"completed", "abstaining"}
        else None,
        "output": {
            "summary": "review complete",
            "coverage": ["app.py"],
            "blind_spots": [],
            "recommendation": "abstain" if status == "abstaining" else "ship",
            "findings": [],
        }
        if status in {"completed", "abstaining"}
        else None,
        "error": None,
    }


def test_bridge_enters_existing_analysis_evidence_and_finalize(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path)
    bundle = bridge.export(run, {"gpt-sol": "sol-delege"})
    req = bundle["requests"][0]
    validate_session_request(req)
    assert req["packet"]["artifact_contents"]["app.py"]["content"] == "VALUE = 1\n"
    assert req["requested_identity"]["delegate"] == "sol-delege"
    bridge.import_result(bundle["run_id"], response(req))
    resumed = SessionBridge(bridge.engine)
    pending = resumed.complete(bundle["run_id"])
    assert pending.requires_reconciliation
    kinds = [record.kind for record in EvidenceLedger(pending.evidence_path).verify()]
    assert "provider-routing" in kinds and "pending-state" in kinds
    artifacts = bridge.engine.finalize(bundle["run_id"], dispositions=())
    assert artifacts.receipt["fused"] is True
    assert artifacts.receipt["self_model"] == "claude-fable-5-1"


def test_missing_delegate_remains_missing_and_degrades(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path)
    bundle = bridge.export(run, {})
    assert bundle["requests"][0]["dispatchable"] is False
    pending = bridge.complete(bundle["run_id"])
    assert pending.degraded
    result = json.loads(pending.result_path.read_text())
    assert result["outputs"][0]["bridge_status"] == "missing"


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "call_id",
        "packet_hash",
        "artifact_manifest_hash",
        "reviewer_schema_hash",
        "reviewer_schema_id",
    ],
)
def test_mismatched_correlation_rejected_without_consuming_call(tmp_path: Path, field: str) -> None:
    bridge, run = bridge_fixture(tmp_path)
    bundle = bridge.export(run, {"gpt-sol": "sol-delege"})
    good = response(bundle["requests"][0])
    bad = {**good, field: "wrong"}
    with pytest.raises(ReceiptError):
        bridge.import_result(bundle["run_id"], bad)
    bridge.import_result(bundle["run_id"], good)
    assert not bridge.complete(bundle["run_id"]).degraded


def test_duplicate_and_replayed_result_rejected(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path)
    bundle = bridge.export(run, {"gpt-sol": "sol-delege"})
    result = response(bundle["requests"][0])
    bridge.import_result(bundle["run_id"], result)
    with pytest.raises(ReceiptError, match="outstanding"):
        bridge.import_result(bundle["run_id"], result)
    pending = bridge.complete(bundle["run_id"])
    assert pending.requires_reconciliation
    with pytest.raises(ReceiptError):
        bridge.import_result(bundle["run_id"], result)
    with pytest.raises(ReceiptError):
        bridge.complete(bundle["run_id"])


def test_concurrent_calls_keep_correlation(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path, dual=True)
    bundle = bridge.export(run, {"gpt-sol": "sol-delege", "astra-ultra": "astra-delege"})
    requests = bundle["requests"]
    assert requests[0]["packet_hash"] == requests[1]["packet_hash"]
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(
            pool.map(
                lambda req: SessionBridge(bridge.engine).import_result(
                    bundle["run_id"], response(req)
                ),
                requests,
            )
        )
    assert len(outcomes) == 2
    assert not bridge.complete(bundle["run_id"]).degraded


@pytest.mark.parametrize("status", ["missing", "failed", "cancelled", "malformed", "abstaining"])
def test_incomplete_statuses_survive(tmp_path: Path, status: str) -> None:
    bridge, run = bridge_fixture(tmp_path)
    bundle = bridge.export(run, {"gpt-sol": "sol-delege"})
    bridge.import_result(bundle["run_id"], response(bundle["requests"][0], status=status))
    pending = bridge.complete(bundle["run_id"])
    result = json.loads(pending.result_path.read_text())
    assert result["outputs"][0]["bridge_status"] == status
    analysis = json.loads(pending.analysis_path.read_text())
    assert analysis["context_complete"] is False


def test_partial_response_is_malformed_not_empty_success(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path)
    bundle = bridge.export(run, {"gpt-sol": "sol-delege"})
    result = response(bundle["requests"][0])
    result["output"] = {"summary": "cut off"}
    imported = bridge.import_result(bundle["run_id"], result)
    assert imported["status"] == "malformed"
    assert bridge.complete(bundle["run_id"]).degraded
    artifacts = bridge.engine.finalize(bundle["run_id"], dispositions=None, automatic=True)
    assert artifacts.receipt["verdict"] == "degraded"
    assert artifacts.receipt["calls"][0]["identity_evidence"] == "unavailable"


def test_outstanding_call_becomes_missing_on_complete(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path)
    bundle = bridge.export(run, {"gpt-sol": "sol-delege"})
    pending = bridge.complete(bundle["run_id"])
    assert pending.degraded
    assert json.loads(pending.result_path.read_text())["outputs"][0]["bridge_status"] == "missing"


@pytest.mark.parametrize("model", ["claude-fable-5", "claude-fable-5-1", "gpt-6-astra"])
def test_returned_fable_or_forced_override_rejected(tmp_path: Path, model: str) -> None:
    bridge, run = bridge_fixture(tmp_path)
    bundle = bridge.export(run, {"gpt-sol": "sol-delege"})
    result = response(bundle["requests"][0])
    result["actual_identity"]["model"] = model
    with pytest.raises(ReceiptError):
        bridge.import_result(bundle["run_id"], result)


def test_requested_fable_delegate_rejected(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path)
    with pytest.raises(PolicyError):
        bridge.export(run, {"gpt-sol": "fable-delege"})


def test_state_tampering_and_stale_artifact_rejected(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path)
    bundle = bridge.export(run, {"gpt-sol": "sol-delege"})
    result = response(bundle["requests"][0])
    (run.repo_root / "app.py").write_text("VALUE = 2\n")
    with pytest.raises(SnapshotError):
        bridge.import_result(bundle["run_id"], result)
    (run.repo_root / "app.py").write_text("VALUE = 1\n", newline="\n")
    path = bridge.engine.work_root / bundle["run_id"] / "session-bridge.json"
    raw = json.loads(path.read_text())
    raw["requests"][0]["packet_hash"] = "0" * 64
    path.write_text(json.dumps(raw))
    with pytest.raises(ReceiptError, match="bound"):
        bridge.import_result(bundle["run_id"], result)


def test_dlp_and_hard_cost_admission_prevent_export(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path)
    bridge.engine.config.data["guardrails"]["max_cost_usd"] = 1
    with pytest.raises(PolicyError, match="cost"):
        bridge.export(run, {"gpt-sol": "sol-delege"})
    bridge.engine.config.data["guardrails"]["max_cost_usd"] = None
    (run.repo_root / "app.py").write_text('KEY = "sk-proj-' + "A" * 64 + '"\n')
    with pytest.raises(PolicyError, match="secret"):
        bridge.export(run, {"gpt-sol": "sol-delege"})


def test_request_validation_rejects_tampered_packet(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path)
    bundle = bridge.export(run, {"gpt-sol": "sol-delege"})
    bad = copy.deepcopy(bundle["requests"][0])
    bad["packet"]["task"] = "different task"
    with pytest.raises(ReceiptError):
        validate_session_request(bad)


def test_batch_rejection_does_not_consume_first_valid_response(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path, dual=True)
    bundle = bridge.export(run, {"gpt-sol": "sol-delege", "astra-ultra": "astra-delege"})
    results = [response(req) for req in bundle["requests"]]
    wrong = copy.deepcopy(results)
    wrong[1]["actual_identity"] = wrong[0]["actual_identity"]
    with pytest.raises(ReceiptError, match="identity"):
        bridge.import_results(bundle["run_id"], wrong)
    assert len(bridge.import_results(bundle["run_id"], results)) == 2
    assert not bridge.complete(bundle["run_id"]).degraded


def test_cross_process_duplicate_consumes_outstanding_call_once(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path)
    bundle = bridge.export(run, {"gpt-sol": "sol-delege"})
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(response(bundle["requests"][0])))
    script = """
import json, sys
from pathlib import Path
from autofusion.config import load_config
from autofusion.engine import FusionEngine
from autofusion.errors import ReceiptError
from autofusion.registry import ProviderRegistry
from autofusion.session_bridge import SessionBridge
engine = FusionEngine(load_config(), ProviderRegistry({}), work_root=Path(sys.argv[1]))
bridge = SessionBridge(engine)
try:
    bridge.import_result(sys.argv[2], json.loads(Path(sys.argv[3]).read_text()))
except ReceiptError:
    sys.exit(3)
"""
    argv = [
        sys.executable,
        "-c",
        script,
        str(bridge.engine.work_root),
        bundle["run_id"],
        str(result_path),
    ]
    children = [
        subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)
    ]
    try:
        for child in children:
            stdout, stderr = child.communicate(timeout=15)
            assert not stdout and not stderr
        assert sorted(child.returncode for child in children) == [0, 3]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
    assert not bridge.complete(bundle["run_id"]).degraded


def test_concurrent_runs_cannot_exchange_result_envelopes(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path)
    first = bridge.export(run, {"gpt-sol": "sol-delege"})
    second = bridge.export(run, {"gpt-sol": "sol-delege"})
    with pytest.raises(ReceiptError):
        bridge.import_result(second["run_id"], response(first["requests"][0]))
    for bundle in (first, second):
        bridge.import_result(bundle["run_id"], response(bundle["requests"][0]))
        assert not bridge.complete(bundle["run_id"]).degraded


def test_accepted_result_file_cannot_change_before_completion(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path)
    bundle = bridge.export(run, {"gpt-sol": "sol-delege"})
    bridge.import_result(bundle["run_id"], response(bundle["requests"][0]))
    directory = bridge.engine.work_root / bundle["run_id"] / "session-results"
    accepted = next(directory.glob("*.json"))
    accepted.write_text('{"results": []}')
    with pytest.raises(ReceiptError, match="bytes changed"):
        bridge.complete(bundle["run_id"])


def test_policy_and_artifact_freshness_rechecked_at_completion(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path)
    bundle = bridge.export(run, {"gpt-sol": "sol-delege"})
    bridge.import_result(bundle["run_id"], response(bundle["requests"][0]))
    bridge.engine.config.data["guardrails"]["max_calls_per_run"] += 1
    with pytest.raises(ReceiptError, match="policy"):
        bridge.complete(bundle["run_id"])
    bridge.engine.config.data["guardrails"]["max_calls_per_run"] -= 1
    (run.repo_root / "app.py").write_text("VALUE = 99\n")
    with pytest.raises(SnapshotError):
        bridge.complete(bundle["run_id"])


def test_context_and_call_budgets_prevent_export(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path, dual=True)
    bridge.engine.config.data["models"]["gpt-sol"]["capabilities"]["context_window_tokens"] = 1
    with pytest.raises(PolicyError, match="context fit"):
        bridge.export(run, {"gpt-sol": "sol-delege", "astra-ultra": "astra-delege"})
    bridge.engine.config.data["models"]["gpt-sol"]["capabilities"]["context_window_tokens"] = (
        1050000
    )
    bridge.engine.config.data["guardrails"]["max_calls_per_run"] = 1
    from autofusion.errors import BudgetExceeded

    with pytest.raises(BudgetExceeded, match="call budget"):
        bridge.export(run, {"gpt-sol": "sol-delege", "astra-ultra": "astra-delege"})


def test_completed_output_without_actual_identity_is_malformed(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path)
    bundle = bridge.export(run, {"gpt-sol": "sol-delege"})
    result = response(bundle["requests"][0])
    result["actual_identity"] = None
    assert bridge.import_result(bundle["run_id"], result)["status"] == "malformed"
    assert bridge.complete(bundle["run_id"]).degraded


class _GroundingRunner:
    network_denied = True

    def run(self, argv: Sequence[str], cwd: Path, timeout_s: float) -> RunnerOutput:
        assert argv == ("pytest", "-q")
        assert cwd.is_dir() and timeout_s > 0
        return RunnerOutput(exit_code=1, stderr="AssertionError: bridge regression")


def test_bridge_findings_use_existing_grounding_and_reconciliation(tmp_path: Path) -> None:
    bridge, run = bridge_fixture(tmp_path)
    bridge.engine.config.data["verification"]["profiles"]["python"]["commands"]["pytest"] = {
        "argv": ["pytest", "-q"],
        "timeout_s": 30,
        "kind": "dynamic",
        "expected_failure": "AssertionError: bridge regression",
    }
    bridge.engine.grounding_runner = _GroundingRunner()
    bundle = bridge.export(
        replace(run, artifact_kind="diff", run_grounding=True), {"gpt-sol": "sol-delege"}
    )
    result = response(bundle["requests"][0])
    result["output"]["recommendation"] = "revise"
    result["output"]["findings"] = [
        {
            "severity": "major",
            "category": "correctness",
            "claim": "Seeded regression",
            "evidence": {"summary": "app.py defect", "strength": "artifact-cited"},
            "suggested_fix": "Correct the condition",
            "checkable": True,
            "verification_id": "python.pytest",
            "stance_key": "bridge-regression",
            "stance": "supports",
        }
    ]
    bridge.import_result(bundle["run_id"], result)
    pending = bridge.complete(bundle["run_id"])
    analysis = json.loads(pending.analysis_path.read_text())
    assert analysis["grounding_results"][0]["verdict"] == "confirmed"
    finding_id = analysis["findings"][0]["id"]
    with pytest.raises(PolicyError, match="cannot be rejected"):
        bridge.engine.finalize(
            bundle["run_id"],
            dispositions=(FindingDisposition(finding_id, "rejected", "ignore seeded failure"),),
        )
