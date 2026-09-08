"""Disposable, mutation-gated proof execution for review findings."""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Protocol

from autofusion.errors import PolicyError, ProofError
from autofusion.grounding import RunnerOutput, VerificationCommand
from autofusion.policy_signing import (
    PolicyBundleSignature,
    sign_policy_bundle,
    verify_policy_bundle,
)
from autofusion.util import (
    JsonObject,
    bounded_text,
    sha256_bytes,
    sha256_json,
)

_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_ENVIRONMENT_RE = re.compile(
    r"(?i)(no module named|command not found|permission denied|environment error)"
)


class ProofRelation(StrEnum):
    REGRESSION = "regression"
    REPAIR = "repair"
    FEATURE = "feature"
    COUNTEREXAMPLE = "counterexample"


class ProofOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    ENVIRONMENT_ERROR = "environment-error"
    COLLECTION_ERROR = "collection-error"
    NO_TESTS = "no-tests"
    USAGE_ERROR = "usage-error"
    UNKNOWN_FAILURE = "unknown-failure"


class ProofFailureClass(StrEnum):
    ASSERTION = "assertion"
    STATIC_DIAGNOSTIC = "static-diagnostic"
    COLLECTION = "collection"
    NO_TESTS = "no-tests"
    TIMEOUT = "timeout"
    ENVIRONMENT = "environment"
    USAGE = "usage"
    UNKNOWN = "unknown"
    TRUNCATED = "truncated-output"


class ProofVerdict(StrEnum):
    CONFIRMED = "confirmed"
    NOT_REPRODUCED = "not-reproduced"
    INCONCLUSIVE = "inconclusive"


class ProofRunner(Protocol):
    network_denied: bool
    strong_isolation: bool

    @property
    def runner_attestation_hash(self) -> str: ...

    def run(self, argv: Sequence[str], cwd: Path, timeout_s: int) -> RunnerOutput: ...


@dataclass(frozen=True, slots=True)
class ProofPolicy:
    enabled: bool
    approved_overlay_patterns: tuple[str, ...]
    max_overlay_files: int
    max_overlay_bytes: int
    require_mutation: bool
    max_output_chars: int

    def __post_init__(self) -> None:
        if not self.approved_overlay_patterns:
            raise ProofError("proof policy requires approved overlay paths")
        if min(self.max_overlay_files, self.max_overlay_bytes, self.max_output_chars) < 1:
            raise ProofError("proof policy limits must be positive")


@dataclass(frozen=True, slots=True)
class VerificationIntent:
    proof_id: str
    fusion_run_id: str
    finding_id: str
    relation: ProofRelation
    verification_id: str
    test_author: str
    patch_author: str
    candidate_fix_visible: bool
    revision_hashes: Mapping[str, str]
    mutation_required: bool = True
    schema_version: str = "0.1"

    def __post_init__(self) -> None:
        if self.schema_version != "0.1":
            raise ProofError("unsupported verification intent schema version")
        for name, value in (
            ("proof_id", self.proof_id),
            ("fusion_run_id", self.fusion_run_id),
            ("finding_id", self.finding_id),
            ("verification_id", self.verification_id),
            ("test_author", self.test_author),
            ("patch_author", self.patch_author),
        ):
            if not _ID_RE.fullmatch(value):
                raise ProofError(f"{name} is invalid")
        if self.test_author == self.patch_author:
            raise ProofError("the test author must be independent from the patch author")
        if self.candidate_fix_visible:
            raise ProofError("proof tests cannot be generated after seeing the candidate fix")
        expected = relation_expectations(self.relation, mutation_required=self.mutation_required)
        if set(self.revision_hashes) != set(expected):
            raise ProofError("intent revision hashes do not match the declared proof relation")
        if any(not _HASH_RE.fullmatch(value) for value in self.revision_hashes.values()):
            raise ProofError("intent revision hashes must be lowercase SHA-256 values")

    def as_json(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "proof_id": self.proof_id,
            "fusion_run_id": self.fusion_run_id,
            "finding_id": self.finding_id,
            "relation": self.relation.value,
            "verification_id": self.verification_id,
            "test_author": self.test_author,
            "patch_author": self.patch_author,
            "candidate_fix_visible": self.candidate_fix_visible,
            "revision_hashes": dict(self.revision_hashes),
            "mutation_required": self.mutation_required,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, object]) -> VerificationIntent:
        revision_hashes = raw.get("revision_hashes")
        if not isinstance(revision_hashes, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in revision_hashes.items()
        ):
            raise ProofError("verification intent revision_hashes must be an object")
        candidate_fix_visible = raw.get("candidate_fix_visible")
        mutation_required = raw.get("mutation_required", True)
        if not isinstance(candidate_fix_visible, bool) or not isinstance(mutation_required, bool):
            raise ProofError("verification intent boolean fields are invalid")
        try:
            return cls(
                proof_id=str(raw["proof_id"]),
                fusion_run_id=str(raw["fusion_run_id"]),
                finding_id=str(raw["finding_id"]),
                relation=ProofRelation(str(raw["relation"])),
                verification_id=str(raw["verification_id"]),
                test_author=str(raw["test_author"]),
                patch_author=str(raw["patch_author"]),
                candidate_fix_visible=candidate_fix_visible,
                revision_hashes=dict(revision_hashes),
                mutation_required=mutation_required,
                schema_version=str(raw.get("schema_version", "")),
            )
        except (KeyError, ValueError) as exc:
            raise ProofError("verification intent is incomplete or invalid") from exc


