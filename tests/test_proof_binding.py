from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from autofusion.config import load_config
from autofusion.engine import FusionEngine, FusionRunRequest, PendingRun
from autofusion.errors import ProofError
from autofusion.grounding import RunnerOutput, VerificationCommand
from autofusion.proof import (
    ProofCapsule,
    ProofOutcome,
    ProofPolicy,
    ProofRelation,
    VerificationIntent,
    hash_proof_tree,
    relation_expectations,
    run_proof,
    sign_proof_capsule,
)
from autofusion.providers.fake import DeterministicFakeProvider
from autofusion.reconcile import FindingDisposition
from autofusion.registry import ProviderRegistry

_KEY = b"synthetic-proof-signing-key-material-32-bytes"


def _pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[FusionEngine, PendingRun, str]:
    config = load_config()
    monkeypatch.setenv(str(config.section("proof")["attestation_key_env"]), _KEY.decode())
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
                "claim": "seeded proof regression",
                "evidence": {"summary": "app.py", "strength": "artifact-cited"},
                "suggested_fix": "Fix the value",
                "checkable": False,
                "verification_id": None,
                "stance_key": "regression",
                "stance": "supports",
            }
        ],
    }
    engine = FusionEngine(
        config,
        ProviderRegistry({"gpt-sol": DeterministicFakeProvider(config.model("gpt-sol"), output)}),
        work_root=tmp_path / "work",
    )
    pending = engine.run(
        FusionRunRequest(
            task="Review",
            repo_root=repo,
            artifact_kind="diff",
            artifact_paths=("app.py",),
            preset="fast",
            self_model="claude-opus-4-8",
            run_grounding=False,
        )
    )
    assert isinstance(pending, PendingRun)
    return engine, pending, str(json.loads(pending.analysis_path.read_bytes())["findings"][0]["id"])


def _capsule(
    tmp_path: Path,
    pending: PendingRun,
    finding_id: str,
    relation: ProofRelation = ProofRelation.REGRESSION,
    mismatch: str | None = None,
    *,
    proof_id: str = "proof-binding",
    test_author: str = "gpt-sol",
) -> ProofCapsule:
    expectations = relation_expectations(relation, mutation_required=True)
    frozen = Path(json.loads(pending.pending_path.read_bytes())["snapshot"]["root"])
    roots: dict[str, Path] = {}
    root = tmp_path / proof_id / test_author
    target = 1 if relation is ProofRelation.FEATURE else 0
    for name, expected in expectations.items():
        path = root / name
        path.mkdir(parents=True)
        value = target if expected is ProofOutcome.PASS else 2
        (path / "app.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
        if name == "head":
            shutil.copyfile(frozen / "app.py", path / "app.py")
        roots[name] = path
    if mismatch in {"unrelated", "mapped-other"}:
        (roots["head"] / "app.py").write_text("VALUE = 99\n", encoding="utf-8")
    if mismatch == "mapped-other":
        other = next(name for name in roots if name not in {"head", "mutant"})
        shutil.copyfile(frozen / "app.py", roots[other] / "app.py")
    if mismatch == "extra-file":
        (roots["head"] / "unreviewed.txt").write_text("extra", encoding="utf-8")
    overlay = root / "overlay" / "tests" / "autofusion_proof"
    overlay.mkdir(parents=True)
    (overlay / "test_value.py").write_text(
        f"from app import VALUE\ndef test_value():\n    assert VALUE == {target}\n",
        encoding="utf-8",
    )

    class RelationRunner:
        # Synthetic runner: these tests measure binding and persistence, not live isolation.
        network_denied = True
        strong_isolation = True
        runner_attestation_hash = "a" * 64

        def run(self, argv: Sequence[str], cwd: Path, timeout_s: int) -> RunnerOutput:
            return (
                RunnerOutput(0, stdout="1 passed")
                if expectations[cwd.name] is ProofOutcome.PASS
                else RunnerOutput(1, stderr="AssertionError: seeded proof regression")
            )

    capsule = run_proof(
        VerificationIntent(
            proof_id,
            pending.run_id,
            finding_id,
            relation,
            "python.pytest",
            test_author,
            "self",
            False,
            {name: hash_proof_tree(path) for name, path in roots.items()},
        ),
        revisions=roots,
        overlay_root=root / "overlay",
        command=VerificationCommand(
            "python.pytest",
            ("pytest", "-q"),
            30,
            "dynamic",
            expected_failure=("seeded proof regression",),
        ),
        runner=RelationRunner(),
        policy=ProofPolicy(True, ("tests/autofusion_proof/**",), 4, 4096, True, 1024),
        work_root=root / "proof-work",
    )
    return sign_proof_capsule(capsule, key_id="local-proof-v1", signing_key=_KEY)


@pytest.mark.parametrize("relation", list(ProofRelation))
@pytest.mark.parametrize("mismatch", ["unrelated", "mapped-other", "extra-file"])
def test_signed_proof_must_bind_head_to_reviewed_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relation: ProofRelation,
    mismatch: str,
) -> None:
    engine, pending, finding_id = _pending(tmp_path, monkeypatch)
    capsule = _capsule(tmp_path, pending, finding_id, relation, mismatch)
    with pytest.raises(ProofError, match="reviewed artifact"):
        engine.finalize(
            pending.run_id,
            dispositions=(
                FindingDisposition(finding_id, "accepted", "Trust only a proof for this review"),
            ),
            proof_capsules=(capsule,),
        )
    assert not (pending.pending_path.parent / "finalized.json").exists()


@pytest.mark.parametrize("relation", list(ProofRelation))
def test_matching_proof_tree_under_another_root_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relation: ProofRelation,
) -> None:
    engine, pending, finding_id = _pending(tmp_path, monkeypatch)
    capsule = _capsule(tmp_path, pending, finding_id, relation)
    artifacts = engine.finalize(
        pending.run_id,
        dispositions=(
            FindingDisposition(finding_id, "accepted", "Proof matches the reviewed tree"),
        ),
        proof_capsules=(capsule,),
    )
    assert artifacts.receipt["findings"]["confirmed_by_proof"] == 1
