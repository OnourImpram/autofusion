from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from autofusion.errors import PolicyError, ProofError
from autofusion.grounding import RunnerOutput, VerificationCommand
from autofusion.proof import (
    OverlayEntry,
    ProofCapsule,
    ProofObservation,
    ProofOutcome,
    ProofPolicy,
    ProofRelation,
    ProofVerdict,
    VerificationIntent,
    build_overlay_manifest,
    capsule_from_json,
    hash_proof_tree,
    proof_policy_from_config,
    relation_expectations,
    run_proof,
    sign_proof_capsule,
    verify_proof_capsule_attestation,
)

_ATTESTATION_KEY = b"controlled-proof-attestation-key-32-bytes"
_ATTESTATION_KEY_ID = "local-proof-v1"


@dataclass
class FakeProofRunner:
    outcomes: dict[str, RunnerOutput]
    network_denied: bool = True
    strong_isolation: bool = True
    runner_attestation_hash: str = "a" * 64
    calls: list[tuple[tuple[str, ...], str, int]] = field(default_factory=list)

    def run(self, argv: Sequence[str], cwd: Path, timeout_s: int) -> RunnerOutput:
        self.calls.append((tuple(argv), cwd.name, timeout_s))
        return self.outcomes[cwd.name]


def _revision(root: Path, name: str) -> Path:
    revision = root / name
    revision.mkdir()
    (revision / "app.py").write_text(f"VALUE = {name!r}\n", encoding="utf-8")
    return revision


def _overlay(root: Path, content: str = "def test_regression():\n    assert True\n") -> Path:
    overlay = root / "overlay"
    target = overlay / "tests" / "autofusion_proof" / "test_regression.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return overlay


def _policy() -> ProofPolicy:
    return ProofPolicy(
        enabled=True,
        approved_overlay_patterns=("tests/autofusion_proof/**",),
        max_overlay_files=4,
        max_overlay_bytes=4096,
        require_mutation=True,
        max_output_chars=1024,
    )


def _command() -> VerificationCommand:
    return VerificationCommand(
        verification_id="python.pytest",
        argv=("pytest", "-q"),
        timeout_s=30,
        kind="dynamic",
    )


def _intent(revisions: dict[str, Path]) -> VerificationIntent:
    return VerificationIntent(
        proof_id="proof-01",
        fusion_run_id="run-01",
        finding_id="f01",
        relation=ProofRelation.REGRESSION,
        verification_id="python.pytest",
        test_author="gpt-sol",
        patch_author="self",
        candidate_fix_visible=False,
        revision_hashes={name: hash_proof_tree(path) for name, path in revisions.items()},
    )


def _signed(capsule: ProofCapsule) -> ProofCapsule:
    return sign_proof_capsule(
        capsule,
        key_id=_ATTESTATION_KEY_ID,
        signing_key=_ATTESTATION_KEY,
    )


def test_proof_confirms_declared_relation_and_round_trips_capsule(tmp_path: Path) -> None:
    revisions = {
        name: _revision(tmp_path, name) for name in ("base", "head", "mutant")
    }
    runner = FakeProofRunner(
        {
            "base": RunnerOutput(exit_code=0, stdout="1 passed"),
            "head": RunnerOutput(exit_code=1, stderr="AssertionError: regression"),
            "mutant": RunnerOutput(exit_code=1, stderr="AssertionError: mutant"),
        }
    )
    capsule = run_proof(
        _intent(revisions),
        revisions=revisions,
        overlay_root=_overlay(tmp_path),
        command=_command(),
        runner=runner,
        policy=_policy(),
        work_root=tmp_path / "work",
    )

    assert capsule.verdict is ProofVerdict.CONFIRMED
    assert capsule.mutation_gate_passed
    assert len(capsule.observations) == 3
    signed = _signed(capsule)
    assert capsule_from_json(signed.as_json()) == signed
    assert (
        verify_proof_capsule_attestation(
            signed,
            key_id=_ATTESTATION_KEY_ID,
            verification_key=_ATTESTATION_KEY,
        )
        == signed
    )
    assert {call[1] for call in runner.calls} == {"base", "head", "mutant"}
    assert not any((tmp_path / "work").iterdir())