@dataclass(frozen=True, slots=True)
class OverlayEntry:
    path: str
    size: int
    content_hash: str

    def __post_init__(self) -> None:
        candidate = PurePosixPath(self.path)
        if (
            not self.path
            or self.path.startswith("/")
            or any(part in {"", ".", ".."} for part in candidate.parts)
        ):
            raise ProofError("proof overlay entry path is invalid")
        if self.size < 0 or not _HASH_RE.fullmatch(self.content_hash):
            raise ProofError("proof overlay entry metadata is invalid")

    def as_json(self) -> JsonObject:
        return {"path": self.path, "size": self.size, "content_hash": self.content_hash}


@dataclass(frozen=True, slots=True)
class ProofObservation:
    revision: str
    revision_hash: str
    expected: ProofOutcome
    actual: ProofOutcome
    exit_code: int | None
    execution_status: str
    output_hash: str
    output_truncated: bool
    invocation_hash: str
    runner_attestation_hash: str
    failure_class: ProofFailureClass | None = None
    matched_expected_failure: bool = False

    def __post_init__(self) -> None:
        if self.revision not in {"base", "head", "fixed", "control", "mutant"}:
            raise ProofError("proof observation revision is invalid")
        for value in (
            self.revision_hash,
            self.output_hash,
            self.invocation_hash,
            self.runner_attestation_hash,
        ):
            if not _HASH_RE.fullmatch(value):
                raise ProofError("proof observation hash is invalid")
        if not isinstance(self.output_truncated, bool) or not isinstance(
            self.matched_expected_failure, bool
        ):
            raise ProofError("proof observation boolean fields are invalid")
        if self.exit_code is not None and (
            not isinstance(self.exit_code, int) or isinstance(self.exit_code, bool)
        ):
            raise ProofError("proof observation exit code is invalid")
        if self.expected not in {ProofOutcome.PASS, ProofOutcome.FAIL}:
            raise ProofError("proof observation expected outcome is invalid")
        if self.failure_class is not None and not isinstance(self.failure_class, ProofFailureClass):
            raise ProofError("proof observation classification is invalid")
        nonzero = self.exit_code is not None and self.exit_code != 0
        if self.actual is ProofOutcome.PASS:
            valid = (
                self.exit_code == 0
                and self.execution_status == "completed"
                and self.failure_class is None
                and not self.matched_expected_failure
                and not self.output_truncated
            )
        elif self.actual is ProofOutcome.FAIL:
            valid = (
                nonzero
                and self.exit_code is not None
                and self.exit_code > 0
                and self.execution_status == "completed"
                and not self.output_truncated
                and self.failure_class
                in {ProofFailureClass.ASSERTION, ProofFailureClass.STATIC_DIAGNOSTIC}
                and self.matched_expected_failure
                and (self.failure_class is not ProofFailureClass.ASSERTION or self.exit_code == 1)
            )
        else:
            classes = {
                ProofOutcome.TIMEOUT: {ProofFailureClass.TIMEOUT},
                ProofOutcome.ENVIRONMENT_ERROR: {ProofFailureClass.ENVIRONMENT},
                ProofOutcome.COLLECTION_ERROR: {ProofFailureClass.COLLECTION},
                ProofOutcome.NO_TESTS: {ProofFailureClass.NO_TESTS},
                ProofOutcome.USAGE_ERROR: {ProofFailureClass.USAGE},
                ProofOutcome.UNKNOWN_FAILURE: {
                    ProofFailureClass.UNKNOWN,
                    ProofFailureClass.ASSERTION,
                    ProofFailureClass.STATIC_DIAGNOSTIC,
                    ProofFailureClass.TRUNCATED,
                },
            }
            status = (
                "timeout"
                if self.actual is ProofOutcome.TIMEOUT
                else "runner-error"
                if self.exit_code is None
                else "environment-error"
                if self.actual is ProofOutcome.ENVIRONMENT_ERROR
                else "completed"
            )
            valid = (
                self.failure_class in classes.get(self.actual, set())
                and not self.matched_expected_failure
                and self.execution_status == status
                and (self.failure_class is not ProofFailureClass.TRUNCATED or self.output_truncated)
            )
        if not valid:
            raise ProofError("proof observation outcome is inconsistent with execution evidence")

    def as_json(self) -> JsonObject:
        return {
            "revision": self.revision,
            "revision_hash": self.revision_hash,
            "expected": self.expected.value,
            "actual": self.actual.value,
            "exit_code": self.exit_code,
            "execution_status": self.execution_status,
            "output_hash": self.output_hash,
            "output_truncated": self.output_truncated,
            "invocation_hash": self.invocation_hash,
            "runner_attestation_hash": self.runner_attestation_hash,
            "failure_class": self.failure_class.value if self.failure_class is not None else None,
            "matched_expected_failure": self.matched_expected_failure,
        }


