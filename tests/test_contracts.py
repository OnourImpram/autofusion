from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autofusion.config import load_config
from autofusion.contracts import validate_analysis, validate_linked
from autofusion.errors import ReceiptError
from autofusion.receipt import ReceiptStore
from autofusion.util import canonical_json_bytes, sha256_bytes

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> dict[str, Any]:
    value: object = json.loads((ROOT / "tests" / "fixtures" / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return dict(value)


def _linked() -> tuple[dict[str, Any], dict[str, Any], bytes]:
    analysis = _load("analysis-valid.json")
    receipt = _load("receipt-valid.json")
    analysis_bytes = canonical_json_bytes(analysis) + b"\n"
    receipt["analysis_hash"] = sha256_bytes(analysis_bytes)
    return receipt, analysis, analysis_bytes


def test_valid_linked_artifacts() -> None:
    receipt, analysis, analysis_bytes = _linked()
    validate_linked(receipt, analysis, analysis_bytes, load_config())


def test_single_source_consensus_is_rejected() -> None:
    _, analysis, _ = _linked()
    analysis["consensus"][0]["supporters"] = ["gpt-sol"]
    with pytest.raises(ReceiptError, match=r"too short|at least two"):
        validate_analysis(analysis)


def test_ship_with_major_finding_is_rejected() -> None:
    receipt, analysis, _ = _linked()
    analysis["findings"][0]["severity"] = "major"
    analysis["grounding_candidates"] = []
    analysis["grounding_results"] = []
    analysis["findings"][0]["status"] = "proposed"
    analysis_bytes = canonical_json_bytes(analysis) + b"\n"
    receipt["analysis_hash"] = sha256_bytes(analysis_bytes)
    receipt["findings"] = {
        "blocker": 0,
        "major": 1,
        "minor": 0,
        "confirmed_by_exec": 0,
        "confirmed_by_proof": 0,
        "deadlocks": 0,
    }
    with pytest.raises(ReceiptError, match="ship has blocking"):
        validate_linked(receipt, analysis, analysis_bytes, load_config())


def test_analysis_bytes_are_bound_exactly() -> None:
    receipt, analysis, analysis_bytes = _linked()
    with pytest.raises(ReceiptError, match="hash mismatch"):
        validate_linked(receipt, analysis, analysis_bytes + b" ", load_config())


def test_receipt_store_chains_and_detects_tampering(tmp_path: Path) -> None:
    config = load_config()
    store = ReceiptStore(tmp_path / "runs", config)
    receipt, analysis, _ = _linked()
    first = store.persist(analysis, receipt)
    second_analysis = json.loads(json.dumps(analysis))
    second_receipt = json.loads(json.dumps(receipt))
    second_analysis["run_id"] = "run-contract-second"
    second_receipt["run_id"] = "run-contract-second"
    second = store.persist(second_analysis, second_receipt)
    assert store.verify_chain() == ("run-contract-valid", "run-contract-second")
    tampered = json.loads(second.receipt_path.read_text(encoding="utf-8"))
    tampered["verdict"] = "revise"
    second.receipt_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ReceiptError, match="digest mismatch"):
        store.verify_chain()
    assert first.receipt["previous_receipt_hash"] is None


def test_run_id_path_escape_is_rejected(tmp_path: Path) -> None:
    receipt, analysis, _ = _linked()
    receipt["run_id"] = "../escape"
    analysis["run_id"] = "../escape"
    with pytest.raises(ReceiptError, match="unsafe"):
        ReceiptStore(tmp_path / "runs", load_config()).persist(analysis, receipt)
