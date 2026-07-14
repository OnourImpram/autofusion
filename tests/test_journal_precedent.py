from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from autofusion.errors import JournalError, PrecedentError, ReceiptError
from autofusion.evidence import EvidenceLedger
from autofusion.journal import RunJournal
from autofusion.precedent import (
    OutcomeVerdict,
    PrecedentLedger,
    finding_fingerprint,
    repository_scope_hash,
)


def test_run_journal_is_idempotent_and_terminal_summary_is_verified(tmp_path: Path) -> None:
    journal = RunJournal(tmp_path / "journal.jsonl", "run-01")
    first = journal.record(
        "prepare",
        "completed",
        idempotency_key="state:prepared",
        payload={"artifact_kind": "diff"},
    )
    replayed = journal.record(
        "prepare",
        "completed",
        idempotency_key="state:prepared",
        payload={"artifact_kind": "diff"},
    )
    assert replayed == first
    assert len(journal.events()) == 1
    journal.record(
        "terminal",
        "completed",
        idempotency_key="terminal:completed",
        payload={"verdict": "ship", "receipt_hash": "a" * 64},
    )
    summary = journal.summary()
    assert summary.terminal
    assert summary.event_count == 2
    assert summary.journal_head_hash


def test_run_journal_rejects_conflicting_retry_and_tampering(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    journal = RunJournal(path, "run-01")
    journal.record(
        "route",
        "completed",
        idempotency_key="state:routed",
        payload={"panel": "default"},
    )
    with pytest.raises(JournalError, match="conflicting"):
        journal.record(
            "route",
            "completed",
            idempotency_key="state:routed",
            payload={"panel": "quality"},
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["payload"]["payload"]["panel"] = "tampered"
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    with pytest.raises(ReceiptError, match="chain verification"):
        journal.events()


def _ledger(tmp_path: Path) -> PrecedentLedger:
    return PrecedentLedger(
        tmp_path / "precedents.jsonl",
        repository_scope_hash("OnourImpram/autofusion"),
    )


def test_precedent_is_metadata_only_post_blind_and_outcome_linked(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    fingerprint = finding_fingerprint(
        category="correctness",
        claim="A private raw claim that must not be stored",
        path_hashes=("a" * 64,),
    )
    decision = ledger.record_decision(
        fingerprint=fingerprint,
        category="correctness",
        disposition="accepted",
        source_receipt_hash="b" * 64,
        policy_hash="c" * 64,
        ttl_days=30,
    )
    raw = (tmp_path / "precedents.jsonl").read_text(encoding="utf-8")
    assert "private raw claim" not in raw
    with pytest.raises(PrecedentError, match="before blind review"):
        ledger.query(fingerprint, phase="blind-first-pass")

    outcome = ledger.record_outcome(
        decision.record_id,
        outcome=OutcomeVerdict.CONFIRMED,
        evidence_hash="d" * 64,
    )
    repeated = ledger.record_outcome(
        decision.record_id,
        outcome=OutcomeVerdict.CONFIRMED,
        evidence_hash="d" * 64,
    )
    assert repeated == outcome
    matches = ledger.query(fingerprint, phase="post-blind-review")
    assert len(matches) == 1
    assert matches[0].outcome is OutcomeVerdict.CONFIRMED
    assert matches[0].outcome_evidence_hash == "d" * 64


def test_precedent_expiry_unknown_outcome_and_scope_crossing_fail_closed(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    fingerprint = finding_fingerprint(
        category="security", claim="claim", path_hashes=()
    )
    decision = ledger.record_decision(
        fingerprint=fingerprint,
        category="security",
        disposition="rejected",
        source_receipt_hash="e" * 64,
        policy_hash="f" * 64,
        ttl_days=1,
    )
    future = datetime.now(UTC) + timedelta(days=2)
    assert ledger.query(fingerprint, phase="post-blind-review", now=future) == ()
    with pytest.raises(PrecedentError, match="unknown precedent"):
        ledger.record_outcome(
            "precedent-unknown",
            outcome=OutcomeVerdict.REFUTED,
            evidence_hash="1" * 64,
        )

    other_scope = PrecedentLedger(
        ledger.path,
        repository_scope_hash("another/repository"),
    )
    with pytest.raises(PrecedentError, match="repository scope"):
        other_scope.query(fingerprint, phase="post-blind-review")
    assert decision.record_id.startswith("precedent-")


def test_empty_journal_and_invalid_identifiers_fail_closed(tmp_path: Path) -> None:
    summary = RunJournal(tmp_path / "empty.jsonl", "run-empty").summary()
    assert summary.as_json() == {
        "run_id": "run-empty",
        "event_count": 0,
        "latest_step": None,
        "latest_status": None,
        "waiting": False,
        "terminal": False,
        "journal_head_hash": None,
    }
    with pytest.raises(JournalError, match="run_id"):
        RunJournal(tmp_path / "unsafe.jsonl", "unsafe run")
    journal = RunJournal(tmp_path / "valid.jsonl", "run-01")
    with pytest.raises(JournalError, match="step"):
        journal.record("Unsafe", "completed", idempotency_key="safe")
    with pytest.raises(JournalError, match="status"):
        journal.record("route", "unknown", idempotency_key="safe")  # type: ignore[arg-type]
    with pytest.raises(JournalError, match="idempotency"):
        journal.record("route", "completed", idempotency_key="unsafe key")


def test_journal_validates_run_and_payload_hash_inside_valid_chain(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    EvidenceLedger(path).append(
        "run-journal",
        {
            "run_id": "run-other",
            "step": "route",
            "status": "completed",
            "idempotency_key": "route:complete",
            "payload_hash": "0" * 64,
            "payload": {},
        },
    )
    with pytest.raises(JournalError, match="different run"):
        RunJournal(path, "run-01").events()

    hash_path = tmp_path / "hash.jsonl"
    EvidenceLedger(hash_path).append(
        "run-journal",
        {
            "run_id": "run-01",
            "step": "route",
            "status": "completed",
            "idempotency_key": "route:complete",
            "payload_hash": "0" * 64,
            "payload": {"panel": "default"},
        },
    )
    with pytest.raises(JournalError, match="payload hash"):
        RunJournal(hash_path, "run-01").events()


@pytest.mark.parametrize(
    ("category", "disposition", "ttl_days", "message"),
    [
        ("correctness", "unknown", 10, "disposition"),
        ("", "accepted", 10, "category"),
        ("correctness", "accepted", 0, "TTL"),
    ],
)
def test_precedent_rejects_invalid_decision_metadata(
    tmp_path: Path,
    category: str,
    disposition: str,
    ttl_days: int,
    message: str,
) -> None:
    ledger = _ledger(tmp_path)
    with pytest.raises(PrecedentError, match=message):
        ledger.record_decision(
            fingerprint="a" * 64,
            category=category,
            disposition=disposition,
            source_receipt_hash="b" * 64,
            policy_hash="c" * 64,
            ttl_days=ttl_days,
        )


def test_precedent_fingerprints_validate_inputs_and_deduplicate_decisions(
    tmp_path: Path,
) -> None:
    with pytest.raises(PrecedentError, match="category and claim"):
        finding_fingerprint(category="", claim="", path_hashes=())
    with pytest.raises(PrecedentError, match="path_hash"):
        finding_fingerprint(category="security", claim="claim", path_hashes=("bad",))
    with pytest.raises(PrecedentError, match="identifier"):
        repository_scope_hash(" ")

    ledger = _ledger(tmp_path)
    fingerprint = finding_fingerprint(
        category="correctness", claim="same claim", path_hashes=()
    )
    first = ledger.record_decision(
        fingerprint=fingerprint,
        category="correctness",
        disposition="accepted",
        source_receipt_hash="b" * 64,
        policy_hash="c" * 64,
        ttl_days=30,
    )
    second = ledger.record_decision(
        fingerprint=fingerprint,
        category="correctness",
        disposition="accepted",
        source_receipt_hash="b" * 64,
        policy_hash="c" * 64,
        ttl_days=30,
    )
    assert second == first
