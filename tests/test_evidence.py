from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

import autofusion.snapshot as snapshot_module
from autofusion.dlp import DlpPolicy, preflight_text, scan_snapshot_for_secrets
from autofusion.errors import GroundingError, PolicyError, ReceiptError, SnapshotError
from autofusion.evidence import EvidenceLedger
from autofusion.grounding import RunnerOutput, resolve_verification, run_grounding
from autofusion.models import SnapshotManifest
from autofusion.packet import compile_packet
from autofusion.snapshot import SnapshotLimits, assert_snapshot_fresh, build_snapshot


class FakeRunner:
    network_denied = True

    def __init__(self, output: RunnerOutput) -> None:
        self.output = output
        self.received_argv: tuple[str, ...] | None = None

    def run(self, argv: Sequence[str], cwd: Path, timeout_s: float) -> RunnerOutput:
        self.received_argv = tuple(argv)
        return self.output


class UnsafeRunner(FakeRunner):
    network_denied = False


def _config(command: dict[str, object]) -> dict[str, object]:
    return {
        "verification": {
            "profiles": {"python": {"commands": {"pytest": command}}},
            "network": "deny",
        }
    }


def _snapshot(tmp_path: Path) -> tuple[Path, SnapshotManifest]:
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("assert True\n", encoding="utf-8")
    return source, build_snapshot(source, tmp_path / "snapshots")


