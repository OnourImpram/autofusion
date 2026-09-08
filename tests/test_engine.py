from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from autofusion.config import load_config
from autofusion.engine import FusionEngine, FusionRunRequest, PendingRun
from autofusion.errors import NonCallableProviderError, PolicyError, ProofError, ReceiptError
from autofusion.grounding import RunnerOutput, VerificationCommand
from autofusion.models import CallStatus, ModelProfile, ProviderRequest, ProviderResult
from autofusion.proof import (
    ProofPolicy,
    ProofRelation,
    VerificationIntent,
    hash_proof_tree,
    run_proof,
    sign_proof_capsule,
)
from autofusion.providers.base import SelfProvider
from autofusion.providers.fake import DeterministicFakeProvider
from autofusion.reconcile import FindingDisposition
from autofusion.registry import ProviderRegistry


def _review_output(findings: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "summary": "independent review complete",
        "coverage": ["artifact"],
        "blind_spots": [],
        "recommendation": "revise" if findings else "ship",
        "findings": findings or [],
    }


def _registry(output: dict[str, object]) -> ProviderRegistry:
    config = load_config()
    return ProviderRegistry(
        {
            "self": SelfProvider(config.model("self")),
            "gpt-sol": DeterministicFakeProvider(config.model("gpt-sol"), output),
            "gpt-sol-ultra": DeterministicFakeProvider(
                config.model("gpt-sol-ultra"), output
            ),
            "claude-opus": DeterministicFakeProvider(config.model("claude-opus"), output),
            "claude-fable": DeterministicFakeProvider(config.model("claude-fable"), output),
        }
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    return repo


def test_release_pack_accepts_adequate_explicit_panel(tmp_path: Path) -> None:
    engine = FusionEngine(load_config(), _registry(_review_output()), work_root=tmp_path / "work")
    pending = engine.run(FusionRunRequest(
        task="Review release", repo_root=_repo(tmp_path), artifact_kind="release",
        artifact_paths=("app.py",), panel="dual-opus", pack="release",
        self_model="claude-opus-4-8", run_grounding=False,
    ))
    assert isinstance(pending, PendingRun)


def test_self_driven_run_stops_for_reconciliation_then_writes_linked_receipt(
    tmp_path: Path,
) -> None:
    config = load_config()
    engine = FusionEngine(config, _registry(_review_output()), work_root=tmp_path / "work")
    pending = engine.run(
        FusionRunRequest(
            task="Review the change",
            repo_root=_repo(tmp_path),
            artifact_kind="plan",
            artifact_paths=("app.py",),
            preset="fast",
            self_model="claude-opus-4-8",
            run_grounding=False,
        )
    )
    assert isinstance(pending, PendingRun)
    assert pending.requires_reconciliation
    artifacts = engine.finalize(pending.run_id, dispositions=())
    assert artifacts.receipt["fused"] is True
    assert artifacts.receipt["verdict"] == "ship"
    assert artifacts.receipt["self_model"] == "claude-opus-4-8"
    assert artifacts.receipt["analysis_hash"]
    assert artifacts.receipt["receipt_hash"]
    assert artifacts.receipt_path.is_file()


@dataclass(frozen=True)
class FailedProvider:
    profile: ModelProfile

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        return ProviderResult(
            call_id=request.call_id,
            handle=self.profile.handle,
            requested_model=self.profile.model,
            effective_model=None,
            vendor=self.profile.vendor,
            family=self.profile.family,
            mode=self.profile.effort,
            compound=self.profile.compound,
            worker_visibility=self.profile.worker_visibility,
            status=CallStatus.FAILED,
            duration_ms=1,
            output_text="",
            structured_output=None,
            output_hash=None,
            error="provider unavailable",
        )


def test_required_provider_failure_writes_honest_degraded_receipt(tmp_path: Path) -> None:
    config = load_config()
    registry = _registry(_review_output())
    providers = dict(registry.providers)
    providers["gpt-sol"] = FailedProvider(config.model("gpt-sol"))
    engine = FusionEngine(
        config,
        ProviderRegistry(providers),
        work_root=tmp_path / "work",
    )
    artifacts = engine.run(
        FusionRunRequest(
            task="Review failure handling",
            repo_root=_repo(tmp_path),
            artifact_kind="plan",
            artifact_paths=("app.py",),
            preset="fast",
            self_model="claude-opus-4-8",
            run_grounding=False,
        )
    )
    assert not isinstance(artifacts, PendingRun)
    assert artifacts.receipt["fused"] is False
    assert artifacts.receipt["verdict"] == "degraded"
    assert artifacts.receipt["degradation_reasons"]


class FakeGroundingRunner:
    network_denied = True

    def run(self, argv: Sequence[str], cwd: Path, timeout_s: int) -> RunnerOutput:
        del argv, cwd, timeout_s
        return RunnerOutput(exit_code=1, stderr="AssertionError: seeded regression")


def test_execution_confirmed_finding_cannot_be_rejected(tmp_path: Path) -> None:
    finding = {
        "severity": "major",
        "category": "correctness",
        "claim": "The seeded regression is reachable",
        "evidence": {"summary": "app.py contains the defect", "strength": "artifact-cited"},
        "suggested_fix": "Correct the branch and preserve the regression test",
        "checkable": True,
        "verification_id": "python.pytest",
        "stance_key": "seeded-regression",
        "stance": "supports",
    }
    config = load_config(
        overrides={
            "verification": {
                "profiles": {
                    "python": {
                        "commands": {
                            "pytest": {
                                "argv": ["pytest", "-q"],
                                "timeout_s": 30,
                                "kind": "dynamic",
                                "expected_failure": "AssertionError: seeded regression",
                            }
                        }
                    }
                }
            }
        }
    )
    registry = _registry(_review_output([finding]))
    engine = FusionEngine(
        config,
        registry,
        work_root=tmp_path / "work",
        grounding_runner=FakeGroundingRunner(),
    )
    pending = engine.run(
        FusionRunRequest(
            task="Review seeded defect",
            repo_root=_repo(tmp_path),
            artifact_kind="diff",
            artifact_paths=("app.py",),
            preset="fast",
            self_model="claude-opus-4-8",
        )
    )
    assert isinstance(pending, PendingRun)
    analysis = json.loads(pending.analysis_path.read_text(encoding="utf-8"))
    assert analysis["grounding_results"][0]["verdict"] == "confirmed"
    finding_id = analysis["findings"][0]["id"]
    with pytest.raises(PolicyError, match="cannot be rejected"):
        engine.finalize(
            pending.run_id,
            dispositions=(FindingDisposition(finding_id, "rejected", "model was noisy"),),
        )
    artifacts = engine.finalize(
        pending.run_id,
        dispositions=(
            FindingDisposition(finding_id, "accepted", "trusted execution confirmed it"),
        ),
    )
    assert artifacts.receipt["findings"]["confirmed_by_exec"] == 1
    assert artifacts.receipt["verdict"] == "revise"


def test_engine_never_invokes_self_provider(tmp_path: Path) -> None:
    config = load_config()

    @dataclass(frozen=True)
    class ExplodingSelf:
        profile: ModelProfile

        def invoke(self, request: ProviderRequest) -> ProviderResult:
            del request
            raise NonCallableProviderError("self was invoked")

    registry = _registry(_review_output())
    providers = dict(registry.providers)
    providers["self"] = ExplodingSelf(config.model("self"))
    engine = FusionEngine(config, ProviderRegistry(providers), work_root=tmp_path / "work")
    pending = engine.run(
        FusionRunRequest(
            task="Respect the self boundary",
            repo_root=_repo(tmp_path),
            artifact_kind="plan",
            artifact_paths=("app.py",),
            preset="fast",
            self_model="claude-opus-4-8",
            run_grounding=False,
        )
    )
    assert isinstance(pending, PendingRun)


def test_repo_secret_blocks_cli_dispatch_and_writes_degraded_receipt(tmp_path: Path) -> None:
    config = load_config()

    @dataclass(frozen=True)
    class ExplodingProvider:
        profile: ModelProfile

        def invoke(self, request: ProviderRequest) -> ProviderResult:
            del request
            raise AssertionError("DLP-blocked provider must never be invoked")

    registry = _registry(_review_output())
    providers = dict(registry.providers)
    providers["gpt-sol"] = ExplodingProvider(config.model("gpt-sol"))
    repo = _repo(tmp_path)
    (repo / "secrets.env").write_text(
        "API_KEY=credential-value-12345", encoding="utf-8"
    )
    artifacts = FusionEngine(
        config, ProviderRegistry(providers), work_root=tmp_path / "work"
    ).run(
        FusionRunRequest(
            task="Review without leaking credentials",
            repo_root=repo,
            artifact_kind="plan",
            artifact_paths=("app.py",),
            preset="fast",
            self_model="claude-opus-4-8",
            run_grounding=False,
        )
    )
    assert not isinstance(artifacts, PendingRun)
    assert artifacts.receipt["fused"] is False
    assert artifacts.receipt["verdict"] == "degraded"
    assert artifacts.receipt["calls"][0]["status"] == "policy-blocked"
    evidence = (tmp_path / "work" / artifacts.run_id / "evidence.jsonl").read_text(
        encoding="utf-8"
    )
    assert "credential-value-12345" not in evidence
    assert "dlp-block" in evidence


def test_wallclock_overrun_degrades_without_claiming_fusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(overrides={"guardrails": {"max_wallclock_s": 1}})
    monkeypatch.setattr(
        FusionEngine,
        "_wallclock_seconds",
        staticmethod(lambda started_at, finished_at: 2),
    )
    artifacts = FusionEngine(
        config, _registry(_review_output()), work_root=tmp_path / "work"
    ).run(
        FusionRunRequest(
            task="Review under an exhausted budget",
            repo_root=_repo(tmp_path),
            artifact_kind="plan",
            artifact_paths=("app.py",),
            preset="fast",
            self_model="claude-opus-4-8",
            run_grounding=False,
        )
    )
    assert not isinstance(artifacts, PendingRun)
    assert artifacts.receipt["fused"] is False
    assert artifacts.receipt["verdict"] == "degraded"
    assert artifacts.receipt["budgets"]["max_wallclock_s"] == 1
    assert artifacts.receipt["budgets"]["wallclock_s"] == 2


@dataclass(frozen=True)
class PanelProvider:
    profile: ModelProfile
    rank: int | None = None

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        required = request.response_schema.get("required")
        if isinstance(required, list) and "winner" in required:
            payload = json.loads(request.prompt.splitlines()[-1])
            left_rank = int(str(payload["left"]["output"]["summary"]).removeprefix("rank-"))
            right_rank = int(str(payload["right"]["output"]["summary"]).removeprefix("rank-"))
            output: dict[str, object] = {
                "winner": "left" if left_rank < right_rank else "right",
                "rationale": "lower deterministic rank wins",
                "confidence": "high",
            }
        else:
            assert self.rank is not None
            output = {
                "summary": f"rank-{self.rank}",
                "proposal": f"proposal {self.rank}",
                "assumptions": [],
                "risks": [],
                "verification": ["inspect selected proposal"],
            }
        return DeterministicFakeProvider(self.profile, output).invoke(request)


def test_external_panel_rank_binds_repeated_judge_calls_and_selects_stably(
    tmp_path: Path,
) -> None:
    config = load_config()
    registry = ProviderRegistry(
        {
            "self": SelfProvider(config.model("self")),
            "gpt-sol": PanelProvider(config.model("gpt-sol"), rank=1),
            "claude-opus": PanelProvider(config.model("claude-opus"), rank=2),
            "gpt-sol-ultra": PanelProvider(config.model("gpt-sol-ultra")),
        }
    )
    artifacts = FusionEngine(config, registry, work_root=tmp_path / "work").run(
        FusionRunRequest(
            task="Produce and rank independent implementation proposals",
            repo_root=_repo(tmp_path),
            artifact_kind="plan",
            artifact_paths=("app.py",),
            preset="parallel",
            external_only=True,
            run_grounding=False,
        )
    )
    assert not isinstance(artifacts, PendingRun)
    assert artifacts.receipt["fused"] is True
    assert artifacts.receipt["topology"] == "panel-rank"
    assert artifacts.receipt["budgets"]["used_calls"] == 4
    assert artifacts.analysis["selection"]["winner_id"] == "p01"
    assert artifacts.analysis["selection"]["order_sensitive"] is False
    judge_calls = [
        call for call in artifacts.receipt["calls"] if call["handle"] == "gpt-sol-ultra"
    ]
    assert len(judge_calls) == 2
    assert len({call["call_id"] for call in judge_calls}) == 2


def test_release_pack_escalates_fast_request_and_records_pack(tmp_path: Path) -> None:
    config = load_config()
    engine = FusionEngine(config, _registry(_review_output()), work_root=tmp_path / "work")
    pending = engine.run(
        FusionRunRequest(
            task="Review release readiness",
            repo_root=_repo(tmp_path),
            artifact_kind="release",
            artifact_paths=("app.py",),
            preset="fast",
            pack="release",
            self_model="claude-opus-4-8",
            run_grounding=False,
        )
    )
    assert isinstance(pending, PendingRun)
    raw = json.loads(pending.pending_path.read_text(encoding="utf-8"))
    assert raw["pack"] == "release"
    assert raw["route"]["selected_preset"] == "balanced"
    assert raw["route"]["topology"] == "dual-review"
    assert len(raw["calls"]) == 2


def test_migration_pack_dispatches_every_adversarial_reviewer(tmp_path: Path) -> None:
    config = load_config()
    engine = FusionEngine(config, _registry(_review_output()), work_root=tmp_path / "work")
    pending = engine.run(
        FusionRunRequest(
            task="Review migration and rollback safety",
            repo_root=_repo(tmp_path),
            artifact_kind="migration",
            artifact_paths=("app.py",),
            preset="fast",
            pack="migration",
            self_model="claude-opus-4-8",
            run_grounding=False,
        )
    )
    assert isinstance(pending, PendingRun)
    raw = json.loads(pending.pending_path.read_text(encoding="utf-8"))
    assert raw["route"]["selected_preset"] == "high"
    assert raw["route"]["topology"] == "adversarial-review"
    assert {call["handle"] for call in raw["calls"]} == {
        "gpt-sol-ultra",
        "claude-opus",
    }


def test_pack_rejects_incompatible_artifact(tmp_path: Path) -> None:
    config = load_config()
    engine = FusionEngine(config, _registry(_review_output()), work_root=tmp_path / "work")
    with pytest.raises(PolicyError, match="does not accept"):
        engine.run(
            FusionRunRequest(
                task="Use the wrong pack",
                repo_root=_repo(tmp_path),
                artifact_kind="answer",
                pack="migration",
                self_model="claude-opus-4-8",
                run_grounding=False,
            )
        )


@dataclass
class CapsuleRunner:
    network_denied: bool = True
    strong_isolation: bool = True
    runner_attestation_hash: str = "c" * 64

    def run(self, argv: Sequence[str], cwd: Path, timeout_s: int) -> RunnerOutput:
        del argv, timeout_s
        return RunnerOutput(exit_code=0 if cwd.name == "base" else 1)


def test_confirmed_proof_capsule_cannot_be_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proof_key = "controlled-proof-attestation-key-material"
    monkeypatch.setenv("AUTOFUSION_PROOF_ATTESTATION_KEY", proof_key)
    finding = {
        "severity": "major",
        "category": "correctness",
        "claim": "The head introduces a regression",
        "evidence": {"summary": "app.py changes behavior", "strength": "artifact-cited"},
        "suggested_fix": "Restore the invariant",
        "checkable": True,
        "verification_id": "python.pytest",
        "stance_key": "proof-regression",
        "stance": "supports",
    }
    config = load_config()
    engine = FusionEngine(
        config,
        _registry(_review_output([finding])),
        work_root=tmp_path / "work",
    )
    pending = engine.run(
        FusionRunRequest(
            task="Review proof linked defect",
            repo_root=_repo(tmp_path),
            artifact_kind="diff",
            artifact_paths=("app.py",),
            preset="fast",
            self_model="claude-opus-4-8",
            run_grounding=False,
        )
    )
    assert isinstance(pending, PendingRun)
    analysis = json.loads(pending.analysis_path.read_text(encoding="utf-8"))
    finding_id = str(analysis["findings"][0]["id"])
    revisions: dict[str, Path] = {}
    for name in ("base", "head", "mutant"):
        revision = tmp_path / f"proof-{name}"
        revision.mkdir()
        (revision / "app.py").write_text(f"VALUE = {name!r}\n", encoding="utf-8")
        revisions[name] = revision
    overlay = tmp_path / "overlay" / "tests" / "autofusion_proof"
    overlay.mkdir(parents=True)
    (overlay / "test_regression.py").write_text(
        "def test_regression():\n    assert True\n", encoding="utf-8"
    )
    capsule = run_proof(
        VerificationIntent(
            proof_id="proof-engine-01",
            fusion_run_id=pending.run_id,
            finding_id=finding_id,
            relation=ProofRelation.REGRESSION,
            verification_id="python.pytest",
            test_author="gpt-sol",
            patch_author="self",
            candidate_fix_visible=False,
            revision_hashes={
                name: hash_proof_tree(path) for name, path in revisions.items()
            },
        ),
        revisions=revisions,
        overlay_root=tmp_path / "overlay",
        command=VerificationCommand(
            "python.pytest", ("pytest", "-q"), 30, "dynamic"
        ),
        runner=CapsuleRunner(),
        policy=ProofPolicy(
            True,
            ("tests/autofusion_proof/**",),
            4,
            4096,
            True,
            1024,
        ),
        work_root=tmp_path / "proof-work",
    )
    with pytest.raises(ReceiptError, match="explicit finding dispositions"):
        engine.finalize(pending.run_id, dispositions=None)
    with pytest.raises(ProofError, match="attestation"):
        engine.finalize(
            pending.run_id,
            dispositions=(
                FindingDisposition(finding_id, "accepted", "unsigned capsule"),
            ),
            proof_capsules=(capsule,),
        )
    wrongly_signed = sign_proof_capsule(
        capsule,
        key_id="local-proof-v1",
        signing_key=b"wrong-proof-attestation-key-material-32",
    )
    with pytest.raises(PolicyError, match="verification failed"):
        engine.finalize(
            pending.run_id,
            dispositions=(
                FindingDisposition(finding_id, "accepted", "forged signature"),
            ),
            proof_capsules=(wrongly_signed,),
        )
    capsule = sign_proof_capsule(
        capsule,
        key_id="local-proof-v1",
        signing_key=proof_key.encode("utf-8"),
    )
    with pytest.raises(PolicyError, match="cannot be rejected"):
        engine.finalize(
            pending.run_id,
            dispositions=(
                FindingDisposition(finding_id, "rejected", "reject despite proof"),
            ),
            proof_capsules=(capsule,),
        )
    artifacts = engine.finalize(
        pending.run_id,
        dispositions=(
            FindingDisposition(finding_id, "accepted", "independent proof confirmed it"),
        ),
        proof_capsules=(capsule,),
    )
    assert artifacts.receipt["findings"]["confirmed_by_proof"] == 1
    assert artifacts.receipt["verdict"] == "revise"
    assert artifacts.analysis["proof_results"][0]["capsule_hash"] == capsule.capsule_hash
    status = engine.status(pending.run_id)
    assert status["finalized"] is True
    assert status["can_resume_reconciliation"] is False