def _proof_decision(
    observations: tuple[ProofObservation, ...], *, mutation_required: bool
) -> tuple[bool, ProofVerdict]:
    mutation = next((item for item in observations if item.revision == "mutant"), None)
    mutation_gate = not mutation_required or (
        mutation is not None
        and mutation.expected is ProofOutcome.FAIL
        and mutation.actual is ProofOutcome.FAIL
        and mutation.matched_expected_failure
    )
    if any(item.actual not in {ProofOutcome.PASS, ProofOutcome.FAIL} for item in observations):
        verdict = ProofVerdict.INCONCLUSIVE
    elif mutation_gate and all(item.actual is item.expected for item in observations):
        verdict = ProofVerdict.CONFIRMED
    else:
        verdict = ProofVerdict.NOT_REPRODUCED
    return mutation_gate, verdict


@dataclass(frozen=True, slots=True)
class ProofCapsule:
    intent: VerificationIntent
    overlay_hash: str
    overlay_entries: tuple[OverlayEntry, ...]
    observations: tuple[ProofObservation, ...]
    mutation_gate_passed: bool
    verdict: ProofVerdict
    capsule_hash: str = ""
    attestation: PolicyBundleSignature | None = None
    schema_version: str = "0.1"

    def __post_init__(self) -> None:
        if self.schema_version != "0.1":
            raise ProofError("unsupported proof capsule schema version")
        if self.capsule_hash and not _HASH_RE.fullmatch(self.capsule_hash):
            raise ProofError("proof capsule hash is invalid")
        if not self.overlay_entries:
            raise ProofError("proof capsule requires a nonempty overlay manifest")
        if self.overlay_hash != _overlay_hash(self.overlay_entries):
            raise ProofError("proof capsule overlay hash verification failed")
        expectations = relation_expectations(
            self.intent.relation,
            mutation_required=self.intent.mutation_required,
        )
        observed = {item.revision: item for item in self.observations}
        if len(observed) != len(self.observations) or set(observed) != set(expectations):
            raise ProofError("proof capsule observations do not match the declared relation")
        for revision, expected in expectations.items():
            observation = observed[revision]
            if observation.revision_hash != self.intent.revision_hashes[revision]:
                raise ProofError("proof capsule observation revision hash is inconsistent")
            if observation.expected is not expected:
                raise ProofError("proof capsule expected outcome is inconsistent")
        computed_mutation_gate, computed_verdict = _proof_decision(
            self.observations, mutation_required=self.intent.mutation_required
        )
        if self.mutation_gate_passed != computed_mutation_gate:
            raise ProofError("proof capsule mutation gate is inconsistent")
        if self.verdict is not computed_verdict:
            raise ProofError("proof capsule verdict is inconsistent with its observations")
        if self.capsule_hash and self.capsule_hash != sha256_json(self.body()):
            raise ProofError("proof capsule hash verification failed")
        if self.attestation is not None:
            if not self.capsule_hash:
                raise ProofError("proof capsule attestation requires a capsule hash")
            if self.attestation.bundle_hash != sha256_json(proof_attestation_bundle(self)):
                raise ProofError("proof capsule attestation bundle hash is inconsistent")

    def body(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "proof_id": self.intent.proof_id,
            "fusion_run_id": self.intent.fusion_run_id,
            "finding_id": self.intent.finding_id,
            "intent_hash": sha256_json(self.intent.as_json()),
            "relation": self.intent.relation.value,
            "verification_id": self.intent.verification_id,
            "test_author": self.intent.test_author,
            "patch_author": self.intent.patch_author,
            "candidate_fix_visible": self.intent.candidate_fix_visible,
            "overlay_hash": self.overlay_hash,
            "overlay_entries": [entry.as_json() for entry in self.overlay_entries],
            "observations": [observation.as_json() for observation in self.observations],
            "mutation_required": self.intent.mutation_required,
            "mutation_gate_passed": self.mutation_gate_passed,
            "verdict": self.verdict.value,
        }

    def as_json(self) -> JsonObject:
        payload: JsonObject = {**self.body(), "capsule_hash": self.capsule_hash}
        if self.attestation is not None:
            payload["attestation"] = self.attestation.as_json()
        return payload


