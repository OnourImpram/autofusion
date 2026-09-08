from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from test_proof_binding import _KEY, _pending

from autofusion.grounding import RunnerOutput, VerificationCommand
from autofusion.proof import (
    ProofOutcome,
    ProofPolicy,
    ProofRelation,
    ProofVerdict,
    VerificationIntent,
    hash_proof_tree,
    run_proof,
    sign_proof_capsule,
)
from autofusion.reconcile import FindingDisposition


@pytest.mark.parametrize("difference", ["empty-directory", "file-mode"])
def test_proof_stages_the_same_canonical_tree_as_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    difference: str,
) -> None:
    engine, pending, finding_id = _pending(tmp_path, monkeypatch)
    frozen = Path(json.loads(pending.pending_path.read_bytes())["snapshot"]["root"])
    roots = {name: tmp_path / f"canonical-{name}" for name in ("base", "head", "mutant")}
    for name, root in roots.items():
        shutil.copytree(frozen, root)
        if name != "base":
            if difference == "empty-directory":
                (root / "runtime_flag").mkdir()
            else:
                (root / "app.py").chmod(stat.S_IREAD | stat.S_IWRITE)
    assert hash_proof_tree(roots["head"]) == hash_proof_tree(frozen)
    overlay = tmp_path / "canonical-overlay" / "tests" / "autofusion_proof"
    overlay.mkdir(parents=True)
    (overlay / "check_directory.py").write_text(
        "from pathlib import Path\nimport stat\n"
        "assert not Path('runtime_flag').exists(), 'seeded proof regression'\n"
        "assert not (Path('app.py').stat().st_mode & stat.S_IWUSR), 'seeded proof regression'\n",
        encoding="utf-8",
    )

    executed_outputs: list[RunnerOutput] = []

    class LocalAssertionRunner:
        # Only this fixed local assertion executes; isolation flags are fixture declarations.
        network_denied = True
        strong_isolation = True
        runner_attestation_hash = "b" * 64

        def run(self, argv: Sequence[str], cwd: Path, timeout_s: int) -> RunnerOutput:
            try:
                result = subprocess.run(
                    argv,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                    check=False,
                )
            except OSError as error:
                raise AssertionError(f"local assertion could not start: {error}") from error
            output = RunnerOutput(result.returncode, result.stdout, result.stderr)
            executed_outputs.append(output)
            return output

    capsule = run_proof(
        VerificationIntent(
            "proof-canonical",
            pending.run_id,
            finding_id,
            ProofRelation.REGRESSION,
            "python.canonical",
            "gpt-sol",
            "self",
            False,
            {name: hash_proof_tree(root) for name, root in roots.items()},
        ),
        revisions=roots,
        overlay_root=tmp_path / "canonical-overlay",
        command=VerificationCommand(
            "python.canonical",
            (sys.executable, "tests/autofusion_proof/check_directory.py"),
            5,
            "dynamic",
            expected_failure=("seeded proof regression",),
        ),
        runner=LocalAssertionRunner(),
        policy=ProofPolicy(True, ("tests/autofusion_proof/**",), 4, 4096, True, 4096),
        work_root=tmp_path / "canonical-work",
    )
    assert capsule.verdict is ProofVerdict.NOT_REPRODUCED, executed_outputs
    head = next(item for item in capsule.observations if item.revision == "head")
    assert head.actual is ProofOutcome.PASS
    signed = sign_proof_capsule(capsule, key_id="local-proof-v1", signing_key=_KEY)
    artifacts = engine.finalize(
        pending.run_id,
        dispositions=(FindingDisposition(finding_id, "accepted", "No confirming proof exists"),),
        proof_capsules=(signed,),
    )
    assert artifacts.receipt["findings"]["confirmed_by_proof"] == 0