def test_mutation_survival_prevents_confirmation(tmp_path: Path) -> None:
    revisions = {
        name: _revision(tmp_path, name) for name in ("base", "head", "mutant")
    }
    runner = FakeProofRunner(
        {
            "base": RunnerOutput(exit_code=0),
            "head": RunnerOutput(exit_code=1),
            "mutant": RunnerOutput(exit_code=0),
        }
    )
    capsule = run_proof(
        _intent(revisions),
        revisions=revisions,
        overlay_root=_overlay(tmp_path),
        command=_command(),
        runner=runner,
        policy=_policy(),
        work_root=tmp_path / "work",
    )
    assert capsule.verdict is ProofVerdict.NOT_REPRODUCED
    assert not capsule.mutation_gate_passed


def test_timeout_or_environment_failure_is_inconclusive(tmp_path: Path) -> None:
    revisions = {
        name: _revision(tmp_path, name) for name in ("base", "head", "mutant")
    }
    runner = FakeProofRunner(
        {
            "base": RunnerOutput(exit_code=0),
            "head": RunnerOutput(exit_code=None, timed_out=True),
            "mutant": RunnerOutput(exit_code=1),
        }
    )
    capsule = run_proof(
        _intent(revisions),
        revisions=revisions,
        overlay_root=_overlay(tmp_path),
        command=_command(),
        runner=runner,
        policy=_policy(),
        work_root=tmp_path / "work",
    )
    assert capsule.verdict is ProofVerdict.INCONCLUSIVE
    assert ProofOutcome.TIMEOUT in {item.actual for item in capsule.observations}


def test_proof_rejects_weak_isolation_and_unattested_runner(tmp_path: Path) -> None:
    revisions = {
        name: _revision(tmp_path, name) for name in ("base", "head", "mutant")
    }
    runner = FakeProofRunner({}, strong_isolation=False)
    with pytest.raises(PolicyError, match="disposable isolation"):
        run_proof(
            _intent(revisions),
            revisions=revisions,
            overlay_root=_overlay(tmp_path),
            command=_command(),
            runner=runner,
            policy=_policy(),
            work_root=tmp_path / "work",
        )
    runner.strong_isolation = True
    runner.runner_attestation_hash = "unattested"
    with pytest.raises(PolicyError, match="attestation"):
        run_proof(
            _intent(revisions),
            revisions=revisions,
            overlay_root=_overlay(tmp_path),
            command=_command(),
            runner=runner,
            policy=_policy(),
            work_root=tmp_path / "work",
        )


def test_overlay_policy_rejects_path_escape_binary_and_size_bombs(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    (overlay / "payload.py").write_text("pass\n", encoding="utf-8")
    with pytest.raises(PolicyError, match="unapproved path"):
        build_overlay_manifest(overlay, _policy())

    target = overlay / "tests" / "autofusion_proof" / "payload.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\x00binary")
    (overlay / "payload.py").unlink()
    with pytest.raises(PolicyError, match="text only"):
        build_overlay_manifest(overlay, _policy())

    target.write_text("x" * 5000, encoding="utf-8")
    with pytest.raises(PolicyError, match="byte limit"):
        build_overlay_manifest(overlay, _policy())


def test_intent_requires_independent_author_and_hidden_fix() -> None:
    hashes = {"base": "1" * 64, "head": "2" * 64, "mutant": "3" * 64}
    with pytest.raises(ProofError, match="independent"):
        VerificationIntent(
            "proof-01",
            "run-01",
            "f01",
            ProofRelation.REGRESSION,
            "python.pytest",
            "self",
            "self",
            False,
            hashes,
        )
    with pytest.raises(ProofError, match="candidate fix"):
        VerificationIntent(
            "proof-01",
            "run-01",
            "f01",
            ProofRelation.REGRESSION,
            "python.pytest",
            "gpt-sol",
            "self",
            True,
            hashes,
        )


