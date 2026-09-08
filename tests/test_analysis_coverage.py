from __future__ import annotations

import copy
import json
import runpy
from pathlib import Path

import pytest

from autofusion.analysis import AnalysisInput, build_analysis
from autofusion.config import load_config
from autofusion.contracts import validate_analysis, validate_linked, validate_linked_paths
from autofusion.engine import FusionEngine, FusionRunRequest, PendingRun
from autofusion.errors import ReceiptError
from autofusion.models import CallStatus, ProviderResult
from autofusion.providers.base import SelfProvider
from autofusion.providers.fake import DeterministicFakeProvider
from autofusion.reconcile import FindingDisposition, reconcile_analysis
from autofusion.registry import ProviderRegistry
from autofusion.util import JsonObject, sha256_bytes, sha256_json


def _review(
    recommendation: str = "ship",
    coverage: tuple[str, ...] = ("artifact",),
    blind_spots: tuple[str, ...] = (),
) -> JsonObject:
    return {
        "summary": "controlled review",
        "coverage": list(coverage),
        "blind_spots": list(blind_spots),
        "recommendation": recommendation,
        "findings": [],
    }


def _input(
    handle: str, output: JsonObject, *, context_complete: bool = True
) -> AnalysisInput:
    profile = load_config().model(handle)
    return AnalysisInput(
        ProviderResult(
            call_id=f"call-{handle}",
            handle=handle,
            requested_model=profile.model,
            effective_model=profile.canonical_model,
            vendor=profile.vendor,
            family=profile.family,
            mode=profile.effort,
            compound=profile.compound,
            worker_visibility=profile.worker_visibility,
            status=CallStatus.COMPLETED,
            duration_ms=1,
            output_text="",
            structured_output=output,
            output_hash=sha256_json(output),
        ),
        context_complete=context_complete,
    )


def _analysis(*inputs: AnalysisInput) -> JsonObject:
    return build_analysis(
        run_id="coverage-run",
        packet_hash="a" * 64,
        panel="default",
        topology="review",
        inputs=inputs,
        required_handles=tuple(item.source_id for item in inputs),
    )