def test_snapshot_rejects_destination_traversal_and_stale_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "safe.txt").write_text("safe", encoding="utf-8")
    with pytest.raises(SnapshotError):
        build_snapshot(source, source / "snapshots")
    manifest = build_snapshot(source, tmp_path / "snapshots")
    (source / "safe.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(SnapshotError, match="changed"):
        assert_snapshot_fresh(manifest)


def test_snapshot_rejects_symlink_escape(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "safe.txt").write_text("safe", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    try:
        (source / "escape.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows test host")
    with pytest.raises(SnapshotError, match="link"):
        build_snapshot(source, tmp_path / "snapshots")


def test_snapshot_detects_mutation_while_freezing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    changing_file = source / "safe.txt"
    changing_file.write_text("before", encoding="utf-8")
    original = snapshot_module._stable_file_bytes
    calls = 0

    def mutate_after_read(path: Path, limits: SnapshotLimits) -> bytes:
        nonlocal calls
        payload = original(path, limits)
        calls += 1
        if calls == 2:
            changing_file.write_text("after", encoding="utf-8")
        return payload

    monkeypatch.setattr(snapshot_module, "_stable_file_bytes", mutate_after_read)
    with pytest.raises(SnapshotError, match="changed"):
        build_snapshot(source, tmp_path / "snapshots")


def test_snapshot_limits_and_packet_require_one_frozen_manifest(tmp_path: Path) -> None:
    source, manifest = _snapshot(tmp_path)
    with pytest.raises(ValueError, match="absent"):
        compile_packet(manifest, task="review", artifact_paths=("../secret",))
    packet = compile_packet(manifest, task="review", artifact_paths=("app.py",))
    assert packet.payload["snapshot"]["snapshot_hash"] == manifest.snapshot_hash
    assert packet.packet_hash
    with pytest.raises(SnapshotError, match="exceeds"):
        build_snapshot(
            source,
            tmp_path / "small",
            limits=SnapshotLimits(max_file_bytes=1, max_total_bytes=1),
        )


def test_snapshot_excludes_runtime_and_dependency_state(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / ".git").mkdir(parents=True)
    (source / ".git" / "config").write_text("secret remote", encoding="utf-8")
    (source / ".fusion" / "runs").mkdir(parents=True)
    (source / ".fusion" / "runs" / "old.json").write_text("{}", encoding="utf-8")
    (source / "app.py").write_text("print('safe')", encoding="utf-8")
    manifest = build_snapshot(source, tmp_path / "snapshots")
    assert {entry["path"] for entry in manifest.entries} == {"app.py"}


def test_dlp_redacts_and_blocks_leakage() -> None:
    secret = "API_KEY=super-secret-value"
    redacted = preflight_text(secret)
    assert redacted.matches
    assert secret not in redacted.text
    with pytest.raises(PolicyError, match="blocked"):
        preflight_text(secret, DlpPolicy(action="block"))


def test_packet_compilation_preflights_dlp(tmp_path: Path) -> None:
    _, manifest = _snapshot(tmp_path)
    packet = compile_packet(
        manifest,
        task="review",
        constraints={"credential": "API_KEY=super-secret-value"},
    )
    assert "super-secret-value" not in str(packet.payload)
    with pytest.raises(PolicyError, match="blocked"):
        compile_packet(
            manifest,
            task="review",
            constraints={"credential": "API_KEY=super-secret-value"},
            dlp_policy=DlpPolicy(action="block"),
        )


def test_packet_mode_includes_only_selected_redacted_content(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "selected.txt").write_text("email=user@example.com", encoding="utf-8")
    (source / "other.txt").write_text("not included", encoding="utf-8")
    manifest = build_snapshot(source, tmp_path / "snapshots")
    packet = compile_packet(
        manifest,
        task="review",
        artifact_paths=("selected.txt",),
        include_artifact_contents=True,
    )
    contents = packet.payload["artifact_contents"]
    assert isinstance(contents, dict)
    assert "selected.txt" in contents
    assert "other.txt" not in contents
    assert "user@example.com" not in str(contents)
    dlp = packet.payload["dlp"]
    assert isinstance(dlp, dict)
    match_counts = dlp["match_counts"]
    assert isinstance(match_counts, dict)
    assert match_counts["email"] == 1


def test_snapshot_secret_scan_ignores_email_but_counts_credentials(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "contact.txt").write_text("owner=user@example.com", encoding="utf-8")
    (source / "secrets.env").write_text(
        "API_KEY=credential-value-12345", encoding="utf-8"
    )
    report = scan_snapshot_for_secrets(build_snapshot(source, tmp_path / "snapshots"))
    assert report.blocked
    assert report.matched_files == 1
    assert report.rule_counts["credential-assignment"] == 1
    assert "email" not in report.rule_counts


def test_grounding_resolves_only_trusted_ids_and_never_executes_reviewer_text(
    tmp_path: Path,
) -> None:
    command = {
        "argv": ["pytest", "-q"],
        "timeout_s": 5,
        "kind": "dynamic",
        "expected_failure": "AssertionError",
    }
    resolved = resolve_verification(_config(command), "python.pytest")
    runner = FakeRunner(RunnerOutput(exit_code=1, stderr="AssertionError: defect"))
    result = run_grounding(
        resolved,
        snapshot_root=tmp_path,
        runner=runner,
        max_output_chars=100,
    )
    assert result.verdict == "confirmed"
    assert runner.received_argv == ("pytest", "-q")
    with pytest.raises(GroundingError, match="unknown"):
        resolve_verification(_config(command), "python.pytest; whoami")


def test_grounding_bounds_output_and_classifies_timeout_and_environment(tmp_path: Path) -> None:
    command = {
        "argv": ["ruff", "check", "."],
        "timeout_s": 5,
        "kind": "static",
        "expected_failure": "E501",
    }
    resolved = resolve_verification(_config(command), "python.pytest")
    bomb = run_grounding(
        resolved,
        snapshot_root=tmp_path,
        runner=FakeRunner(RunnerOutput(exit_code=1, stderr="E501 " + "x" * 5_000)),
        max_output_chars=80,
    )
    assert bomb.verdict == "confirmed"
    assert bomb.output_truncated and len(bomb.output) <= 80
    timeout = run_grounding(
        resolved,
        snapshot_root=tmp_path,
        runner=FakeRunner(RunnerOutput(exit_code=None, timed_out=True)),
        max_output_chars=80,
    )
    assert timeout.verdict == "timeout"
    environment = run_grounding(
        resolved,
        snapshot_root=tmp_path,
        runner=FakeRunner(RunnerOutput(exit_code=1, stderr="No module named tooling")),
        max_output_chars=80,
    )
    assert environment.verdict == "environment_error"
    with pytest.raises(PolicyError, match="network-denied"):
        run_grounding(
            resolved,
            snapshot_root=tmp_path,
            runner=UnsafeRunner(RunnerOutput(exit_code=0)),
            max_output_chars=80,
        )


def test_evidence_ledger_detects_chain_tampering(tmp_path: Path) -> None:
    ledger = EvidenceLedger(tmp_path / "evidence.jsonl")
    first = ledger.append("grounding", {"verdict": "confirmed"})
    second = ledger.append("receipt", {"packet_hash": "a" * 64})
    assert ledger.verify() == (first, second)
    contents = ledger.path.read_text(encoding="utf-8")
    ledger.path.write_text(contents.replace("confirmed", "forged"), encoding="utf-8")
    with pytest.raises(ReceiptError, match="chain"):
        ledger.verify()
