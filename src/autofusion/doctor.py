"""Environment diagnostics that never expose credential values."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from autofusion.config import FusionConfig
from autofusion.grounding import GroundingRunner
from autofusion.proof import ProofRunner
from autofusion.util import JsonObject, sha256_json


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: str
    detail: str

    def as_json(self) -> JsonObject:
        return {"name": self.name, "status": self.status, "detail": self.detail}


def _executable(raw: object, fallback: str) -> str:
    if isinstance(raw, dict):
        params = raw.get("params")
        if isinstance(params, dict) and isinstance(params.get("executable"), str):
            return str(params["executable"])
    return fallback


def _resolve_executable(command: str) -> str | None:
    candidate = Path(command)
    if candidate.is_absolute():
        return str(candidate) if candidate.is_file() else None
    return shutil.which(command)


def _version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            shell=False,
            timeout=10.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "version probe failed"
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace").strip()
    return output[:256] if output else f"exit={result.returncode}"


def inspect_environment(
    config: FusionConfig,
    *,
    grounding_runner: GroundingRunner | None,
    proof_runner: ProofRunner | None = None,
) -> JsonObject:
    checks: list[DoctorCheck] = []
    models = config.section("models")
    for handle, raw in models.items():
        profile = config.model(handle)
        if not profile.enabled:
            activation_gate = raw.get("activation_gate") if isinstance(raw, dict) else None
            detail = (
                f"profile disabled by config; activation gate: {activation_gate}"
                if isinstance(activation_gate, str) and activation_gate
                else "profile disabled by config"
            )
            checks.append(DoctorCheck(f"model:{handle}", "disabled", detail))
            continue
        if profile.transport == "self":
            checks.append(
                DoctorCheck(
                    f"model:{handle}",
                    "context-required",
                    "active Claude session is intentionally not callable",
                )
            )
            continue
        if profile.transport in {"codex-exec", "claude-exec"}:
            fallback = "codex" if profile.transport == "codex-exec" else "claude"
            executable = _resolve_executable(_executable(raw, fallback))
            checks.append(
                DoctorCheck(
                    f"model:{handle}",
                    "available" if executable else "missing",
                    _version(executable) if executable else f"executable not found: {fallback}",
                )
            )
            continue
        if profile.transport in {"openai-compatible", "anthropic", "anthropic-http"}:
            key_env = raw.get("key_env") if isinstance(raw, dict) else None
            available = isinstance(key_env, str) and bool(os.environ.get(key_env))
            checks.append(
                DoctorCheck(
                    f"model:{handle}",
                    "available" if available else "credential-missing",
                    f"credential variable {'is set' if available else 'is not set'}: {key_env}",
                )
            )
            continue
        checks.append(DoctorCheck(f"model:{handle}", "unsupported", profile.transport))
    checks.append(
        DoctorCheck(
            "grounding-network-isolation",
            "available" if grounding_runner is not None else "unavailable",
            type(grounding_runner).__name__ if grounding_runner is not None else "fail-closed",
        )
    )
    checks.append(
        DoctorCheck(
            "proof-disposable-isolation",
            "available" if proof_runner is not None else "unavailable",
            (
                type(proof_runner).__name__
                if proof_runner is not None
                else "configure proof.docker_image with a digest-pinned image"
            ),
        )
    )
    proof_settings = config.section("proof")
    proof_key_env = str(proof_settings.get("attestation_key_env", ""))
    proof_key = os.environ.get(proof_key_env)
    proof_key_available = proof_key is not None and len(proof_key.encode("utf-8")) >= 32
    checks.append(
        DoctorCheck(
            "proof-capsule-signing",
            "available" if proof_key_available else "credential-missing",
            (
                f"credential variable is set: {proof_key_env}"
                if proof_key_available
                else f"credential variable is not set or is too short: {proof_key_env}"
            ),
        )
    )
    blocking = [check.name for check in checks if check.status in {"missing", "unsupported"}]
    return {
        "schema_version": 1,
        "config_hash": sha256_json(config.data),
        "checks": [check.as_json() for check in checks],
        "blocking": blocking,
        "healthy": not blocking,
    }