@pytest.mark.parametrize(
    ("recommendation", "coverage", "blind_spots", "verdict", "complete"),
    [
        ("abstain", ("artifact",), (), "blocked", False),
        ("abstain", (), ("mandatory artifact missing",), "blocked", False),
        ("ship", (), (), "blocked", False),
        ("ship", ("  ",), (), "blocked", False),
        ("ship", ("artifact",), ("mandatory dependency missing",), "blocked", False),
        ("block", ("artifact",), (), "blocked", True),
        ("revise", ("artifact",), (), "blocked", True),
        ("ship", ("artifact",), (), "ship", True),
    ],
)
def test_reviewer_outcomes_survive_finalization_and_receipt_linkage(
    tmp_path: Path,
    recommendation: str,
    coverage: tuple[str, ...],
    blind_spots: tuple[str, ...],
    verdict: str,
    complete: bool,
) -> None:
    config = load_config()
    output = _review(recommendation, coverage, blind_spots)
    registry = ProviderRegistry(
        {
            "self": SelfProvider(config.model("self")),
            "gpt-sol": DeterministicFakeProvider(config.model("gpt-sol"), output),
        }
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    engine = FusionEngine(config, registry, work_root=tmp_path / "work")
    pending = engine.run(
        FusionRunRequest(
            task="Review the artifact",
            repo_root=repo,
            artifact_kind="diff",
            artifact_paths=("app.py",),
            preset="fast",
            self_model="claude-opus-4-8",
            run_grounding=False,
        )
    )
    assert isinstance(pending, PendingRun)
    artifacts = engine.finalize(pending.run_id, dispositions=None, automatic=True)
    assert artifacts.receipt["verdict"] == verdict
    assert artifacts.receipt["context_complete"] is complete
    assert artifacts.receipt["fused"] is complete
    participant = artifacts.analysis["participants"][1]
    assert participant["status"] == "completed"
    for name in ("coverage", "blind_spots", "recommendation"):
        assert participant[name] == output[name]
    assert artifacts.receipt["calls"][0]["status"] == "completed"
    persisted = json.loads(artifacts.analysis_path.read_text(encoding="utf-8"))
    assert persisted["participants"][1] == participant
    validate_linked_paths(artifacts.receipt_path, artifacts.analysis_path, config)


def test_analysis_retains_coverage_by_source_and_reported_blind_spots() -> None:
    analysis = _analysis(
        _input("gpt-sol", _review(coverage=("artifact", "tests"))),
        _input(
            "claude-opus",
            _review(coverage=("artifact",), blind_spots=("mandatory deployment context",)),
        ),
    )
    areas = {item["area"]: item for item in analysis["partial_coverage"]}
    assert areas["artifact"]["covered_by"] == ["gpt-sol", "claude-opus"]
    assert areas["artifact"]["missing"] == ""
    assert areas["tests"]["covered_by"] == ["gpt-sol"]
    assert "claude-opus" in areas["tests"]["missing"]
    assert any(
        "mandatory deployment context" in item["reason"] and "claude-opus" in item["reason"]
        for item in analysis["blind_spots"]
    )
    assert analysis["context_complete"] is False
    validate_analysis(analysis)
    reconciled = reconcile_analysis(analysis, ())
    assert reconciled["partial_coverage"] == analysis["partial_coverage"]
    assert reconciled["blind_spots"] == analysis["blind_spots"]
    assert reconciled["decision_impact"]["effect"] == "human-required"


def test_input_context_failure_is_retained_even_for_completed_review() -> None:
    analysis = _analysis(_input("gpt-sol", _review(), context_complete=False))
    assert analysis["participants"][0]["context_complete"] is False
    assert analysis["context_complete"] is False
    validate_analysis(analysis)


def test_abstaining_reviewer_keeps_its_partial_finding_and_provenance() -> None:
    output = _review("abstain", blind_spots=("mandatory integration context missing",))
    output["findings"] = [{"severity": "minor", "claim": "check the visible branch"}]
    analysis = _analysis(_input("gpt-sol", output))
    assert analysis["context_complete"] is False
    assert analysis["findings"][0]["source_ids"] == ["gpt-sol"]
    assert analysis["findings"][0]["claim"] == "check the visible branch"
    finding_id = analysis["findings"][0]["id"]
    reconciled = reconcile_analysis(
        analysis, (FindingDisposition(finding_id, "accepted", "investigate the partial finding"),)
    )
    assert reconciled["decision_impact"]["effect"] == "human-required"
    validate_analysis(reconciled)


@pytest.mark.parametrize("output", [{"findings": []}, {"draft": "candidate"}, {"winner": "left"}])
def test_nonreview_outputs_remain_compatible_without_reviewer_metadata(output: JsonObject) -> None:
    analysis = _analysis(_input("gpt-sol", output))
    assert analysis["context_complete"] is True
    assert "recommendation" not in analysis["participants"][0]
    validate_analysis(analysis)


@pytest.mark.parametrize("recommendation", ["block", "revise"])
def test_unexplained_recommendation_is_not_erased_by_another_reviewers_finding(
    recommendation: str,
) -> None:
    output = _review()
    output["findings"] = [{"severity": "minor", "claim": "cosmetic concern"}]
    analysis = _analysis(
        _input("gpt-sol", _review(recommendation)), _input("claude-opus", output)
    )
    finding_id = analysis["findings"][0]["id"]
    reconciled = reconcile_analysis(
        analysis, (FindingDisposition(finding_id, "waived", "cosmetic concern waived"),)
    )
    assert reconciled["decision_impact"]["effect"] == "human-required"


def test_explicit_disposition_can_settle_a_recommendation_with_its_finding() -> None:
    output = _review("revise")
    output["findings"] = [{"severity": "minor", "claim": "cosmetic concern"}]
    analysis = _analysis(_input("gpt-sol", output))
    finding_id = analysis["findings"][0]["id"]
    reconciled = reconcile_analysis(
        analysis, (FindingDisposition(finding_id, "waived", "cosmetic concern waived"),)
    )
    assert reconciled["decision_impact"]["effect"] == "clarified"


@pytest.mark.parametrize("partial_disposition", [False, True])
def test_partial_reconciliation_cannot_erase_unsettled_recommendation(
    partial_disposition: bool,
) -> None:
    output = _review("revise")
    output["findings"] = [
        {"severity": "minor", "claim": "first concern"},
        {"severity": "minor", "claim": "second concern"},
    ]
    analysis = _analysis(_input("gpt-sol", output))
    dispositions = (
        (FindingDisposition(analysis["findings"][0]["id"], "waived", "first concern settled"),)
        if partial_disposition else ()
    )
    reconciled = reconcile_analysis(analysis, dispositions, require_all=False)
    assert reconciled["findings"][1]["status"] == "proposed"
    assert reconciled["decision_impact"]["effect"] == "human-required"
    validate_analysis(reconciled)


def test_runtime_validator_rejects_inflated_reviewer_quorum() -> None:
    analysis = _analysis(_input("gpt-sol", _review("abstain")))
    analysis["context_complete"] = True
    analysis["decision_impact"]["effect"] = "none"
    with pytest.raises(ReceiptError, match="context_complete"):
        validate_analysis(analysis)


@pytest.mark.parametrize("unsettled_finding", [False, True])
def test_runtime_validator_rejects_erased_unexplained_recommendation(
    unsettled_finding: bool,
) -> None:
    output = _review("block")
    if unsettled_finding:
        output["findings"] = [{"severity": "minor", "claim": "unsettled concern"}]
    analysis = _analysis(_input("gpt-sol", output))
    analysis["decision_impact"]["effect"] = "none"
    with pytest.raises(ReceiptError, match="recommendation"):
        validate_analysis(analysis)


def test_linked_validator_rejects_context_flag_mismatch() -> None:
    fixtures = Path(__file__).parent / "fixtures"
    analysis_path = fixtures / "analysis-valid.json"
    analysis_bytes = analysis_path.read_bytes()
    analysis = json.loads(analysis_bytes)
    receipt = json.loads((fixtures / "receipt-valid.json").read_bytes())
    receipt.update(context_complete=False, fused=False, verdict="blocked", state="escalated")
    receipt["analysis_hash"] = sha256_bytes(analysis_bytes)
    with pytest.raises(ReceiptError, match="context_complete"):
        validate_linked(receipt, analysis, analysis_bytes, load_config())


def test_static_linked_validator_rejects_context_flag_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    validator = runpy.run_path(str(root / "scripts" / "validate_linked_run.py"))
    analysis_path = root / "tests" / "fixtures" / "analysis-valid.json"
    analysis_bytes = analysis_path.read_bytes()
    analysis = json.loads(analysis_bytes)
    receipt = json.loads((root / "tests" / "fixtures" / "receipt-valid.json").read_bytes())
    receipt["analysis_hash"] = sha256_bytes(analysis_bytes)
    validator["validate_linked_run"](receipt, analysis, analysis_path)
    receipt["context_complete"] = False
    with pytest.raises(validator["LinkedRunError"], match="context_complete"):
        validator["validate_linked_run"](receipt, analysis, analysis_path)


def test_static_validator_accepts_preserved_review_gaps_and_rejects_inflation() -> None:
    root = Path(__file__).resolve().parents[1]
    validator = runpy.run_path(str(root / "scripts" / "validate_analysis_instances.py"))
    analysis = json.loads((root / "tests" / "fixtures" / "analysis-valid.json").read_bytes())
    schema = json.loads((root / "schemas" / "fusion-analysis.schema.json").read_bytes())
    config = json.loads((root / ".fusion.example.json").read_bytes())
    analysis["participants"][1].update(
        coverage=[], blind_spots=["mandatory artifact missing"], recommendation="abstain",
        context_complete=False,
    )
    analysis["context_complete"] = False
    analysis["decision_impact"]["effect"] = "human-required"
    validator["validate_analysis"](analysis, schema, config)
    inflated = copy.deepcopy(analysis)
    inflated["context_complete"] = True
    with pytest.raises(validator["AnalysisError"], match="context_complete"):
        validator["validate_analysis"](inflated, schema, config)


@pytest.mark.parametrize("unsettled_finding", [False, True])
def test_static_validator_rejects_erased_unexplained_recommendation(
    unsettled_finding: bool,
) -> None:
    root = Path(__file__).resolve().parents[1]
    validator = runpy.run_path(str(root / "scripts" / "validate_analysis_instances.py"))
    analysis = json.loads((root / "tests" / "fixtures" / "analysis-valid.json").read_bytes())
    schema = json.loads((root / "schemas" / "fusion-analysis.schema.json").read_bytes())
    config = json.loads((root / ".fusion.example.json").read_bytes())
    analysis["participants"][1].update(
        coverage=["artifact"], blind_spots=[], recommendation="block", context_complete=True,
    )
    if unsettled_finding:
        for finding in analysis["findings"]:
            finding.update(
                severity="minor", status="proposed", checkable=False, verification_id=None
            )
            finding["evidence"]["strength"] = "artifact-cited"
    else:
        analysis["findings"] = []
    for key in ("grounding_candidates", "grounding_results", "unique_insights"):
        analysis[key] = []
    analysis["decision_impact"]["effect"] = "human-required"
    validator["validate_analysis"](analysis, schema, config)
    analysis["decision_impact"]["effect"] = "none"
    with pytest.raises(validator["AnalysisError"], match="recommendation"):
        validator["validate_analysis"](analysis, schema, config)
