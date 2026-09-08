from __future__ import annotations

import json
from pathlib import Path

import pytest

from autofusion import cli
from autofusion.util import JsonObject


def _prepare(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[JsonObject, list[str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    common = ["--repo", str(repo), "--work-root", str(tmp_path / "work")]
    output = tmp_path / "requests.json"
    assert cli.main([
        "session-export", *common, "--task", "Review the plan", "--kind", "plan",
        "--path", "app.py", "--preset", "fast", "--self-model", "claude-fable-5-1",
        "--delegate", "gpt-sol=sol-delege", "--no-grounding", "--output", str(output),
    ]) == 0
    bundle: JsonObject = json.loads(output.read_text(encoding="utf-8"))
    assert json.loads(capsys.readouterr().out)["run_id"] == bundle["run_id"]
    return bundle, common


def test_session_cli_exports_imports_and_completes_without_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AUTOFUSION_GLOBAL_CONFIG", str(tmp_path / "absent.json"))
    bundle, common = _prepare(tmp_path, capsys)
    request = bundle["requests"][0]
    result = {key: request[key] for key in (
        "schema_version", "run_id", "call_id", "packet_hash", "artifact_manifest_hash",
        "reviewer_schema_id", "reviewer_schema_hash",
    )}
    result.update({
        "status": "completed",
        "actual_identity": {key: request["requested_identity"][key]
                            for key in ("model", "vendor", "family", "delegate")},
        "output": {"summary": "Reviewed", "coverage": ["artifact"], "blind_spots": [],
                   "recommendation": "ship", "findings": []},
        "error": None,
    })
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    assert cli.main([
        "session-import", bundle["run_id"], *common, "--result", str(result_path),
    ]) == 0
    capsys.readouterr()
    assert cli.main(["session-complete", bundle["run_id"], *common]) == 0
    pending = json.loads(capsys.readouterr().out)
    assert pending["requires_reconciliation"] is True
    assert Path(pending["analysis_path"]).is_file()
    assert cli.main([
        "session-import", bundle["run_id"], *common, "--result", str(result_path),
    ]) == 2


def test_session_cli_rejects_ambiguous_delegate_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTOFUSION_GLOBAL_CONFIG", str(tmp_path / "absent.json"))
    base = ["session-export", "--repo", str(tmp_path), "--task", "Review", "--kind", "plan"]
    assert cli.main([*base, "--delegate", "gpt-sol"]) == 2
    assert cli.main([
        *base, "--delegate", "gpt-sol=sol-delege", "--delegate", "gpt-sol=astra-delege",
    ]) == 2
