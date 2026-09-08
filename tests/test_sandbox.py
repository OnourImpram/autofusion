from __future__ import annotations

import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from autofusion import sandbox
from autofusion.budget import BudgetLedger
from autofusion.errors import GroundingError, PolicyError
from autofusion.grounding import VerificationCommand, run_grounding
from autofusion.models import RunBudget
from autofusion.providers.process import CommandOutcome
from autofusion.sandbox import (
    DockerProofRunner,
    LinuxUnshareGroundingRunner,
    WslUnshareGroundingRunner,
)


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


def test_wsl_translation_consumes_the_verification_deadline(tmp_path: Path) -> None:
    command_runner = RecordingCommandRunner()

    def slow_translation(path: Path) -> str:
        time.sleep(0.1)
        return "/mnt/c/snapshot"

    runner = WslUnshareGroundingRunner(
        command_runner=command_runner, path_translator=slow_translation
    )
    outcome = runner.run(("pytest", "-q"), tmp_path, 0.05)
    assert outcome.timed_out
    assert command_runner.argv is None


def test_orchestrated_wsl_grounding_is_blocked_before_launch(tmp_path: Path) -> None:
    command_runner = RecordingCommandRunner()
    runner = WslUnshareGroundingRunner(
        command_runner=command_runner, path_translator=lambda _: "/mnt/c/snapshot"
    )
    with pytest.raises(PolicyError, match="total execution deadline"):
        run_grounding(
            VerificationCommand("python.pytest", ("pytest", "-q"), 30, "dynamic"),
            snapshot_root=tmp_path, runner=runner, max_output_chars=128,
            budget=BudgetLedger(RunBudget(1, 1, None, 128)),
        )
    assert command_runner.argv is None


def test_linux_runner_uses_fixed_unshare_argv(tmp_path: Path) -> None:
    command_runner = RecordingCommandRunner()
    runner = LinuxUnshareGroundingRunner(
        command_runner=command_runner,
        executable="/usr/bin/unshare",
    )
    result = runner.run(("pytest", "-q"), tmp_path, 12)
    assert result.exit_code == 1
    assert command_runner.argv == (
        "/usr/bin/unshare",
        "-Urn",
        "--",
        "pytest",
        "-q",
    )


def test_docker_proof_runner_applies_disposable_security_controls(tmp_path: Path) -> None:
    command_runner = RecordingCommandRunner()
    image_digest = "1" * 64
    image_id = "sha256:" + "a" * 64
    runner = DockerProofRunner(
        command_runner=command_runner,
        executable="docker",
        image=f"autofusion-proof@sha256:{image_digest}",
        image_id=image_id,
        server_version="28.0.0",
    )
    result = runner.run(("pytest", "-q"), tmp_path, 30)
    assert result.exit_code == 1
    assert runner.network_denied and runner.strong_isolation
    assert len(runner.runner_attestation_hash) == 64
    assert command_runner.argv is not None
    argv = command_runner.argv
    for required in (
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "no-new-privileges",
        "--pids-limit",
        "--memory",
        "--cpus",
        "--tmpfs",
    ):
        assert required in argv
    assert argv[-3:] == (image_id, "pytest", "-q")
    assert runner.image not in argv


def test_probe_and_grounding_detection_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0),
    )
    assert sandbox._probe(("controlled",))

    def timeout(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired("controlled", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert not sandbox._probe(("controlled",))

    monkeypatch.setattr("autofusion.sandbox.shutil.which", lambda _name: None)
    assert LinuxUnshareGroundingRunner.detect() is None
    assert WslUnshareGroundingRunner.detect() is None


def test_wsl_translation_validates_subprocess_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = WslUnshareGroundingRunner(command_runner=RecordingCommandRunner())
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, stdout=b"/mnt/c/snapshot\n"
        ),
    )
    assert runner._translate(tmp_path) == "/mnt/c/snapshot"

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, stdout=b"bad"),
    )
    with pytest.raises(GroundingError, match="invalid snapshot path"):
        runner._translate(tmp_path)

    def fail(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise OSError("controlled")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(GroundingError, match="translate snapshot"):
        runner._translate(tmp_path)


def test_docker_detection_attests_image_and_rejects_invalid_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "autofusion.sandbox.shutil.which", lambda _name: "C:\\docker.exe"
    )

    def inspect(
        argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        if "version" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=b"28.1.0\n")
        return subprocess.CompletedProcess(
            argv, 0, stdout=("sha256:" + "b" * 64).encode("ascii")
        )

    monkeypatch.setattr(subprocess, "run", inspect)
    image_digest = "1" * 64
    runner = DockerProofRunner.detect(
        {
            "docker_image": f"proof@sha256:{image_digest}",
            "memory_mb": 512,
            "cpus": 0.5,
            "pids_limit": 64,
        }
    )
    assert runner is not None
    assert runner.image_id == "sha256:" + "b" * 64
    assert runner.memory_mb == 512
    assert runner.cpus == 0.5
    assert runner.pids_limit == 64
    assert sandbox.detect_proof_runner({"docker_image": None}) is None

    assert DockerProofRunner.detect({"docker_image": ""}) is None
    assert DockerProofRunner.detect({"docker_image": "proof:latest"}) is None
    assert (
        DockerProofRunner.detect(
            {
                "docker_image": f"proof@sha256:{image_digest}",
                "memory_mb": "too-much",
            }
        )
        is None
    )
    assert (
        DockerProofRunner.detect(
            {"docker_image": f"proof@sha256:{image_digest}", "memory_mb": 64}
        )
        is None
    )


def test_docker_detection_rejects_failed_or_malformed_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("autofusion.sandbox.shutil.which", lambda _name: "docker")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 1, stdout=b""),
    )
    image = f"proof@sha256:{'1' * 64}"
    assert DockerProofRunner.detect({"docker_image": image}) is None

    calls = 0

    def malformed(
        argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal calls
        calls += 1
        output = b"28.1.0" if calls == 1 else b"not-a-content-id"
        return subprocess.CompletedProcess(argv, 0, stdout=output)

    monkeypatch.setattr(subprocess, "run", malformed)
    assert DockerProofRunner.detect({"docker_image": image}) is None
