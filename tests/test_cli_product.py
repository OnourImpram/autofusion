from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from autofusion import cli
from autofusion.grounding import RunnerOutput
from autofusion.proof import hash_proof_tree
from autofusion.util import JsonObject


@pytest.fixture(autouse=True)
def _isolate_global_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTOFUSION_GLOBAL_CONFIG", str(tmp_path / "absent-global.json"))


def _output(capsys: pytest.CaptureFixture[str]) -> JsonObject:
    raw: object = json.loads(capsys.readouterr().out)
    assert isinstance(raw, dict)
    return cast(JsonObject, raw)


def _revision(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir()
    (path / "value.py").write_text(f"VALUE = {name!r}\n", encoding="utf-8")
    return path


@dataclass
class _ProofRunner:
    outcomes: dict[str, RunnerOutput]
    network_denied: bool = True
    strong_isolation: bool = True
    runner_attestation_hash: str = "a" * 64

    def run(self, argv: Sequence[str], cwd: Path, timeout_s: float) -> RunnerOutput:
        del argv, timeout_s
        return self.outcomes[cwd.name]


class _StatusEngine:
    def status(self, run_id: str) -> JsonObject:
        return {"run_id": run_id, "resumable": True, "state": "analyzed"}


def test_cli_lists_packs_hashes_trees_and_reports_run_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["packs"]) == 0
    assert "security" in cast(dict[str, object], _output(capsys)["packs"])

    tree = _revision(tmp_path, "tree")
    assert cli.main(["proof-hash", "--root", str(tree)]) == 0
    assert _output(capsys)["tree_hash"] == hash_proof_tree(tree)

    monkeypatch.setattr(cli, "_engine", lambda _args: _StatusEngine())
    assert cli.main(["run-status", "run-01"]) == 0
    assert _output(capsys) == {
        "resumable": True,
        "run_id": "run-01",
        "state": "analyzed",
    }


def test_cli_prove_executes_supplied_blind_mutation_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(
        "AUTOFUSION_PROOF_ATTESTATION_KEY",
        "controlled-proof-attestation-key-material",
    )
    revisions = {
        name: _revision(tmp_path, name) for name in ("base", "head", "mutant")
    }
    overlay = tmp_path / "overlay" / "tests" / "autofusion_proof"
    overlay.mkdir(parents=True)
    (overlay / "test_regression.py").write_text(
        "def test_regression():\n    assert True\n", encoding="utf-8"
    )
    intent = {
        "schema_version": "0.1",
        "proof_id": "proof-cli-01",
        "fusion_run_id": "run-cli-01",
        "finding_id": "f01",
        "relation": "regression",
        "verification_id": "python.pytest",
        "test_author": "gpt-sol",
        "patch_author": "self",
        "candidate_fix_visible": False,
        "revision_hashes": {
            name: hash_proof_tree(path) for name, path in revisions.items()
        },
        "mutation_required": True,
    }
    intent_path = tmp_path / "intent.json"
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    output_path = tmp_path / "capsule.json"
    runner = _ProofRunner(
        {
            "base": RunnerOutput(exit_code=0),
            "head": RunnerOutput(exit_code=1),
            "mutant": RunnerOutput(exit_code=1),
        }
    )
    monkeypatch.setattr(cli, "detect_proof_runner", lambda _settings: runner)

    argv = [
        "prove",
        "--repo",
        str(tmp_path),
        "--intent",
        str(intent_path),
        "--overlay",
        str(tmp_path / "overlay"),
        "--output",
        str(output_path),
        "--work-root",
        str(tmp_path / "work"),
    ]
    for name, path in revisions.items():
        argv.extend(("--revision", f"{name}={path}"))

    assert cli.main(argv) == 0
    payload = _output(capsys)
    assert payload["verdict"] == "confirmed"
    assert cast(dict[str, object], payload["attestation"])["key_id"] == "local-proof-v1"
    assert output_path.is_file()
    assert json.loads(output_path.read_text(encoding="utf-8")) == payload