def relation_expectations(
    relation: ProofRelation, *, mutation_required: bool
) -> dict[str, ProofOutcome]:
    expectations: dict[str, ProofOutcome]
    if relation is ProofRelation.REGRESSION:
        expectations = {"base": ProofOutcome.PASS, "head": ProofOutcome.FAIL}
    elif relation is ProofRelation.REPAIR:
        expectations = {"head": ProofOutcome.FAIL, "fixed": ProofOutcome.PASS}
    elif relation is ProofRelation.FEATURE:
        expectations = {"base": ProofOutcome.FAIL, "head": ProofOutcome.PASS}
    else:
        expectations = {"control": ProofOutcome.PASS, "head": ProofOutcome.FAIL}
    if mutation_required:
        expectations["mutant"] = ProofOutcome.FAIL
    return expectations


def _safe_relative(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ProofError("proof path escapes its declared root") from exc
    rendered = relative.as_posix()
    candidate = PurePosixPath(rendered)
    if not rendered or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ProofError("proof path is not a safe repository path")
    return rendered


def _iter_files(root: Path) -> tuple[Path, ...]:
    if not root.is_dir() or root.is_symlink():
        raise ProofError(f"proof root is unavailable or symbolic: {root}")
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ProofError(f"proof roots cannot contain symbolic links: {path}")
        if path.is_file():
            files.append(path)
    return tuple(files)


def hash_proof_tree(root: Path) -> str:
    """Hash relative paths, sizes and content hashes for proof and frozen review trees."""
    resolved = root.resolve(strict=True)
    entries: list[JsonObject] = []
    for path in _iter_files(resolved):
        relative = _safe_relative(path, resolved)
        if relative == ".fusion" or relative.startswith(".fusion/"):
            continue
        if relative == ".git" or relative.startswith(".git/"):
            continue
        content = path.read_bytes()
        entries.append(
            {
                "path": relative,
                "size": len(content),
                "content_hash": sha256_bytes(content),
            }
        )
    return sha256_json({"version": 1, "entries": entries})


def assert_reviewed_revision(capsule: ProofCapsule, reviewed_tree_hash: str) -> None:
    """Every supported relation uses head for the frozen artifact under review."""
    if (
        not _HASH_RE.fullmatch(reviewed_tree_hash)
        or capsule.intent.revision_hashes.get("head") != reviewed_tree_hash
    ):
        raise ProofError("proof head does not match the reviewed artifact")


def build_overlay_manifest(root: Path, policy: ProofPolicy) -> tuple[OverlayEntry, ...]:
    resolved = root.resolve(strict=True)
    entries: list[OverlayEntry] = []
    total_bytes = 0
    for path in _iter_files(resolved):
        relative = _safe_relative(path, resolved)
        if not any(
            fnmatch.fnmatchcase(relative, pattern) for pattern in policy.approved_overlay_patterns
        ):
            raise PolicyError(f"proof overlay touches an unapproved path: {relative}")
        content = path.read_bytes()
        if b"\x00" in content:
            raise PolicyError(f"proof overlay must contain text only: {relative}")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PolicyError(f"proof overlay is not UTF-8 text: {relative}") from exc
        total_bytes += len(content)
        entries.append(OverlayEntry(relative, len(content), sha256_bytes(content)))
    if not entries:
        raise PolicyError("proof overlay contains no approved test files")
    if len(entries) > policy.max_overlay_files:
        raise PolicyError("proof overlay exceeds its file limit")
    if total_bytes > policy.max_overlay_bytes:
        raise PolicyError("proof overlay exceeds its byte limit")
    return tuple(entries)


def _overlay_hash(entries: tuple[OverlayEntry, ...]) -> str:
    return sha256_json({"version": 1, "entries": [entry.as_json() for entry in entries]})


def _copy_revision(source: Path, destination: Path) -> None:
    # Materialize the same file-only, read-only contract as the frozen review snapshot.
    destination.mkdir(parents=True)
    for path in _iter_files(source):
        relative = _safe_relative(path, source)
        if relative.startswith((".git/", ".fusion/")) or relative in {".git", ".fusion"}:
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        os.chmod(target, 0o444)


def _apply_overlay(overlay_root: Path, workspace: Path, entries: tuple[OverlayEntry, ...]) -> None:
    for entry in entries:
        source = overlay_root / PurePosixPath(entry.path)
        destination = workspace / PurePosixPath(entry.path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            os.chmod(destination, 0o600)
        shutil.copy2(source, destination)
        if sha256_bytes(destination.read_bytes()) != entry.content_hash:
            raise ProofError("proof overlay changed during staging")


def _is_pytest(argv: tuple[str, ...]) -> bool:
    if not argv:
        return False
    executable = argv[0].replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if executable in {"pytest", "pytest.exe"}:
        return True
    if not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?", executable):
        return False
    # Interpreter options precede the terminating -m, -c, or script interface option.
    # https://docs.python.org/3.11/using/cmdline.html#interface-options
    index = 1
    while index < len(argv):
        option = argv[index]
        if option == "-m":
            return argv[index + 1 : index + 2] == ("pytest",)
        if option in {"-W", "-X", "--check-hash-based-pycs"}:
            index += 2
        elif re.fullmatch(r"-[bBdEiIOPqRsSuvx]+", option) or option.startswith(("-W", "-X")):
            index += 1
        else:
            return False
    return False


def _classify(
    command: VerificationCommand, output: RunnerOutput
) -> tuple[ProofOutcome, str, ProofFailureClass | None, bool]:
    if output.timed_out or output.failure_class == "timeout":
        return ProofOutcome.TIMEOUT, "timeout", ProofFailureClass.TIMEOUT, False
    if output.exit_code is None:
        return ProofOutcome.ENVIRONMENT_ERROR, "runner-error", ProofFailureClass.ENVIRONMENT, False
    declared_errors = {
        "environment": (ProofOutcome.ENVIRONMENT_ERROR, ProofFailureClass.ENVIRONMENT),
        "collection": (ProofOutcome.COLLECTION_ERROR, ProofFailureClass.COLLECTION),
        "collection-error": (ProofOutcome.COLLECTION_ERROR, ProofFailureClass.COLLECTION),
        "no-tests": (ProofOutcome.NO_TESTS, ProofFailureClass.NO_TESTS),
        "usage": (ProofOutcome.USAGE_ERROR, ProofFailureClass.USAGE),
        "usage-error": (ProofOutcome.USAGE_ERROR, ProofFailureClass.USAGE),
    }
    if output.failure_class in declared_errors:
        declared, failure_class = declared_errors[output.failure_class]
        status = "environment-error" if declared is ProofOutcome.ENVIRONMENT_ERROR else "completed"
        return declared, status, failure_class, False
    if output.failure_class not in {None, "assertion", "static-diagnostic"}:
        return ProofOutcome.UNKNOWN_FAILURE, "completed", ProofFailureClass.UNKNOWN, False
    if output.exit_code == 0:
        if output.truncated:
            return ProofOutcome.UNKNOWN_FAILURE, "completed", ProofFailureClass.TRUNCATED, False
        if output.failure_class is not None:
            return ProofOutcome.UNKNOWN_FAILURE, "completed", ProofFailureClass.UNKNOWN, False
        return ProofOutcome.PASS, "completed", None, False
    combined = f"{output.stdout}\n{output.stderr}"
    if _is_pytest(command.argv):
        # Exit codes alone do not distinguish test assertions from fixture/collection errors.
        # https://docs.pytest.org/en/stable/reference/exit-codes.html
        if re.search(r"(?i)(ERROR collecting|errors? during collection)", combined):
            return ProofOutcome.COLLECTION_ERROR, "completed", ProofFailureClass.COLLECTION, False
        if output.exit_code == 5:
            return ProofOutcome.NO_TESTS, "completed", ProofFailureClass.NO_TESTS, False
        if output.exit_code == 4:
            return ProofOutcome.USAGE_ERROR, "completed", ProofFailureClass.USAGE, False
        if output.exit_code == 3 or re.search(
            r"(?im)(ERROR at (?:setup|teardown)|^ERROR\s|\b[1-9]\d* errors?\b)", combined
        ):
            return (
                ProofOutcome.ENVIRONMENT_ERROR,
                "environment-error",
                ProofFailureClass.ENVIRONMENT,
                False,
            )
        if output.exit_code not in {0, 1}:
            return ProofOutcome.UNKNOWN_FAILURE, "completed", ProofFailureClass.UNKNOWN, False
    if output.failure_class == "environment" or _ENVIRONMENT_RE.search(combined):
        return (
            ProofOutcome.ENVIRONMENT_ERROR,
            "environment-error",
            ProofFailureClass.ENVIRONMENT,
            False,
        )
    if output.truncated:
        return ProofOutcome.UNKNOWN_FAILURE, "completed", ProofFailureClass.TRUNCATED, False
    failure_class = ProofFailureClass.UNKNOWN
    if output.exit_code > 0 and command.kind == "static":
        failure_class = ProofFailureClass.STATIC_DIAGNOSTIC
    elif output.exit_code == 1 and re.search(
        r"(?m)^\s*(?:(?:E\s+)?AssertionError(?::|\s*$)|E\s+assert\s)", combined
    ):
        failure_class = ProofFailureClass.ASSERTION
        traceback = output.stderr.rpartition("Traceback (most recent call last):")
        if traceback[1]:
            # Python emits the actual exception after indented traceback frames.
            exception = re.search(r"(?m)^([^\s:\n]+)(?::|$)", traceback[2])
            if exception is None or exception[1] != "AssertionError":
                failure_class = ProofFailureClass.UNKNOWN
        if any(
            re.match(r"(?:AssertionError\b|assert(?:\s|$))", summary) is None
            for summary in re.findall(r"(?m)^FAILED\s+[^\n]+? - ([^\n]+)", combined)
        ):
            failure_class = ProofFailureClass.UNKNOWN
    matched = (
        failure_class in {ProofFailureClass.ASSERTION, ProofFailureClass.STATIC_DIAGNOSTIC}
        and bool(command.expected_failure)
        and all(
            re.search(pattern, combined, flags=re.MULTILINE) is not None
            for pattern in command.expected_failure
        )
    )
    return (
        ProofOutcome.FAIL if matched else ProofOutcome.UNKNOWN_FAILURE,
        "completed",
        failure_class,
        matched,
    )


def _observation(
    *,
    revision: str,
    revision_hash: str,
    expected: ProofOutcome,
    command: VerificationCommand,
    output: RunnerOutput,
    runner: ProofRunner,
    max_output_chars: int,
) -> ProofObservation:
    bounded, truncated = bounded_text(f"{output.stdout}\n{output.stderr}", max_output_chars)
    truncated = truncated or output.truncated
    actual, status, failure_class, matched = _classify(
        command, replace(output, truncated=truncated)
    )
    invocation_hash = sha256_json(
        {
            "revision": revision,
            "revision_hash": revision_hash,
            "verification_id": command.verification_id,
            "argv": list(command.argv),
            "cwd": command.cwd,
            "timeout_s": command.timeout_s,
            "kind": command.kind,
            "expected_failure": list(command.expected_failure),
            "network": "deny",
            "isolation": "disposable",
            "runner_attestation_hash": runner.runner_attestation_hash,
        }
    )
    return ProofObservation(
        revision=revision,
        revision_hash=revision_hash,
        expected=expected,
        actual=actual,
        exit_code=output.exit_code,
        execution_status=status,
        output_hash=sha256_bytes(bounded.encode("utf-8")),
        output_truncated=truncated,
        invocation_hash=invocation_hash,
        runner_attestation_hash=runner.runner_attestation_hash,
        failure_class=failure_class,
        matched_expected_failure=matched,
    )


def _capsule(
    intent: VerificationIntent,
    entries: tuple[OverlayEntry, ...],
    observations: tuple[ProofObservation, ...],
    *,
    mutation_gate_passed: bool,
    verdict: ProofVerdict,
) -> ProofCapsule:
    draft = ProofCapsule(
        intent=intent,
        overlay_hash=_overlay_hash(entries),
        overlay_entries=entries,
        observations=observations,
        mutation_gate_passed=mutation_gate_passed,
        verdict=verdict,
    )
    return replace(draft, capsule_hash=sha256_json(draft.body()))


def proof_attestation_bundle(capsule: ProofCapsule) -> JsonObject:
    """Return the domain-separated public data covered by the local HMAC."""

    if not _HASH_RE.fullmatch(capsule.capsule_hash):
        raise ProofError("proof capsule must be hashed before attestation")
    return {
        "domain": "autofusion-proof-capsule-v1",
        "capsule_hash": capsule.capsule_hash,
        "runner_attestation_hashes": sorted(
            {item.runner_attestation_hash for item in capsule.observations}
        ),
    }


def sign_proof_capsule(
    capsule: ProofCapsule,
    *,
    key_id: str,
    signing_key: bytes | bytearray | memoryview,
) -> ProofCapsule:
    """Sign a completed capsule without persisting the secret material."""

    signature = sign_policy_bundle(
        proof_attestation_bundle(capsule),
        key_id=key_id,
        signing_key=signing_key,
    )
    return replace(capsule, attestation=signature)


def verify_proof_capsule_attestation(
    capsule: ProofCapsule,
    *,
    key_id: str,
    verification_key: bytes | bytearray | memoryview,
) -> ProofCapsule:
    """Require one active local key before a capsule may affect reconciliation."""

    if capsule.attestation is None:
        raise PolicyError("proof capsule is missing its local attestation signature")
    verify_policy_bundle(
        proof_attestation_bundle(capsule),
        capsule.attestation,
        {key_id: verification_key},
        allowed_key_ids={key_id},
    )
    return capsule


def run_proof(
    intent: VerificationIntent,
    *,
    revisions: Mapping[str, Path],
    overlay_root: Path,
    command: VerificationCommand,
    runner: ProofRunner,
    policy: ProofPolicy,
    work_root: Path,
) -> ProofCapsule:
    """Execute one proof relation without mutating any supplied revision root."""

    if not policy.enabled:
        raise PolicyError("proof execution is disabled by policy")
    if not runner.network_denied or not runner.strong_isolation:
        raise PolicyError("proof execution requires network denial and disposable isolation")
    if not _HASH_RE.fullmatch(runner.runner_attestation_hash):
        raise PolicyError("proof runner attestation is unavailable")
    if policy.require_mutation and not intent.mutation_required:
        raise PolicyError("proof policy requires a mutation gate")
    if intent.verification_id != command.verification_id:
        raise ProofError("proof intent and trusted verification command do not match")
    try:
        for pattern in command.expected_failure:
            if not pattern.strip():
                raise ProofError("proof expected failure patterns must be nonempty")
            re.compile(pattern)
    except re.error as error:
        raise ProofError("proof expected failure pattern is invalid") from error
    expectations = relation_expectations(
        intent.relation, mutation_required=intent.mutation_required
    )
    if set(revisions) != set(expectations):
        raise ProofError("supplied revisions do not match the proof relation")
    entries = build_overlay_manifest(overlay_root, policy)
    resolved_revisions: dict[str, Path] = {}
    for name, source in revisions.items():
        resolved = source.resolve(strict=True)
        actual_hash = hash_proof_tree(resolved)
        if actual_hash != intent.revision_hashes[name]:
            raise ProofError(f"revision hash changed before proof execution: {name}")
        resolved_revisions[name] = resolved
    work_root.mkdir(parents=True, exist_ok=True)
    observations: list[ProofObservation] = []
    with tempfile.TemporaryDirectory(prefix=f"{intent.proof_id}-", dir=work_root) as temporary:
        temporary_root = Path(temporary).resolve()
        for revision, expected in expectations.items():
            source = resolved_revisions[revision]
            workspace = temporary_root / revision
            _copy_revision(source, workspace)
            if hash_proof_tree(workspace) != intent.revision_hashes[revision]:
                raise ProofError(f"staged revision does not match the reviewed intent: {revision}")
            _apply_overlay(overlay_root.resolve(strict=True), workspace, entries)
            cwd = (workspace / command.cwd).resolve(strict=False)
            try:
                cwd.relative_to(workspace)
            except ValueError as exc:
                raise ProofError("proof command working directory escapes its workspace") from exc
            try:
                output = runner.run(command.argv, cwd, command.timeout_s)
            except TimeoutError:
                output = RunnerOutput(exit_code=None, timed_out=True)
            except OSError as exc:
                output = RunnerOutput(exit_code=None, stderr=str(exc), failure_class="environment")
            observations.append(
                _observation(
                    revision=revision,
                    revision_hash=intent.revision_hashes[revision],
                    expected=expected,
                    command=command,
                    output=output,
                    runner=runner,
                    max_output_chars=policy.max_output_chars,
                )
            )
            if hash_proof_tree(source) != intent.revision_hashes[revision]:
                raise ProofError(f"proof execution mutated its source revision: {revision}")
    observation_tuple = tuple(observations)
    mutation_gate_passed, verdict = _proof_decision(
        observation_tuple, mutation_required=intent.mutation_required
    )
    return _capsule(
        intent,
        entries,
        observation_tuple,
        mutation_gate_passed=mutation_gate_passed,
        verdict=verdict,
    )


def proof_policy_from_config(value: Mapping[str, object]) -> ProofPolicy:
    patterns = value.get("approved_overlay_patterns", [])
    if not isinstance(patterns, list) or not all(isinstance(item, str) for item in patterns):
        raise ProofError("proof approved_overlay_patterns must be an array of strings")
    max_overlay_files = value.get("max_overlay_files", 16)
    max_overlay_bytes = value.get("max_overlay_bytes", 262_144)
    max_output_chars = value.get("max_output_chars", 32_000)
    if (
        not isinstance(max_overlay_files, int)
        or not isinstance(max_overlay_bytes, int)
        or not isinstance(max_output_chars, int)
    ):
        raise ProofError("proof numeric policy limits must be integers")
    return ProofPolicy(
        enabled=bool(value.get("enabled", False)),
        approved_overlay_patterns=tuple(patterns),
        max_overlay_files=max_overlay_files,
        max_overlay_bytes=max_overlay_bytes,
        require_mutation=bool(value.get("require_mutation", True)),
        max_output_chars=max_output_chars,
    )


def capsule_from_json(raw: Mapping[str, object]) -> ProofCapsule:
    """Rebuild and cryptographically verify a persisted proof capsule."""

    overlay_raw = raw.get("overlay_entries")
    observations_raw = raw.get("observations")
    if not isinstance(overlay_raw, list) or not isinstance(observations_raw, list):
        raise ProofError("proof capsule entries or observations are invalid")
    mutation_gate_passed = raw.get("mutation_gate_passed")
    if not isinstance(mutation_gate_passed, bool):
        raise ProofError("proof capsule mutation gate boolean is invalid")
    attestation_raw = raw.get("attestation")
    if not isinstance(attestation_raw, dict):
        raise ProofError("proof capsule attestation is missing or invalid")
    try:
        attestation = PolicyBundleSignature(
            key_id=str(attestation_raw["key_id"]),
            bundle_hash=str(attestation_raw["bundle_hash"]),
            signature=str(attestation_raw["signature"]),
            algorithm=str(attestation_raw["algorithm"]),
            version=int(attestation_raw["version"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProofError("proof capsule attestation is incomplete") from exc
    intent_raw: JsonObject = {
        "schema_version": str(raw.get("schema_version", "")),
        "proof_id": raw.get("proof_id"),
        "fusion_run_id": raw.get("fusion_run_id"),
        "finding_id": raw.get("finding_id"),
        "relation": raw.get("relation"),
        "verification_id": raw.get("verification_id"),
        "test_author": raw.get("test_author"),
        "patch_author": raw.get("patch_author"),
        "candidate_fix_visible": raw.get("candidate_fix_visible"),
        "revision_hashes": {
            str(item.get("revision")): str(item.get("revision_hash"))
            for item in observations_raw
            if isinstance(item, dict)
        },
        "mutation_required": raw.get("mutation_required", True),
    }
    intent = VerificationIntent.from_json(intent_raw)
    entries: list[OverlayEntry] = []
    for item in overlay_raw:
        if not isinstance(item, dict):
            raise ProofError("proof capsule overlay entry is invalid")
        entries.append(
            OverlayEntry(
                path=str(item.get("path", "")),
                size=int(item.get("size", -1)),
                content_hash=str(item.get("content_hash", "")),
            )
        )
    observations: list[ProofObservation] = []
    for item in observations_raw:
        if not isinstance(item, dict):
            raise ProofError("proof capsule observation is invalid")
        for boolean_field in ("output_truncated", "matched_expected_failure"):
            if not isinstance(item.get(boolean_field), bool):
                raise ProofError("proof observation boolean fields are invalid")
        exit_code = item.get("exit_code")
        if exit_code is not None and (
            not isinstance(exit_code, int) or isinstance(exit_code, bool)
        ):
            raise ProofError("proof observation exit code is invalid")
        try:
            if not isinstance(item.get("expected"), str) or not isinstance(item.get("actual"), str):
                raise ProofError("proof observation classification is invalid")
            expected = ProofOutcome(item["expected"])
            actual = ProofOutcome(item["actual"])
            failure_class = (
                ProofFailureClass(item["failure_class"])
                if item.get("failure_class") is not None
                else None
            )
        except (TypeError, ValueError) as error:
            raise ProofError("proof observation classification is invalid") from error
        observations.append(
            ProofObservation(
                revision=str(item.get("revision", "")),
                revision_hash=str(item.get("revision_hash", "")),
                expected=expected,
                actual=actual,
                exit_code=exit_code,
                execution_status=str(item.get("execution_status", "")),
                output_hash=str(item.get("output_hash", "")),
                output_truncated=item["output_truncated"],
                invocation_hash=str(item.get("invocation_hash", "")),
                runner_attestation_hash=str(item.get("runner_attestation_hash", "")),
                failure_class=failure_class,
                matched_expected_failure=item["matched_expected_failure"],
            )
        )
    capsule = ProofCapsule(
        intent=intent,
        overlay_hash=str(raw.get("overlay_hash", "")),
        overlay_entries=tuple(entries),
        observations=tuple(observations),
        mutation_gate_passed=mutation_gate_passed,
        verdict=ProofVerdict(str(raw.get("verdict", ""))),
        capsule_hash=str(raw.get("capsule_hash", "")),
        attestation=attestation,
        schema_version=str(raw.get("schema_version", "")),
    )
    if capsule.overlay_hash != _overlay_hash(capsule.overlay_entries):
        raise ProofError("proof capsule overlay hash verification failed")
    if capsule.capsule_hash != sha256_json(capsule.body()):
        raise ProofError("proof capsule hash verification failed")
    if raw.get("intent_hash") != sha256_json(intent.as_json()):
        raise ProofError("proof capsule intent hash verification failed")
    return capsule
