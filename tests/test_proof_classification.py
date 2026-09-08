from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from autofusion.errors import ProofError
from autofusion.grounding import RunnerOutput, VerificationCommand
from autofusion.proof import (
    ProofCapsule,
    ProofPolicy,
    ProofRelation,
    ProofVerdict,
    VerificationIntent,
    capsule_from_json,
    hash_proof_tree,
    run_proof,
    sign_proof_capsule,
)

TARGET = "AssertionError: intended regression"
COMMAND = VerificationCommand(
    "python.pytest", ("pytest", "-q"), 10, "dynamic", expected_failure=(TARGET,)
)


@dataclass
class ClassifiedRunner:
    outputs: dict[str, RunnerOutput]
    network_denied: bool = True
    strong_isolation: bool = True
    runner_attestation_hash: str = "a" * 64
    calls: list[str] = field(default_factory=list)

    def run(self, argv: Sequence[str], cwd: Path, timeout_s: int) -> RunnerOutput:
        self.calls.append(cwd.name)
        return self.outputs[cwd.name]


def scenario(
    root: Path,
    output: RunnerOutput,
    *,
    revision: str = "head",
    command: VerificationCommand = COMMAND,
    max_output_chars: int = 1024,
    valid_failure: str = TARGET,
) -> ProofCapsule:
    revisions = {}
    for name in ("base", "head", "mutant"):
        path = root / name
        path.mkdir()
        (path / "app.py").write_text(f"VALUE = {name!r}\n", encoding="utf-8")
        revisions[name] = path
    overlay = root / "overlay"
    target = overlay / "tests" / "autofusion_proof" / "test_target.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_target():\n    assert True\n", encoding="utf-8")
    outputs = {
        "base": RunnerOutput(0, stdout="1 passed"),
        "head": RunnerOutput(1, stderr=valid_failure),
        "mutant": RunnerOutput(1, stderr=valid_failure),
    }
    outputs[revision] = output
    return run_proof(
        VerificationIntent(
            "classification",
            "run-classification",
            "f01",
            ProofRelation.REGRESSION,
            command.verification_id,
            "reviewer",
            "self",
            False,
            {name: hash_proof_tree(path) for name, path in revisions.items()},
        ),
        revisions=revisions,
        overlay_root=overlay,
        command=command,
        runner=ClassifiedRunner(outputs),
        policy=ProofPolicy(True, ("tests/autofusion_proof/**",), 4, 4096, True, max_output_chars),
        work_root=root / "work",
    )


@pytest.mark.parametrize("revision", ["head", "mutant"])
@pytest.mark.parametrize(
    ("output", "actual", "failure_class"),
    [
        (
            RunnerOutput(2, stderr="ERROR collecting test_target.py\n" + TARGET),
            "collection-error",
            "collection",
        ),
        (RunnerOutput(5, stdout="no tests ran"), "no-tests", "no-tests"),
        (RunnerOutput(4, stderr="usage: pytest\n" + TARGET), "usage-error", "usage"),
        (RunnerOutput(3, stderr="INTERNALERROR\n" + TARGET), "environment-error", "environment"),
        (
            RunnerOutput(1, stderr="ERROR at setup of test_target\n" + TARGET),
            "environment-error",
            "environment",
        ),
        (
            RunnerOutput(1, stderr="No module named controlled_dependency"),
            "environment-error",
            "environment",
        ),
        (RunnerOutput(None, timed_out=True), "timeout", "timeout"),
        (RunnerOutput(1, stderr="unrelated operation failed"), "unknown-failure", "unknown"),
        (
            RunnerOutput(1, stderr="AssertionError: unrelated assertion"),
            "unknown-failure",
            "assertion",
        ),
    ],
)
def test_nonverification_failure_cannot_confirm(
    tmp_path: Path, revision: str, output: RunnerOutput, actual: str, failure_class: str
) -> None:
    capsule = scenario(tmp_path, output, revision=revision)
    observed = next(item for item in capsule.observations if item.revision == revision)
    assert capsule.verdict is ProofVerdict.INCONCLUSIVE
    assert observed.actual.value == actual
    assert observed.as_json()["failure_class"] == failure_class
    assert observed.as_json()["matched_expected_failure"] is False
    if revision == "mutant":
        assert not capsule.mutation_gate_passed
    signed = sign_proof_capsule(
        capsule, key_id="test-key", signing_key=b"synthetic-proof-signing-material-32-bytes"
    )
    assert capsule_from_json(signed.as_json()) == signed


@pytest.mark.parametrize("patterns", [(), ("unrelated diagnostic",), (TARGET, "absent")])
def test_failure_requires_all_nonempty_expected_patterns(
    tmp_path: Path, patterns: tuple[str, ...]
) -> None:
    capsule = scenario(
        tmp_path,
        RunnerOutput(1, stderr=TARGET),
        command=replace(COMMAND, expected_failure=patterns),
    )
    assert capsule.verdict is ProofVerdict.INCONCLUSIVE
    assert not capsule.mutation_gate_passed


