from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from autofusion.architect_editor import prepare_architect_edit, validate_architect_diff
from autofusion.drift import capture_drift_snapshot, compare_drift_snapshots
from autofusion.errors import PolicyError, ReplayError
from autofusion.github_annotations import format_github_annotation
from autofusion.policy_signing import sign_policy_bundle, verify_policy_bundle
from autofusion.replay import (
    ReplayBinding,
    bind_replay_inputs,
    create_replay_record,
    replay_record,
    write_replay_record,
)
from autofusion.telemetry import build_telemetry_event, emit_telemetry
from autofusion.util import sha256_text


def _binding() -> ReplayBinding:
    return bind_replay_inputs(
        packet_hash=sha256_text("packet"),
        policy_bundle={"deny": ["network"]},
        model_identifier="reviewer-v1",
        prompt="review this packet",
        response_schema={"type": "object"},
    )


def test_replay_rejects_tampering_and_policy_mismatch(tmp_path: Path) -> None:
    binding = _binding()
    record = create_replay_record(binding, output_text="safe")
    path = tmp_path / "record.json"
    write_replay_record(path, record)
    assert replay_record(path, binding).output_text == "safe"

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["output_text"] = "tampered"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ReplayError, match="content hash"):
        replay_record(path, binding)

    write_replay_record(path, record)
    mismatched = bind_replay_inputs(
        packet_hash=sha256_text("packet"),
        policy_bundle={"deny": ["network", "shell"]},
        model_identifier="reviewer-v1",
        prompt="review this packet",
        response_schema={"type": "object"},
    )
    with pytest.raises(ReplayError, match="binding"):
        replay_record(path, mismatched)


def test_policy_signature_rejects_wrong_and_revoked_keys() -> None:
    import autofusion.policy_signing as signing

    bundle = {"guardrails": {"fail_closed": True}}
    signature = sign_policy_bundle(bundle, key_id="2026-a", signing_key=b"s" * 32)
    assert verify_policy_bundle(bundle, signature, {"2026-a": b"s" * 32}) == signature
    assert "hmac.compare_digest" in inspect.getsource(signing.verify_policy_bundle)
    with pytest.raises(PolicyError, match="verification failed"):
        verify_policy_bundle(bundle, signature, {"2026-a": b"w" * 32})
    with pytest.raises(PolicyError, match="revoked"):
        verify_policy_bundle(
            bundle, signature, {"2026-a": b"s" * 32}, revoked_key_ids={"2026-a"}
        )
    with pytest.raises(PolicyError, match="at least 32"):
        sign_policy_bundle(bundle, key_id="2026-b", signing_key=b"short")


def test_drift_is_deterministic_and_reports_structural_change() -> None:
    before = capture_drift_snapshot("policy", {"b": 2, "a": {"enabled": True}})
    equivalent = capture_drift_snapshot("policy", {"a": {"enabled": True}, "b": 2})
    assert before.snapshot_hash == equivalent.snapshot_hash
    assert not compare_drift_snapshots(before, equivalent).changed

    after = capture_drift_snapshot("policy", {"a": {"enabled": False}, "c": 3})
    report = compare_drift_snapshots(before, after)
    assert [(change.path, change.kind) for change in report.changes] == [
        ("/a/enabled", "changed"),
        ("/b", "removed"),
        ("/c", "added"),
    ]


@pytest.mark.parametrize(
    "diff",
    [
        "diff --git a/src/ok.py b/src/../secret.py\n@@ -1 +1 @@\n-a\n+b\n",
        "diff --git a/src/ok.py b/src/ok.py\nGIT binary patch\n",
        "diff --git a/src/old.py b/src/new.py\nrename from src/old.py\nrename to src/new.py\n",
        "diff --git a/src/ok.py b/src/ok.py\n--- a/src/ok.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-a\n",
        "diff --git a/src/unapproved.py b/src/unapproved.py\n@@ -1 +1 @@\n-a\n+b\n",
    ],
)
def test_architect_editor_rejects_unsafe_diffs(diff: str) -> None:
    with pytest.raises(PolicyError):
        validate_architect_diff(diff, approved_paths=("src/ok.py",))


def test_architect_editor_validates_but_never_applies() -> None:
    diff = (
        "diff --git a/src/ok.py b/src/ok.py\n"
        "--- a/src/ok.py\n"
        "+++ b/src/ok.py\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
    )
    validated = prepare_architect_edit(diff, approved_paths=("src/ok.py",))
    assert validated.paths == ("src/ok.py",)
    assert not validated.applied
    with pytest.raises(PolicyError, match="separate explicit application"):
        prepare_architect_edit(diff, approved_paths=("src/ok.py",), apply=True)


def test_annotation_escapes_actions_command_injection() -> None:
    rendered = format_github_annotation(
        "error",
        "failure\n::warning::injected",
        title="bad,title:field",
        path="src/example.py",
        start_line=3,
    )
    assert "\n" not in rendered
    assert "%0A::warning::injected" in rendered
    assert "title=bad%2Ctitle%3Afield" in rendered


def test_telemetry_is_metadata_only_and_opt_in() -> None:
    received: list[object] = []
    disabled = build_telemetry_event("run.completed", {"status": "ok"})
    emit_telemetry(disabled, received.append)
    assert received == []
    with pytest.raises(PolicyError, match="approved metadata"):
        build_telemetry_event("run.completed", {"prompt": "private"}, enabled=True)
    enabled = build_telemetry_event("run.completed", {"status": "ok"}, enabled=True)
    emit_telemetry(enabled, received.append)
    assert len(received) == 1
