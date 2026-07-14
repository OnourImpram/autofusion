from __future__ import annotations

from pathlib import Path

import pytest

from autofusion.config import FusionConfig, load_config, merge_layers, validate_config
from autofusion.errors import ConfigurationError
from autofusion.github_report import build_github_check_report


def test_github_report_is_human_gated_and_escapes_model_content() -> None:
    analysis = {
        "findings": [
            {
                "id": "f01",
                "severity": "major",
                "claim": "<script>alert(1)</script> [unsafe](javascript:alert(1))",
                "status": "grounded",
            }
        ]
    }
    receipt = {
        "verdict": "revise",
        "fused": True,
        "topology": "dual-review",
        "panel": "dual-opus",
    }
    report = build_github_check_report(analysis, receipt, head_sha="a" * 40)
    summary = str(report["output"]["summary"])
    assert "<script>" not in summary
    assert "javascript:alert" in summary
    assert report["conclusion"] == "action_required"
    assert report["autofusion_policy"] == {
        "automatic_merge": False,
        "automatic_approval": False,
        "raw_prompts_included": False,
    }
    assert {action["identifier"] for action in report["actions"]} == {
        "autofusion-prove",
        "autofusion-escalate",
        "autofusion-rerun",
    }


def test_github_report_rejects_non_git_head() -> None:
    with pytest.raises(ValueError, match="head_sha"):
        build_github_check_report({}, {}, head_sha="not-a-git-sha")


def test_proof_and_precedent_policy_cannot_be_weakened_by_local_overlay() -> None:
    base = load_config().data
    first_pattern = base["proof"]["approved_overlay_patterns"][0]
    merged = merge_layers(
        base,
        {
            "proof": {
                "require_mutation": False,
                "require_independent_test_author": False,
                "candidate_fix_visible": True,
                "require_signed_capsules": False,
                "attestation_key_env": "ATTACKER_KEY",
                "attestation_key_id": "attacker",
                "network": "allow",
                "isolation": "host",
                "max_overlay_files": 999,
                "approved_overlay_patterns": [first_pattern, "**"],
            },
            "precedent": {
                "automatic_authority": True,
                "automatic_first_pass_injection": True,
                "retrieval_phase": "blind-first-pass",
                "privacy": "raw",
            },
        },
    )
    proof = merged["proof"]
    precedent = merged["precedent"]
    assert proof["require_mutation"] is True
    assert proof["require_independent_test_author"] is True
    assert proof["candidate_fix_visible"] is False
    assert proof["require_signed_capsules"] is True
    assert proof["attestation_key_env"] == "AUTOFUSION_PROOF_ATTESTATION_KEY"
    assert proof["attestation_key_id"] == "local-proof-v1"
    assert proof["network"] == "deny"
    assert proof["isolation"] == "disposable-docker"
    assert proof["max_overlay_files"] == base["proof"]["max_overlay_files"]
    assert proof["approved_overlay_patterns"] == [first_pattern]
    assert precedent["automatic_authority"] is False
    assert precedent["automatic_first_pass_injection"] is False
    assert precedent["retrieval_phase"] == "post-blind-review"
    assert precedent["privacy"] == "metadata-only"

    approved_image = f"registry.example/proof@sha256:{'a' * 64}"
    attacker_image = f"registry.example/attacker@sha256:{'b' * 64}"
    pinned = merge_layers(base, {"proof": {"docker_image": approved_image}})
    overlaid = merge_layers(pinned, {"proof": {"docker_image": attacker_image}})
    assert overlaid["proof"]["docker_image"] == approved_image


def test_builtin_fusion_packs_are_valid_and_invalid_pack_is_rejected(
    tmp_path: Path,
) -> None:
    del tmp_path
    config = load_config()
    assert set(config.section("packs")) == {
        "migration",
        "security",
        "release",
        "incident",
        "api-contract",
        "dependency",
        "research-evidence",
    }
    with pytest.raises(ConfigurationError, match="invalid artifact kinds"):
        load_config(
            overrides={"packs": {"migration": {"artifact_kinds": ["unknown-kind"]}}}
        )


def test_precedent_directory_must_remain_repository_relative() -> None:
    config = load_config()
    raw = merge_layers(config.data, {"precedent": {"directory": "../escape.jsonl"}})
    with pytest.raises(ConfigurationError, match="inside the repository"):
        validate_config(FusionConfig(raw, ()))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("attestation_key_env", "1INVALID-ENV", "environment variable is invalid"),
        ("attestation_key_id", "invalid key id", "key ID is invalid"),
        ("docker_image", "proof:latest", "must be pinned"),
    ),
)
def test_proof_attestation_identifiers_are_strict(
    field: str, value: str, message: str
) -> None:
    config = load_config()
    raw = config.data.copy()
    proof = dict(config.section("proof"))
    proof[field] = value
    raw["proof"] = proof
    with pytest.raises(ConfigurationError, match=message):
        validate_config(FusionConfig(raw, ()))