def test_truncated_assertion_output_cannot_confirm(tmp_path: Path) -> None:
    capsule = scenario(tmp_path, RunnerOutput(1, stderr="x" * 2048 + TARGET))
    assert capsule.observations[1].output_truncated
    assert capsule.verdict is ProofVerdict.INCONCLUSIVE


def test_upstream_truncation_cannot_be_hidden_by_larger_output_policy(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class TruncatedOutput(RunnerOutput):
        truncated: bool = True

    capsule = scenario(tmp_path, TruncatedOutput(1, stderr=TARGET))
    assert capsule.observations[1].output_truncated
    assert capsule.verdict is ProofVerdict.INCONCLUSIVE


def test_static_diagnostic_matches_the_trusted_verification(tmp_path: Path) -> None:
    diagnostic = "E501 intended violation"
    command = VerificationCommand(
        "static.check", ("static-check",), 10, "static", expected_failure=(diagnostic,)
    )
    capsule = scenario(
        tmp_path, RunnerOutput(2, stderr=diagnostic), command=command, valid_failure=diagnostic
    )
    assert capsule.verdict is ProofVerdict.CONFIRMED
    assert capsule.observations[1].as_json()["failure_class"] == "static-diagnostic"


def test_intended_assertion_preserves_typed_match_evidence(tmp_path: Path) -> None:
    capsule = scenario(tmp_path, RunnerOutput(1, stderr=TARGET))
    assert capsule.verdict is ProofVerdict.CONFIRMED
    observation = capsule.observations[1].as_json()
    assert observation["failure_class"] == "assertion"
    assert observation["matched_expected_failure"] is True


@pytest.mark.parametrize("argv", [("other-verifier",), ("python", "tool.py", "pytest")])
def test_pytest_exit_codes_are_not_applied_to_other_commands(
    tmp_path: Path, argv: tuple[str, ...]
) -> None:
    capsule = scenario(
        tmp_path, RunnerOutput(5, stderr=TARGET), command=replace(COMMAND, argv=argv)
    )
    assert capsule.observations[1].actual.value == "unknown-failure"
    assert capsule.verdict is ProofVerdict.INCONCLUSIVE


@pytest.mark.parametrize("argv", [("pytest.exe",), (sys.executable, "-m", "pytest")])
def test_pytest_invocation_forms_preserve_no_tests_classification(
    tmp_path: Path, argv: tuple[str, ...]
) -> None:
    capsule = scenario(tmp_path, RunnerOutput(5), command=replace(COMMAND, argv=argv))
    assert capsule.observations[1].actual.value == "no-tests"


def test_invalid_expected_pattern_rejects_before_execution(tmp_path: Path) -> None:
    with pytest.raises(ProofError, match="expected failure"):
        scenario(
            tmp_path,
            RunnerOutput(1, stderr=TARGET),
            command=replace(COMMAND, expected_failure=("[",)),
        )


def test_capsule_rejects_failure_with_success_exit(tmp_path: Path) -> None:
    capsule = scenario(tmp_path, RunnerOutput(1, stderr=TARGET))
    with pytest.raises(ProofError, match="observation"):
        replace(capsule.observations[1], exit_code=0)


@pytest.mark.parametrize(
    ("content", "actual"),
    [
        ("def test_target():\n    assert False, 'intended regression'\n", "fail"),
        ("def test_bad(\n", "collection-error"),
        (
            "def test_target():\n"
            "    print('AssertionError: intended regression')\n"
            "    raise RuntimeError('unrelated failure')\n",
            "unknown-failure",
        ),
    ],
)
def test_local_pytest_output_is_classified_without_isolation_claim(
    tmp_path: Path, content: str, actual: str
) -> None:
    probe = tmp_path / "local-probe"
    probe.mkdir()
    (probe / "test_target.py").write_text(content, encoding="utf-8")
    environment = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONIOENCODING="utf-8")
    environment.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--noconftest", "-p", "no:cacheprovider"],
        cwd=probe,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    root = tmp_path / "proof-fixture"
    root.mkdir()
    capsule = scenario(root, RunnerOutput(result.returncode, result.stdout, result.stderr))
    assert capsule.observations[1].actual.value == actual


def test_capsule_schema_validates_classified_observations(tmp_path: Path) -> None:
    from jsonschema import Draft202012Validator

    root = Path(__file__).parents[1]
    schema = json.loads((root / "schemas" / "proof-capsule.schema.json").read_text())
    capsule = scenario(tmp_path, RunnerOutput(2, stderr="ERROR collecting target"))
    signed = sign_proof_capsule(
        capsule, key_id="test-key", signing_key=b"synthetic-proof-signing-material-32-bytes"
    )
    Draft202012Validator(schema).validate(signed.as_json())


