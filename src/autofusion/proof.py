"""Disposable, mutation-gated proof execution for review findings."""

from __future__ import annotations

import fnmatch
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
        if not isinstance(candidate_fix_visible, bool) or not isinstance(
            mutation_required, bool
        ):
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
        }


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
        mutation = observed.get("mutant")
        computed_mutation_gate = bool(
            not self.intent.mutation_required
            or (
                mutation is not None
                and mutation.expected is ProofOutcome.FAIL
                and mutation.actual is ProofOutcome.FAIL
            )
        )
        if self.mutation_gate_passed != computed_mutation_gate:
            raise ProofError("proof capsule mutation gate is inconsistent")
        if any(
            item.actual in {ProofOutcome.TIMEOUT, ProofOutcome.ENVIRONMENT_ERROR}
            for item in self.observations
        ):
            computed_verdict = ProofVerdict.INCONCLUSIVE
        elif computed_mutation_gate and all(
            item.actual is item.expected for item in self.observations
        ):
            computed_verdict = ProofVerdict.CONFIRMED
        else:
            computed_verdict = ProofVerdict.NOT_REPRODUCED
        if self.verdict is not computed_verdict:
            raise ProofError("proof capsule verdict is inconsistent with its observations")
        if self.capsule_hash and self.capsule_hash != sha256_json(self.body()):
            raise ProofError("proof capsule hash verification failed")
        if self.attestation is not None:
            if not self.capsule_hash:
                raise ProofError("proof capsule attestation requires a capsule hash")
            if self.attestation.bundle_hash != sha256_json(
                proof_attestation_bundle(self)
            ):
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


def build_overlay_manifest(root: Path, policy: ProofPolicy) -> tuple[OverlayEntry, ...]:
    resolved = root.resolve(strict=True)
    entries: list[OverlayEntry] = []
    total_bytes = 0
    for path in _iter_files(resolved):
        relative = _safe_relative(path, resolved)
        if not any(
            fnmatch.fnmatchcase(relative, pattern)
            for pattern in policy.approved_overlay_patterns
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
    _iter_files(source)
    shutil.copytree(
        source,
        destination,
        symlinks=False,
        ignore=shutil.ignore_patterns(".git", ".fusion", "__pycache__", ".pytest_cache"),
    )


def _apply_overlay(overlay_root: Path, workspace: Path, entries: tuple[OverlayEntry, ...]) -> None:
    for entry in entries:
        source = overlay_root / PurePosixPath(entry.path)
        destination = workspace / PurePosixPath(entry.path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if sha256_bytes(destination.read_bytes()) != entry.content_hash:
            raise ProofError("proof overlay changed during staging")


def _classify(output: RunnerOutput) -> tuple[ProofOutcome, str]:
    if output.timed_out:
        return ProofOutcome.TIMEOUT, "timeout"
    if output.exit_code is None:
        return ProofOutcome.ENVIRONMENT_ERROR, "runner-error"
    combined = f"{output.stdout}\n{output.stderr}"
    if output.failure_class == "environment" or _ENVIRONMENT_RE.search(combined):
        return ProofOutcome.ENVIRONMENT_ERROR, "environment-error"
    if output.exit_code == 0:
        return ProofOutcome.PASS, "completed"
    return ProofOutcome.FAIL, "completed"


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
    actual, status = _classify(output)
    bounded, truncated = bounded_text(f"{output.stdout}\n{output.stderr}", max_output_chars)
    invocation_hash = sha256_json(
        {
            "revision": revision,
            "revision_hash": revision_hash,
            "verification_id": command.verification_id,
            "argv": list(command.argv),
            "cwd": command.cwd,
            "timeout_s": command.timeout_s,
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
                output = RunnerOutput(
                    exit_code=None, stderr=str(exc), failure_class="environment"
                )
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
    mutation = next(
        (item for item in observation_tuple if item.revision == "mutant"), None
    )
    mutation_gate_passed = bool(
        not intent.mutation_required
        or (
            mutation is not None
            and mutation.expected is ProofOutcome.FAIL
            and mutation.actual is ProofOutcome.FAIL
        )
    )
    if any(
        item.actual in {ProofOutcome.TIMEOUT, ProofOutcome.ENVIRONMENT_ERROR}
        for item in observation_tuple
    ):
        verdict = ProofVerdict.INCONCLUSIVE
    elif mutation_gate_passed and all(
        item.actual is item.expected for item in observation_tuple
    ):
        verdict = ProofVerdict.CONFIRMED
    else:
        verdict = ProofVerdict.NOT_REPRODUCED
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
        observations.append(
            ProofObservation(
                revision=str(item.get("revision", "")),
                revision_hash=str(item.get("revision_hash", "")),
                expected=ProofOutcome(str(item.get("expected", ""))),
                actual=ProofOutcome(str(item.get("actual", ""))),
                exit_code=(
                    int(item["exit_code"]) if item.get("exit_code") is not None else None
                ),
                execution_status=str(item.get("execution_status", "")),
                output_hash=str(item.get("output_hash", "")),
                output_truncated=bool(item.get("output_truncated", False)),
                invocation_hash=str(item.get("invocation_hash", "")),
                runner_attestation_hash=str(item.get("runner_attestation_hash", "")),
            )
        )
    capsule = ProofCapsule(
        intent=intent,
        overlay_hash=str(raw.get("overlay_hash", "")),
        overlay_entries=tuple(entries),
        observations=tuple(observations),
        mutation_gate_passed=bool(raw.get("mutation_gate_passed", False)),
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
