from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from autofusion.errors import ProofError
from autofusion.grounding import RunnerOutput, VerificationCommand
from autofusion.proof import (
    ProofPolicy,
    ProofRelation,
    VerificationIntent,
    hash_proof_tree,
    run_proof,
)


def test_staged_head_is_verified_before_execution_after_source_churn(tmp_path: Path) -> None:
    revisions = {name: tmp_path / name for name in ("base", "head", "mutant")}
    for name, path in revisions.items():
        path.mkdir()
        (path / "app.py").write_text("VALUE = 1\n" if name != "mutant" else "VALUE = 99\n")
    hashes = {name: hash_proof_tree(path) for name, path in revisions.items()}
    overlay = tmp_path / "overlay" / "tests" / "autofusion_proof"
    overlay.mkdir(parents=True)
    (overlay / "test_value.py").write_text(
        "from app import VALUE\ndef test_value():\n    assert VALUE == 1\n", encoding="utf-8"
    )
    calls: list[str] = []

    class EditingRunner:
        # Simulate a concurrent editor using real source writes at deterministic stage boundaries.
        network_denied = True
        strong_isolation = True
        runner_attestation_hash = "a" * 64

        def run(self, argv: Sequence[str], cwd: Path, timeout_s: int) -> RunnerOutput:
            calls.append(cwd.name)
            if cwd.name == "base":
                (revisions["head"] / "app.py").write_text("VALUE = 99\n")
                return RunnerOutput(0, stdout="1 passed")
            if cwd.name == "head":
                assert (cwd / "app.py").read_text() == "VALUE = 99\n"
                (revisions["head"] / "app.py").write_text("VALUE = 1\n")
            return RunnerOutput(1, stderr="AssertionError: intended value regression")

    with pytest.raises(ProofError, match="staged revision"):
        run_proof(
            VerificationIntent(
                "proof-stage",
                "run-stage",
                "f01",
                ProofRelation.REGRESSION,
                "python.test",
                "reviewer",
                "self",
                False,
                hashes,
            ),
            revisions=revisions,
            overlay_root=tmp_path / "overlay",
            command=VerificationCommand(
                "python.test",
                ("pytest", "-q"),
                5,
                "dynamic",
                expected_failure=("intended value regression",),
            ),
            runner=EditingRunner(),
            policy=ProofPolicy(True, ("tests/autofusion_proof/**",), 4, 4096, True, 4096),
            work_root=tmp_path / "work",
        )
    assert calls == ["base"]