def test_capsule_match_flag_requires_a_real_boolean(tmp_path: Path) -> None:
    capsule = scenario(tmp_path, RunnerOutput(1, stderr=TARGET))
    signed = sign_proof_capsule(
        capsule, key_id="test-key", signing_key=b"synthetic-proof-signing-material-32-bytes"
    )
    payload = signed.as_json()
    payload["observations"][1]["matched_expected_failure"] = "false"
    with pytest.raises(ProofError, match=r"observation.*boolean"):
        capsule_from_json(payload)


@pytest.mark.parametrize("flags", [("-I",), ("-X", "dev"), ("-W", "ignore")])
def test_python_flags_do_not_hide_pytest_setup_errors(
    tmp_path: Path, flags: tuple[str, ...]
) -> None:
    capsule = scenario(
        tmp_path,
        RunnerOutput(1, stderr="ERROR at setup of test_target\n" + TARGET + "\n1 error"),
        command=replace(COMMAND, argv=(sys.executable, *flags, "-m", "pytest")),
    )
    assert capsule.observations[1].actual.value == "environment-error"
    assert capsule.verdict is ProofVerdict.INCONCLUSIVE


@pytest.mark.parametrize("exit_code", [2, 5])
def test_assertion_observation_rejects_nonassertion_exit(tmp_path: Path, exit_code: int) -> None:
    capsule = scenario(tmp_path, RunnerOutput(1, stderr=TARGET))
    with pytest.raises(ProofError, match="observation"):
        replace(capsule.observations[1], exit_code=exit_code)


@pytest.mark.parametrize("stdout", ["ERROR collecting example", "No module named example"])
def test_successful_verifier_output_is_not_an_error_report(tmp_path: Path, stdout: str) -> None:
    capsule = scenario(tmp_path, RunnerOutput(0, stdout=stdout), revision="base")
    assert capsule.verdict is ProofVerdict.CONFIRMED
    assert capsule.observations[0].actual.value == "pass"


@pytest.mark.parametrize(
    ("failure_class", "actual"),
    [
        ("collection", "collection-error"),
        ("no-tests", "no-tests"),
        ("usage", "usage-error"),
        ("unrecognized-runner-error", "unknown-failure"),
    ],
)
def test_runner_error_class_precedes_assertion_text(
    tmp_path: Path, failure_class: str, actual: str
) -> None:
    capsule = scenario(tmp_path, RunnerOutput(1, stderr=TARGET, failure_class=failure_class))
    assert capsule.verdict is ProofVerdict.INCONCLUSIVE
    assert capsule.observations[1].actual.value == actual


def test_capsule_mutation_gate_requires_a_real_boolean(tmp_path: Path) -> None:
    capsule = scenario(tmp_path, RunnerOutput(1, stderr=TARGET))
    signed = sign_proof_capsule(
        capsule, key_id="test-key", signing_key=b"synthetic-proof-signing-material-32-bytes"
    )
    payload = signed.as_json()
    payload["mutation_gate_passed"] = "false"
    with pytest.raises(ProofError, match="boolean"):
        capsule_from_json(payload)


def test_unrelated_exception_quoting_assertion_text_cannot_confirm(tmp_path: Path) -> None:
    capsule = scenario(tmp_path, RunnerOutput(1, stderr="RuntimeError: " + TARGET))
    assert capsule.verdict is ProofVerdict.INCONCLUSIVE


def test_custom_exception_summary_overrides_printed_assertion(tmp_path: Path) -> None:
    output = RunnerOutput(
        1, stdout=TARGET, stderr="FAILED test_target.py::test_target - Boom: other"
    )
    assert scenario(tmp_path, output).verdict is ProofVerdict.INCONCLUSIVE


def test_pytest_rewritten_assertion_matches_intended_failure(tmp_path: Path) -> None:
    diagnostic = "E       assert 1 == 2\nFAILED test_target.py::test_target - assert 1 == 2"
    capsule = scenario(
        tmp_path,
        RunnerOutput(1, stderr=diagnostic),
        command=replace(COMMAND, expected_failure=("assert 1 == 2",)),
        valid_failure=diagnostic,
    )
    assert capsule.verdict is ProofVerdict.CONFIRMED


@pytest.mark.parametrize("exception", ["RuntimeError", "AssertionError"])
def test_plain_python_traceback_overrides_printed_assertion(
    tmp_path: Path,
    exception: str,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"print({TARGET!r}); raise {exception}('intended regression\\nmore detail')",
        ],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 1
    capsule = scenario(
        tmp_path,
        RunnerOutput(result.returncode, result.stdout, result.stderr),
        command=replace(COMMAND, argv=(sys.executable, "verification.py")),
    )
    expected = (
        ProofVerdict.CONFIRMED if exception == "AssertionError" else ProofVerdict.INCONCLUSIVE
    )
    assert capsule.verdict is expected
