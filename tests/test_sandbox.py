from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from autofusion.providers.process import CommandOutcome
from autofusion.sandbox import WslUnshareGroundingRunner


class RecordingCommandRunner:
    def __init__(self) -> None:
        self.argv: tuple[str, ...] | None = None

    def run(self, argv: Sequence[str], **kwargs: object) -> CommandOutcome:
        del kwargs
        self.argv = tuple(argv)
        return CommandOutcome(tuple(argv), 1, "", "AssertionError", 1, False, False)


def test_wsl_runner_uses_fixed_argv_network_namespace_without_shell(tmp_path: Path) -> None:
    command_runner = RecordingCommandRunner()
    runner = WslUnshareGroundingRunner(
        command_runner=command_runner,
        path_translator=lambda _: "/mnt/c/snapshot",
    )
    result = runner.run(("python3", "-m", "pytest", "-q"), tmp_path, 10)
    assert result.exit_code == 1
    assert command_runner.argv == (
        "wsl.exe",
        "-d",
        "Ubuntu-24.04",
        "--cd",
        "/mnt/c/snapshot",
        "--",
        "unshare",
        "-Urn",
        "--",
        "python3",
        "-m",
        "pytest",
        "-q",
    )
