from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_proof_binding import _capsule, _pending

from autofusion import engine as engine_module
from autofusion.errors import ReceiptError
from autofusion.evidence import EvidenceLedger
from autofusion.reconcile import FindingDisposition


def test_concurrent_finalization_cannot_drop_an_accepted_capsule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autofusion.reconcile import reconcile_analysis
    from autofusion.util import JsonObject

    engine, pending, finding_id = _pending(tmp_path, monkeypatch)
    capsule = _capsule(tmp_path, pending, finding_id)
    paused = threading.Event()
    resume = threading.Event()
    dispositions = (FindingDisposition(finding_id, "rejected", "Reject the unproven finding"),)

    def pause_first(
        analysis: JsonObject,
        values: tuple[FindingDisposition, ...],
        *,
        require_all: bool = True,
    ) -> JsonObject:
        if threading.current_thread().name.startswith("proof-free"):
            paused.set()
            assert resume.wait(timeout=5)
        return reconcile_analysis(analysis, values, require_all=require_all)

    monkeypatch.setattr(engine_module, "reconcile_analysis", pause_first)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="proof-free") as pool:
        first = pool.submit(engine.finalize, pending.run_id, dispositions=dispositions)
        try:
            assert paused.wait(timeout=5)
            with pytest.raises(ReceiptError, match=r"finalization.*progress"):
                engine.finalize(
                    pending.run_id, dispositions=dispositions, proof_capsules=(capsule,)
                )
        finally:
            resume.set()
        artifacts = first.result(timeout=5)
    assert artifacts.receipt["verdict"] == "ship"
    # The second call was rejected before acceptance; evidence cannot be lost after acceptance.
    assert not any(
        r.kind == "proof-capsule" for r in EvidenceLedger(pending.evidence_path).verify()
    )