def test_revision_hash_swap_and_capsule_tampering_are_rejected(tmp_path: Path) -> None:
    revisions = {
        name: _revision(tmp_path, name) for name in ("base", "head", "mutant")
    }
    intent = _intent(revisions)
    (revisions["head"] / "app.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ProofError, match="hash changed"):
        run_proof(
            intent,
            revisions=revisions,
            overlay_root=_overlay(tmp_path),
            command=_command(),
            runner=FakeProofRunner({}),
            policy=_policy(),
            work_root=tmp_path / "work",
        )

    raw = {
        "schema_version": "0.1",
        "proof_id": "proof-01",
        "fusion_run_id": "run-01",
        "finding_id": "f01",
        "intent_hash": "0" * 64,
        "relation": "regression",
        "verification_id": "python.pytest",
        "test_author": "gpt-sol",
        "patch_author": "self",
        "candidate_fix_visible": False,
        "overlay_hash": "0" * 64,
        "overlay_entries": [],
        "observations": [],
        "mutation_required": True,
        "mutation_gate_passed": True,
        "verdict": "confirmed",
        "capsule_hash": "0" * 64,
    }
    with pytest.raises(ProofError):
        capsule_from_json(raw)


def test_relation_and_config_policy_contracts() -> None:
    assert relation_expectations(ProofRelation.REPAIR, mutation_required=True) == {
        "head": ProofOutcome.FAIL,
        "fixed": ProofOutcome.PASS,
        "mutant": ProofOutcome.FAIL,
    }
    policy = proof_policy_from_config(
        {
            "enabled": True,
            "approved_overlay_patterns": ["tests/autofusion_proof/**"],
            "max_overlay_files": 2,
            "max_overlay_bytes": 100,
            "require_mutation": True,
            "max_output_chars": 200,
        }
    )
    assert policy.enabled and policy.require_mutation


def test_all_relation_expectations_and_optional_mutation() -> None:
    assert relation_expectations(ProofRelation.FEATURE, mutation_required=False) == {
        "base": ProofOutcome.FAIL,
        "head": ProofOutcome.PASS,
    }
    assert relation_expectations(
        ProofRelation.COUNTEREXAMPLE, mutation_required=True
    ) == {
        "control": ProofOutcome.PASS,
        "head": ProofOutcome.FAIL,
        "mutant": ProofOutcome.FAIL,
    }


def test_proof_value_objects_reject_malformed_provenance() -> None:
    with pytest.raises(ProofError, match="approved overlay"):
        ProofPolicy(True, (), 1, 1, True, 1)
    with pytest.raises(ProofError, match="positive"):
        ProofPolicy(True, ("tests/**",), 0, 1, True, 1)
    with pytest.raises(ProofError, match="schema version"):
        VerificationIntent(
            "proof-01",
            "run-01",
            "f01",
            ProofRelation.REGRESSION,
            "python.pytest",
            "reviewer",
            "self",
            False,
            {"base": "1" * 64, "head": "2" * 64, "mutant": "3" * 64},
            schema_version="9",
        )
    with pytest.raises(ProofError, match="proof_id is invalid"):
        VerificationIntent(
            "unsafe id",
            "run-01",
            "f01",
            ProofRelation.REGRESSION,
            "python.pytest",
            "reviewer",
            "self",
            False,
            {"base": "1" * 64, "head": "2" * 64, "mutant": "3" * 64},
        )
    with pytest.raises(ProofError, match="declared proof relation"):
        VerificationIntent(
            "proof-01",
            "run-01",
            "f01",
            ProofRelation.REGRESSION,
            "python.pytest",
            "reviewer",
            "self",
            False,
            {"base": "1" * 64, "head": "2" * 64},
        )
    with pytest.raises(ProofError, match="lowercase SHA"):
        VerificationIntent(
            "proof-01",
            "run-01",
            "f01",
            ProofRelation.REGRESSION,
            "python.pytest",
            "reviewer",
            "self",
            False,
            {"base": "X" * 64, "head": "2" * 64, "mutant": "3" * 64},
        )
    with pytest.raises(ProofError, match="entry path"):
        OverlayEntry("../escape.py", 1, "1" * 64)
    with pytest.raises(ProofError, match="entry metadata"):
        OverlayEntry("tests/proof.py", -1, "bad")
    with pytest.raises(ProofError, match="observation revision"):
        ProofObservation(
            "unknown",
            "1" * 64,
            ProofOutcome.PASS,
            ProofOutcome.PASS,
            0,
            "completed",
            "2" * 64,
            False,
            "3" * 64,
            "4" * 64,
        )


def test_overlay_rejects_empty_non_utf8_and_file_count(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(PolicyError, match="no approved"):
        build_overlay_manifest(empty, _policy())

    overlay = tmp_path / "overlay"
    target = overlay / "tests" / "autofusion_proof"
    target.mkdir(parents=True)
    (target / "one.py").write_bytes(b"\xff\xfe")
    with pytest.raises(PolicyError, match="not UTF-8"):
        build_overlay_manifest(overlay, _policy())

    (target / "one.py").write_text("pass\n", encoding="utf-8")
    (target / "two.py").write_text("pass\n", encoding="utf-8")
    limited = ProofPolicy(True, ("tests/autofusion_proof/**",), 1, 4096, True, 100)
    with pytest.raises(PolicyError, match="file limit"):
        build_overlay_manifest(overlay, limited)


def test_overlay_rejects_symbolic_entries_even_when_host_creation_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay = _overlay(tmp_path)
    linked = overlay / "tests" / "autofusion_proof" / "linked.py"
    linked.write_text("pass\n", encoding="utf-8")
    original = Path.is_symlink

    def controlled_is_symlink(path: Path) -> bool:
        return path == linked or original(path)

    monkeypatch.setattr(Path, "is_symlink", controlled_is_symlink)
    with pytest.raises(ProofError, match="symbolic links"):
        build_overlay_manifest(overlay, _policy())


def test_proof_policy_and_command_mismatches_fail_before_execution(
    tmp_path: Path,
) -> None:
    revisions = {
        name: _revision(tmp_path, name) for name in ("base", "head", "mutant")
    }
    intent = _intent(revisions)
    disabled = ProofPolicy(False, ("tests/autofusion_proof/**",), 4, 4096, True, 100)
    with pytest.raises(PolicyError, match="disabled"):
        run_proof(
            intent,
            revisions=revisions,
            overlay_root=_overlay(tmp_path),
            command=_command(),
            runner=FakeProofRunner({}),
            policy=disabled,
            work_root=tmp_path / "work-disabled",
        )

    mismatched = VerificationCommand("python.ruff", ("ruff",), 10, "static")
    with pytest.raises(ProofError, match="do not match"):
        run_proof(
            intent,
            revisions=revisions,
            overlay_root=tmp_path / "overlay",
            command=mismatched,
            runner=FakeProofRunner({}),
            policy=_policy(),
            work_root=tmp_path / "work-command",
        )

    with pytest.raises(ProofError, match="supplied revisions"):
        run_proof(
            intent,
            revisions={"base": revisions["base"], "head": revisions["head"]},
            overlay_root=tmp_path / "overlay",
            command=_command(),
            runner=FakeProofRunner({}),
            policy=_policy(),
            work_root=tmp_path / "work-revisions",
        )


def test_optional_mutation_is_rejected_by_strict_policy(tmp_path: Path) -> None:
    revisions = {name: _revision(tmp_path, name) for name in ("base", "head")}
    intent = VerificationIntent(
        "proof-optional",
        "run-01",
        "f01",
        ProofRelation.REGRESSION,
        "python.pytest",
        "reviewer",
        "self",
        False,
        {name: hash_proof_tree(path) for name, path in revisions.items()},
        mutation_required=False,
    )
    with pytest.raises(PolicyError, match="mutation gate"):
        run_proof(
            intent,
            revisions=revisions,
            overlay_root=_overlay(tmp_path),
            command=_command(),
            runner=FakeProofRunner({}),
            policy=_policy(),
            work_root=tmp_path / "work",
        )


def test_runner_exceptions_and_environment_text_are_inconclusive(tmp_path: Path) -> None:
    revisions = {
        name: _revision(tmp_path, name) for name in ("base", "head", "mutant")
    }

    @dataclass
    class RaisingRunner:
        network_denied: bool = True
        strong_isolation: bool = True
        runner_attestation_hash: str = "b" * 64

        def run(
            self, argv: Sequence[str], cwd: Path, timeout_s: int
        ) -> RunnerOutput:
            del argv, timeout_s
            if cwd.name == "base":
                raise TimeoutError
            if cwd.name == "head":
                raise OSError("controlled environment error")
            return RunnerOutput(exit_code=1, stderr="No module named controlled")

    capsule = run_proof(
        _intent(revisions),
        revisions=revisions,
        overlay_root=_overlay(tmp_path),
        command=_command(),
        runner=RaisingRunner(),
        policy=_policy(),
        work_root=tmp_path / "work",
    )
    assert capsule.verdict is ProofVerdict.INCONCLUSIVE
    assert {item.actual for item in capsule.observations} == {
        ProofOutcome.TIMEOUT,
        ProofOutcome.ENVIRONMENT_ERROR,
    }


def test_command_working_directory_cannot_escape_proof_workspace(tmp_path: Path) -> None:
    revisions = {
        name: _revision(tmp_path, name) for name in ("base", "head", "mutant")
    }
    command = VerificationCommand(
        "python.pytest", ("pytest", "-q"), 30, "dynamic", cwd=".."
    )
    with pytest.raises(ProofError, match="working directory escapes"):
        run_proof(
            _intent(revisions),
            revisions=revisions,
            overlay_root=_overlay(tmp_path),
            command=command,
            runner=FakeProofRunner({}),
            policy=_policy(),
            work_root=tmp_path / "work",
        )


def test_capsule_rejects_each_tampered_structural_hash(tmp_path: Path) -> None:
    revisions = {
        name: _revision(tmp_path, name) for name in ("base", "head", "mutant")
    }
    capsule = run_proof(
        _intent(revisions),
        revisions=revisions,
        overlay_root=_overlay(tmp_path),
        command=_command(),
        runner=FakeProofRunner(
            {
                "base": RunnerOutput(exit_code=0),
                "head": RunnerOutput(exit_code=1),
                "mutant": RunnerOutput(exit_code=1),
            }
        ),
        policy=_policy(),
        work_root=tmp_path / "work",
    )
    capsule = _signed(capsule)
    raw = capsule.as_json()
    for key, message in (
        ("overlay_hash", "overlay hash"),
        ("capsule_hash", "capsule hash"),
        ("intent_hash", "intent hash"),
    ):
        tampered = dict(raw)
        tampered[key] = "f" * 64
        with pytest.raises(ProofError, match=message):
            capsule_from_json(tampered)

    with pytest.raises(ProofError, match="mutation gate is inconsistent"):
        ProofCapsule(
            intent=capsule.intent,
            overlay_hash=capsule.overlay_hash,
            overlay_entries=capsule.overlay_entries,
            observations=capsule.observations,
            mutation_gate_passed=False,
            verdict=ProofVerdict.CONFIRMED,
        )

    forged_base = replace(capsule.observations[0], actual=ProofOutcome.FAIL)
    with pytest.raises(ProofError, match="verdict is inconsistent"):
        replace(
            capsule,
            observations=(forged_base, *capsule.observations[1:]),
            capsule_hash="",
            attestation=None,
        )
    with pytest.raises(ProofError, match="declared relation"):
        replace(
            capsule,
            observations=(capsule.observations[0],) * 3,
            capsule_hash="",
            attestation=None,
        )
    forged_hash = replace(capsule.observations[0], revision_hash="9" * 64)
    with pytest.raises(ProofError, match="revision hash"):
        replace(
            capsule,
            observations=(forged_hash, *capsule.observations[1:]),
            capsule_hash="",
            attestation=None,
        )


def test_capsule_attestation_rejects_missing_wrong_or_tampered_keys(
    tmp_path: Path,
) -> None:
    revisions = {
        name: _revision(tmp_path, name) for name in ("base", "head", "mutant")
    }
    capsule = run_proof(
        _intent(revisions),
        revisions=revisions,
        overlay_root=_overlay(tmp_path),
        command=_command(),
        runner=FakeProofRunner(
            {
                "base": RunnerOutput(exit_code=0),
                "head": RunnerOutput(exit_code=1),
                "mutant": RunnerOutput(exit_code=1),
            }
        ),
        policy=_policy(),
        work_root=tmp_path / "work",
    )
    with pytest.raises(PolicyError, match="missing"):
        verify_proof_capsule_attestation(
            capsule,
            key_id=_ATTESTATION_KEY_ID,
            verification_key=_ATTESTATION_KEY,
        )
    signed = _signed(capsule)
    with pytest.raises(PolicyError, match="verification failed"):
        verify_proof_capsule_attestation(
            signed,
            key_id=_ATTESTATION_KEY_ID,
            verification_key=b"different-proof-attestation-key-32-byte",
        )
    with pytest.raises(PolicyError, match="not active"):
        verify_proof_capsule_attestation(
            signed,
            key_id="rotated-proof-v2",
            verification_key=_ATTESTATION_KEY,
        )
    unsigned = capsule.as_json()
    with pytest.raises(ProofError, match="attestation"):
        capsule_from_json(unsigned)
