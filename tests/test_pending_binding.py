from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from autofusion.config import load_config
from autofusion.engine import FusionEngine, FusionRunRequest, PendingRun
from autofusion.errors import ReceiptError
from autofusion.evidence import EvidenceLedger
from autofusion.grounding import RunnerOutput
from autofusion.providers.fake import DeterministicFakeProvider
from autofusion.reconcile import FindingDisposition
from autofusion.registry import ProviderRegistry


class ConfirmingRunner:
    network_denied = True

    def run(self, argv: Sequence[str], cwd: Path, timeout_s: float) -> RunnerOutput:
        return RunnerOutput(1, stderr="AssertionError: seeded regression")


@pytest.mark.parametrize("ground", [False, True])
@pytest.mark.parametrize("tamper", ["remove-findings", "format-only"])
def test_pending_analysis_bytes_are_bound_before_signoff(
    tmp_path: Path,
    ground: bool,
    tamper: str,
) -> None:
    config = load_config(
        overrides={
            "verification": {
                "profiles": {
                    "python": {"commands": {"pytest": {"expected_failure": ["seeded regression"]}}}
                }
            }
        }
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    output = {
        "summary": "Review",
        "coverage": ["artifact"],
        "blind_spots": [],
        "recommendation": "revise",
        "findings": [
            {
                "severity": "minor",
                "category": "correctness",
                "claim": "seeded regression",
                "evidence": {"summary": "app.py", "strength": "artifact-cited"},
                "suggested_fix": "Fix it",
                "checkable": True,
                "verification_id": "python.pytest",
                "stance_key": "regression",
                "stance": "supports",
            }
        ],
    }
    registry = ProviderRegistry(
        {"gpt-sol": DeterministicFakeProvider(config.model("gpt-sol"), output)}
    )
    engine = FusionEngine(
        config, registry, work_root=tmp_path / "work", grounding_runner=ConfirmingRunner()
    )
    pending = engine.run(
        FusionRunRequest(
            task="Review",
            repo_root=repo,
            artifact_kind="diff",
            artifact_paths=("app.py",),
            preset="fast",
            self_model="claude-opus-5",
            run_grounding=ground,
        )
    )
    assert isinstance(pending, PendingRun)
    original_pending = pending.pending_path.read_bytes()
    analysis = json.loads(pending.analysis_path.read_bytes())
    assert analysis["findings"]
    if ground:
        assert analysis["grounding_results"][0]["verdict"] == "confirmed"
    if tamper == "remove-findings":
        for key in ("findings", "grounding_candidates", "grounding_results", "unique_insights"):
            analysis[key] = []
        for participant in analysis["participants"]:
            if "recommendation" in participant:
                participant["recommendation"] = "ship"
        pending.analysis_path.write_text(json.dumps(analysis), encoding="utf-8")
    else:
        pending.analysis_path.write_bytes(b" " + pending.analysis_path.read_bytes())
    dispositions = (
        ()
        if tamper == "remove-findings"
        else (FindingDisposition(analysis["findings"][0]["id"], "accepted", "Accept the finding"),)
    )
    with pytest.raises(ReceiptError, match=r"analysis.*bound"):
        engine.finalize(pending.run_id, dispositions=dispositions)
    assert pending.pending_path.read_bytes() == original_pending
    assert not any(
        record.kind == "signoff" for record in EvidenceLedger(pending.evidence_path).verify()
    )
    assert not (repo / ".fusion" / "runs").exists()
