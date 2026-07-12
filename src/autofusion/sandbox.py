"""Grounding runners that prove network namespace isolation before use."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from autofusion.errors import GroundingError
from autofusion.grounding import GroundingRunner, RunnerOutput
from autofusion.providers.process import CommandOutcome, CommandRunner


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
