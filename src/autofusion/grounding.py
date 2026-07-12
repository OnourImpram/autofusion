"""Trusted argv-only verification with fail-closed network isolation."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from autofusion.config import FusionConfig
from autofusion.errors import GroundingError, PolicyError
from autofusion.util import JsonObject, bounded_text, sha256_json, sha256_text


@dataclass(frozen=True, slots=True)
class VerificationCommand:
    verification_id: str
    argv: tuple[str, ...]
    timeout_s: int
    kind: str
    cwd: str = "."
    expected_failure: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunnerOutput:
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    failure_class: str | None = None


class GroundingRunner(Protocol):
    network_denied: bool

    def run(self, argv: Sequence[str], cwd: Path, timeout_s: int) -> RunnerOutput: ...


@dataclass(frozen=True, slots=True)
class GroundingResult:
    verification_id: str
    verdict: str
    execution_status: str
    exit_code: int | None
    failure_class: str | None
    matched_expected_failure: bool
    output: str
    output_truncated: bool
    output_hash: str
    invocation_hash: str

    def as_json(self) -> JsonObject:
        return {
            "verification_id": self.verification_id,
            "verdict": self.verdict,
            "execution_status": self.execution_status,
            "exit_code": self.exit_code,
            "failure_class": self.failure_class,
            "matched_expected_failure": self.matched_expected_failure,
            "output_hash": self.output_hash,
            "invocation_hash": self.invocation_hash,
        }


def _config_data(config: FusionConfig | Mapping[str, object]) -> Mapping[str, object]:
    return config.data if isinstance(config, FusionConfig) else config


def resolve_verification(
    config: FusionConfig | Mapping[str, object], verification_id: str
) -> VerificationCommand:
    """Resolve a policy-owned verification ID, never reviewer-provided command text."""

    profile_name, separator, command_name = verification_id.partition(".")
    if not separator or not profile_name or not command_name:
        raise GroundingError("verification ID must be profile.command")
    data = _config_data(config)
    verification = data.get("verification")
    if not isinstance(verification, Mapping):
        raise GroundingError("verification registry is unavailable")
    profiles = verification.get("profiles")
    if not isinstance(profiles, Mapping) or not isinstance(profiles.get(profile_name), Mapping):
        raise GroundingError(f"unknown verification ID: {verification_id}")
    commands = profiles[profile_name].get("commands")
    if not isinstance(commands, Mapping) or not isinstance(commands.get(command_name), Mapping):
        raise GroundingError(f"unknown verification ID: {verification_id}")
    raw = commands[command_name]
    argv = raw.get("argv")
    timeout_s = raw.get("timeout_s")
    kind = raw.get("kind")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and item for item in argv)
        or not isinstance(timeout_s, int)
        or timeout_s <= 0
        or not isinstance(kind, str)
    ):
        raise GroundingError(f"invalid verification definition: {verification_id}")
    expected: tuple[str, ...]
    expected_raw = raw.get("expected_failure", ())
    if isinstance(expected_raw, str):
        expected = (expected_raw,)
    elif isinstance(expected_raw, list) and all(isinstance(item, str) for item in expected_raw):
        expected = tuple(expected_raw)
    elif expected_raw == ():
        expected = ()
    else:
        raise GroundingError(f"invalid expected failure: {verification_id}")
    return VerificationCommand(
        verification_id=verification_id,
        argv=tuple(argv),
        timeout_s=timeout_s,
        kind=kind,
        cwd=str(raw.get("cwd", ".")),
        expected_failure=expected,
    )


def _classify_failure(command: VerificationCommand, output: RunnerOutput) -> str | None:
    if output.failure_class is not None:
        return output.failure_class
    combined = f"{output.stdout}\n{output.stderr}"
    environment_pattern = (
        r"(?i)(no module named|command not found|permission denied|environment error)"
    )
    if re.search(environment_pattern, combined):
        return "environment"
    if command.kind == "static":
        return "static-diagnostic"
    if re.search(r"(?i)(assertionerror|\bfailed\b|\bassert\b)", combined):
        return "assertion"
    return "other"


def _within_snapshot(root: Path, relative_cwd: str) -> Path:
    candidate = (root / relative_cwd).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise GroundingError("verification working directory escapes snapshot") from exc
    return candidate


def run_grounding(
    command: VerificationCommand,
    *,
    snapshot_root: Path,
    runner: GroundingRunner | None,
    max_output_chars: int,
) -> GroundingResult:
    """Execute an injected, network-denied runner and classify its evidence conservatively."""

    if runner is None or not runner.network_denied:
        raise PolicyError("grounding is blocked without a verified network-denied runner")
    cwd = _within_snapshot(snapshot_root, command.cwd)
    invocation_hash = sha256_json(
        {
            "verification_id": command.verification_id,
            "argv": list(command.argv),
            "cwd": command.cwd,
            "timeout_s": command.timeout_s,
            "network": "deny",
        }
    )
    try:
        executed = runner.run(command.argv, cwd, command.timeout_s)
    except TimeoutError:
        executed = RunnerOutput(exit_code=None, timed_out=True)
    except OSError as exc:
        executed = RunnerOutput(exit_code=None, stderr=str(exc), failure_class="environment")
    output, truncated = bounded_text(f"{executed.stdout}\n{executed.stderr}", max_output_chars)
    if executed.timed_out:
        verdict, status, failure_class, matched = "timeout", "timeout", "timeout", False
    elif executed.exit_code == 0:
        verdict, status, failure_class, matched = "not-reproduced", "completed", None, False
    elif executed.exit_code is None:
        verdict, status, failure_class, matched = (
            "environment_error",
            "runner-error",
            "environment",
            False,
        )
    else:
        failure_class = _classify_failure(command, executed)
        matched = bool(command.expected_failure) and all(
            re.search(pattern, output, flags=re.MULTILINE) is not None
            for pattern in command.expected_failure
        )
        if failure_class in {"assertion", "static-diagnostic"} and matched:
            verdict, status = "confirmed", "completed"
        elif failure_class == "environment":
            verdict, status = "environment_error", "completed"
        else:
            verdict, status = "inconclusive", "completed"
    return GroundingResult(
        verification_id=command.verification_id,
        verdict=verdict,
        execution_status=status,
        exit_code=executed.exit_code,
        failure_class=failure_class,
        matched_expected_failure=matched,
        output=output,
        output_truncated=truncated,
        output_hash=sha256_text(output),
        invocation_hash=invocation_hash,
    )