def test_cli_prove_fails_closed_without_runner_and_rejects_revision_syntax(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(
        "AUTOFUSION_PROOF_ATTESTATION_KEY",
        "controlled-proof-attestation-key-material",
    )
    intent_path = tmp_path / "intent.json"
    intent_path.write_text("{}", encoding="utf-8")
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    monkeypatch.setattr(cli, "detect_proof_runner", lambda _settings: None)

    assert (
        cli.main(
            [
                "prove",
                "--repo",
                str(tmp_path),
                "--intent",
                str(intent_path),
                "--overlay",
                str(overlay),
                "--revision",
                f"base={tmp_path}",
                "--output",
                str(tmp_path / "capsule.json"),
            ]
        )
        == 2
    )
    assert "no attested disposable Docker proof runner" in caplog.text
    with pytest.raises(ValueError, match="name=path"):
        cli._revision_paths(["invalid"])
    with pytest.raises(ValueError, match="duplicate"):
        cli._revision_paths([f"base={tmp_path}", f"base={tmp_path}"])


def test_cli_prove_requires_signing_key_before_runner_detection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("AUTOFUSION_PROOF_ATTESTATION_KEY", raising=False)
    runner_detection_called = False

    def detect_runner(_settings: object) -> _ProofRunner | None:
        nonlocal runner_detection_called
        runner_detection_called = True
        return None

    monkeypatch.setattr(cli, "detect_proof_runner", detect_runner)
    status = cli.main(
        [
            "prove",
            "--repo",
            str(tmp_path),
            "--intent",
            str(tmp_path / "missing-intent.json"),
            "--overlay",
            str(tmp_path / "missing-overlay"),
            "--revision",
            f"base={tmp_path}",
            "--output",
            str(tmp_path / "capsule.json"),
        ]
    )

    assert status == 2
    assert runner_detection_called is False
    assert "proof attestation key variable is not set" in caplog.text


def test_cli_precedent_round_trip_stays_post_blind_and_metadata_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    claim_path = tmp_path / "claim.txt"
    claim_path.write_text("Sensitive claim that must be hashed", encoding="utf-8")
    assert (
        cli.main(
            [
                "precedent-fingerprint",
                "--category",
                "correctness",
                "--claim-file",
                str(claim_path),
                "--path-hash",
                "a" * 64,
            ]
        )
        == 0
    )
    fingerprint = str(_output(capsys)["finding_fingerprint"])
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(
        json.dumps({"receipt_hash": "b" * 64, "policy_hash": "c" * 64}),
        encoding="utf-8",
    )
    common = ["--repo", str(tmp_path), "--repository-id", "owner/repository"]
    assert (
        cli.main(
            [
                "precedent-record",
                *common,
                "--fingerprint",
                fingerprint,
                "--category",
                "correctness",
                "--disposition",
                "accepted",
                "--receipt",
                str(receipt_path),
                "--ttl-days",
                "30",
            ]
        )
        == 0
    )
    record_id = str(_output(capsys)["record_id"])
    ledger_text = (tmp_path / ".fusion" / "precedents" / "evidence.jsonl").read_text(
        encoding="utf-8"
    )
    assert "Sensitive claim" not in ledger_text

    assert (
        cli.main(
            [
                "precedent-outcome",
                *common,
                record_id,
                "--outcome",
                "confirmed",
                "--evidence-hash",
                "d" * 64,
            ]
        )
        == 0
    )
    assert _output(capsys)["outcome"] == "confirmed"

    assert (
        cli.main(
            [
                "precedent-query",
                *common,
                "--fingerprint",
                fingerprint,
                "--phase",
                "post-blind-review",
            ]
        )
        == 0
    )
    result = _output(capsys)
    assert result["count"] == 1


def test_cli_github_report_writes_bounded_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    analysis_path = tmp_path / "analysis.json"
    receipt_path = tmp_path / "receipt.json"
    output_path = tmp_path / "check.json"
    analysis_path.write_text(json.dumps({"findings": []}), encoding="utf-8")
    receipt_path.write_text(
        json.dumps(
            {
                "verdict": "ship",
                "fused": True,
                "topology": "review",
                "panel": "default",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "validate_linked_paths", lambda *_args: None)
    assert (
        cli.main(
            [
                "github-report",
                "--repo",
                str(tmp_path),
                "--analysis",
                str(analysis_path),
                "--receipt",
                str(receipt_path),
                "--head-sha",
                "e" * 40,
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    payload = _output(capsys)
    assert payload["conclusion"] == "success"
    assert output_path.is_file()
