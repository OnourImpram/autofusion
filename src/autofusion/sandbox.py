"""Grounding runners that prove network namespace isolation before use."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from autofusion.errors import GroundingError
from autofusion.grounding import GroundingRunner, RunnerOutput
from autofusion.providers.process import CommandOutcome, CommandRunner
from autofusion.util import sha256_json

_DOCKER_DIGEST_REFERENCE = re.compile(
    r"(?=.{1,256}\Z)[^\s,]+@sha256:[0-9a-f]{64}\Z"
)
_DOCKER_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


class _CommandExecutor(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        input_text: str,
        cwd: Path,
        timeout_s: float,
        max_output_chars: int,
        environment_allowlist: Sequence[str],
    ) -> CommandOutcome: ...


def _probe(argv: Sequence[str], timeout_s: float = 5.0) -> bool:
    try:
        result = subprocess.run(
            list(argv),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


@dataclass(slots=True)
class LinuxUnshareGroundingRunner(GroundingRunner):
    command_runner: _CommandExecutor
    executable: str = "unshare"
    network_denied: bool = True
    strong_isolation: bool = False

    @classmethod
    def detect(cls) -> LinuxUnshareGroundingRunner | None:
        executable = shutil.which("unshare")
        if executable is None or not _probe((executable, "-Urn", "--", "true")):
            return None
        return cls(command_runner=CommandRunner(), executable=executable)

    def run(self, argv: Sequence[str], cwd: Path, timeout_s: int) -> RunnerOutput:
        outcome = self.command_runner.run(
            (self.executable, "-Urn", "--", *argv),
            input_text="",
            cwd=cwd,
            timeout_s=timeout_s,
            max_output_chars=64_000,
            environment_allowlist=("PATH", "HOME", "TMPDIR"),
        )
        return RunnerOutput(
            exit_code=outcome.returncode,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            timed_out=outcome.timed_out,
        )


@dataclass(slots=True)
class WslUnshareGroundingRunner(GroundingRunner):
    command_runner: _CommandExecutor
    distro: str = "Ubuntu-24.04"
    executable: str = "wsl.exe"
    path_translator: Callable[[Path], str] | None = None
    network_denied: bool = True
    strong_isolation: bool = False

    @classmethod
    def detect(cls, distro: str = "Ubuntu-24.04") -> WslUnshareGroundingRunner | None:
        executable = shutil.which("wsl.exe")
        if executable is None or not _probe(
            (executable, "-d", distro, "--", "unshare", "-Urn", "--", "true"),
            timeout_s=10.0,
        ):
            return None
        return cls(command_runner=CommandRunner(), distro=distro, executable=executable)

    def _translate(self, path: Path) -> str:
        if self.path_translator is not None:
            return self.path_translator(path)
        try:
            result = subprocess.run(
                [
                    self.executable,
                    "-d",
                    self.distro,
                    "--",
                    "wslpath",
                    "-a",
                    "-u",
                    str(path.resolve()),
                ],
                check=False,
                capture_output=True,
                shell=False,
                timeout=5.0,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GroundingError("unable to translate snapshot path for WSL") from error
        translated = result.stdout.decode("utf-8", errors="replace").strip()
        if result.returncode != 0 or not translated.startswith("/") or len(translated) > 4096:
            raise GroundingError("WSL returned an invalid snapshot path")
        return translated

    def run(self, argv: Sequence[str], cwd: Path, timeout_s: int) -> RunnerOutput:
        linux_cwd = self._translate(cwd)
        outcome = self.command_runner.run(
            (
                self.executable,
                "-d",
                self.distro,
                "--cd",
                linux_cwd,
                "--",
                "unshare",
                "-Urn",
                "--",
                *argv,
            ),
            input_text="",
            cwd=Path(os.environ.get("SYSTEMROOT", "C:\\Windows")),
            timeout_s=timeout_s,
            max_output_chars=64_000,
            environment_allowlist=("PATH", "SystemRoot", "WINDIR", "USERPROFILE", "TEMP", "TMP"),
        )
        return RunnerOutput(
            exit_code=outcome.returncode,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            timed_out=outcome.timed_out,
        )


def detect_grounding_runner() -> GroundingRunner | None:
    """Return a runner only after a real network-namespace capability probe succeeds."""

    if os.name == "nt":
        return WslUnshareGroundingRunner.detect()
    return LinuxUnshareGroundingRunner.detect()


@dataclass(slots=True)
class DockerProofRunner:
    """Run generated tests inside a disposable, networkless Docker container."""

    command_runner: _CommandExecutor
    executable: str
    image: str
    image_id: str
    server_version: str
    memory_mb: int = 1024
    cpus: float = 1.0
    pids_limit: int = 128
    network_denied: bool = True
    strong_isolation: bool = True

    @property
    def runner_attestation_hash(self) -> str:
        return sha256_json(
            {
                "runner": "docker-proof-v1",
                "image": self.image,
                "image_id": self.image_id,
                "server_version": self.server_version,
                "network": "none",
                "read_only_root": True,
                "cap_drop": "all",
                "no_new_privileges": True,
                "memory_mb": self.memory_mb,
                "cpus": self.cpus,
                "pids_limit": self.pids_limit,
            }
        )

    @classmethod
    def detect(cls, settings: Mapping[str, object]) -> DockerProofRunner | None:
        image = settings.get("docker_image")
        if not isinstance(image, str) or not _DOCKER_DIGEST_REFERENCE.fullmatch(image):
            return None
        executable = shutil.which("docker")
        if executable is None:
            return None

        def inspect(*argv: str) -> str | None:
            try:
                result = subprocess.run(
                    [executable, *argv],
                    check=False,
                    capture_output=True,
                    shell=False,
                    timeout=10.0,
                )
            except (OSError, subprocess.TimeoutExpired):
                return None
            output = result.stdout.decode("utf-8", errors="replace").strip()
            if result.returncode != 0 or not output or len(output) > 1024:
                return None
            return output

        server_version = inspect("version", "--format", "{{.Server.Version}}")
        image_id = inspect("image", "inspect", image, "--format", "{{.Id}}")
        if (
            server_version is None
            or image_id is None
            or not _DOCKER_IMAGE_ID.fullmatch(image_id)
        ):
            return None
        memory_value = settings.get("memory_mb", 1024)
        cpus_value = settings.get("cpus", 1.0)
        pids_value = settings.get("pids_limit", 128)
        if (
            not isinstance(memory_value, int)
            or not isinstance(cpus_value, (int, float))
            or not isinstance(pids_value, int)
        ):
            return None
        memory_mb = memory_value
        cpus = float(cpus_value)
        pids_limit = pids_value
        if not 128 <= memory_mb <= 16_384 or not 0.25 <= cpus <= 8 or not 16 <= pids_limit <= 4096:
            return None
        return cls(
            command_runner=CommandRunner(),
            executable=executable,
            image=image,
            image_id=image_id,
            server_version=server_version,
            memory_mb=memory_mb,
            cpus=cpus,
            pids_limit=pids_limit,
        )

    def run(self, argv: Sequence[str], cwd: Path, timeout_s: int) -> RunnerOutput:
        workspace = cwd.resolve(strict=True)
        mount = f"type=bind,source={workspace},target=/workspace"
        outcome = self.command_runner.run(
            (
                self.executable,
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                str(self.pids_limit),
                "--memory",
                f"{self.memory_mb}m",
                "--cpus",
                str(self.cpus),
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--mount",
                mount,
                "--workdir",
                "/workspace",
                self.image_id,
                *argv,
            ),
            input_text="",
            cwd=Path(os.environ.get("SYSTEMROOT", "/")),
            timeout_s=timeout_s,
            max_output_chars=64_000,
            environment_allowlist=(
                "PATH",
                "SystemRoot",
                "WINDIR",
                "USERPROFILE",
                "TEMP",
                "TMP",
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
            ),
        )
        return RunnerOutput(
            exit_code=outcome.returncode,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            timed_out=outcome.timed_out,
        )


def detect_proof_runner(settings: Mapping[str, object]) -> DockerProofRunner | None:
    """Return only a runner that meets the generated-code isolation contract."""

    return DockerProofRunner.detect(settings)
