"""Trustworthy fusion runtime with an explicit non-callable self boundary."""

from __future__ import annotations

import asyncio
import math
import os
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import cast

from autofusion.analysis import AnalysisInput, build_analysis
from autofusion.budget import BudgetLedger
from autofusion.config import FusionConfig
from autofusion.contracts import canonical_analysis_bytes, validate_analysis
from autofusion.credentials import CredentialBroker
from autofusion.dlp import DlpAction, DlpPolicy, scan_snapshot_for_secrets
from autofusion.errors import GroundingError, PolicyError, ReceiptError, SnapshotError
from autofusion.evidence import EvidenceLedger
from autofusion.grounding import (
    GroundingResult,
    GroundingRunner,
    resolve_verification,
    run_grounding,
)
from autofusion.identity import SessionIdentity
from autofusion.journal import RunJournal
from autofusion.models import (
    CallStatus,
    ModelProfile,
    Packet,
    ProviderRequest,
    ProviderResult,
    RouteDecision,
    RunArtifacts,
    RunState,
    SnapshotManifest,
)
from autofusion.packet import compile_packet
from autofusion.policy import assert_callable_participants
from autofusion.prompts import (
    load_response_schema,
    pairwise_prompt,
    proposal_prompt,
    review_prompt,
)
from autofusion.proof import (
    ProofCapsule,
    capsule_from_json,
    verify_proof_capsule_attestation,
)
from autofusion.receipt import ReceiptStore
from autofusion.reconcile import (
    FindingDisposition,
    GroundingLink,
    attach_grounding,
    attach_proof_capsules,
    automatic_external_dispositions,
    reconcile_analysis,
)
from autofusion.registry import ProviderRegistry
from autofusion.router import AdaptiveSignals, is_route_at_least, resolve_adaptive_route
from autofusion.snapshot import assert_snapshot_fresh, assert_snapshot_intact, build_snapshot
from autofusion.state import RunStateMachine
from autofusion.topologies import (
    OrchestrationResult,
    PanelRankResult,
    ProviderDispatcher,
    run_adversarial_council,
    run_adversarial_review,
    run_advisor,
    run_dual_review,
    run_panel_rank,
    run_review,
)
from autofusion.util import (
    JsonObject,
    atomic_write_bytes,
    atomic_write_json,
    isoformat_z,
    read_json_object,
    sha256_bytes,
    sha256_json,
    utc_now,
)


@dataclass(frozen=True, slots=True)
class FusionRunRequest:
    task: str
    repo_root: Path
    artifact_kind: str
    artifact_paths: tuple[str, ...] = ()
    preset: str = "adaptive"
    panel: str | None = None
    external_only: bool = False
    self_model: str | None = None
    self_identity_source: str = "runtime-attested"
    self_session_fingerprint: str | None = None
    focus_role: str | None = None
    constraints: JsonObject | None = None
    run_grounding: bool = True
    pack: str | None = None

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("fusion task must not be empty")
        if self.artifact_kind not in {
            "plan",
            "diff",
            "answer",
            "migration",
            "incident",
            "release",
            "api-contract",
            "dependency",
            "research-synthesis",
            "architecture-decision",
        }:
            raise ValueError(f"unsupported artifact kind: {self.artifact_kind}")


@dataclass(frozen=True, slots=True)
class PendingRun:
    run_id: str
    state: str
    analysis_path: Path
    pending_path: Path
    evidence_path: Path
    journal_path: Path
    result_path: Path
    requires_reconciliation: bool
    fused: bool
    degraded: bool


class _AsyncRegistryDispatcher(ProviderDispatcher):
    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry

    async def dispatch(self, request: ProviderRequest) -> ProviderResult:
        return await asyncio.to_thread(self.registry.invoke, request)


class FusionEngine:
    """Coordinate callable participants while never attempting to invoke self."""

    def __init__(
        self,
        config: FusionConfig,
        registry: ProviderRegistry,
        *,
        work_root: Path | None = None,
        grounding_runner: GroundingRunner | None = None,
        credential_broker: CredentialBroker | None = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.work_root = (work_root or Path.home() / ".fusion" / "work").resolve()
        self.grounding_runner = grounding_runner
        self.credential_broker = credential_broker or CredentialBroker(config)

    def run(self, request: FusionRunRequest) -> PendingRun | RunArtifacts:
        """Run callable review steps and stop for self reconciliation when required."""

        pending = self._prepare(request)
        if pending.degraded or not pending.requires_reconciliation:
            return self.finalize(pending.run_id, dispositions=None, automatic=True)
        return pending

    def _resolve_route(self, request: FusionRunRequest) -> tuple[RouteDecision, JsonObject | None]:
        pack = self.config.pack(request.pack) if request.pack is not None else None
        if pack is not None:
            artifact_kinds = pack.get("artifact_kinds")
            assert isinstance(artifact_kinds, list)
            if request.artifact_kind not in artifact_kinds:
                raise PolicyError(
                    f"fusion pack {request.pack} does not accept {request.artifact_kind}"
                )
        signals = AdaptiveSignals(
            artifact_paths=request.artifact_paths,
            cross_module_scope=len(request.artifact_paths) > 3,
            low_verification_strength=request.artifact_kind != "diff",
            irreversible=request.artifact_kind == "migration",
            external_only=request.external_only,
        )
        route = resolve_adaptive_route(
            self.config,
            signals=signals,
            requested_preset=request.preset,
            explicit_panel=request.panel,
        )
        if pack is not None:
            minimum = str(pack["minimum_preset"])
            if not is_route_at_least(route, minimum):
                if request.panel is not None:
                    raise PolicyError(
                        f"explicit panel cannot satisfy {request.pack} minimum preset {minimum}"
                    )
                route = resolve_adaptive_route(
                    self.config,
                    signals=signals,
                    requested_preset=minimum,
                )
            allowed_topologies = pack.get("allowed_topologies")
            assert isinstance(allowed_topologies, list)
            if route.topology not in allowed_topologies:
                raise PolicyError(
                    f"fusion pack {request.pack} does not allow topology {route.topology}"
                )
            route = replace(
                route,
                reasons=(
                    f"fusion pack {request.pack} enforced {minimum} or stronger",
                    *route.reasons,
                ),
            )
        return route, pack

    def finalize(
        self,
        run_id: str,
        *,
        dispositions: tuple[FindingDisposition, ...] | None,
        automatic: bool = False,
        proof_capsules: tuple[ProofCapsule, ...] = (),
    ) -> RunArtifacts:
        """Finalize a pending run after self or external reconciliation."""

        run_directory = self._run_directory(run_id)
        pending_path = run_directory / "pending.json"
        if not pending_path.is_file():
            raise ReceiptError(f"pending run does not exist: {run_id}")
        if (run_directory / "finalized.json").exists():
            raise ReceiptError(f"run is already finalized: {run_id}")
        journal = RunJournal(run_directory / "journal.jsonl", run_id)
        pending = read_json_object(pending_path)
        ledger = EvidenceLedger(run_directory / "evidence.jsonl")
        records = ledger.verify()
        pending_hash = sha256_bytes(pending_path.read_bytes())
        if not any(
            record.kind == "pending-state" and record.payload.get("pending_hash") == pending_hash
            for record in records
        ):
            raise ReceiptError("pending run is not bound to its evidence ledger")
        analysis_path = run_directory / "analysis.json"
        analysis = read_json_object(analysis_path)
        verified_capsules = tuple(
            capsule_from_json(capsule.as_json()) for capsule in proof_capsules
        )
        if verified_capsules:
            proof_settings = self.config.section("proof")
            key_env = str(proof_settings["attestation_key_env"])
            verification_key = os.environ.get(key_env)
            if verification_key is None:
                raise PolicyError(
                    f"proof attestation key variable is not set: {key_env}"
                )
            key_id = str(proof_settings["attestation_key_id"])
            verified_capsules = tuple(
                verify_proof_capsule_attestation(
                    capsule,
                    key_id=key_id,
                    verification_key=verification_key.encode("utf-8"),
                )
                for capsule in verified_capsules
            )
            analysis = attach_proof_capsules(analysis, verified_capsules)
            for capsule in verified_capsules:
                proof_payload: JsonObject = {
                    "proof_id": capsule.intent.proof_id,
                    "finding_id": capsule.intent.finding_id,
                    "capsule_hash": capsule.capsule_hash,
                    "verdict": capsule.verdict.value,
                    "mutation_gate_passed": capsule.mutation_gate_passed,
                }
                prior = [
                    record
                    for record in ledger.verify()
                    if record.kind == "proof-capsule"
                    and record.payload.get("proof_id") == capsule.intent.proof_id
                ]
                if prior and any(record.payload != proof_payload for record in prior):
                    raise ReceiptError("proof ID was reused with a different capsule")
                if not prior:
                    ledger.append("proof-capsule", proof_payload)
        reconcile_input_hash = sha256_json(
            {
                "analysis_hash": sha256_bytes(canonical_analysis_bytes(analysis)),
                "proof_capsule_hashes": [
                    capsule.capsule_hash for capsule in verified_capsules
                ],
            }
        )
        journal.record(
            "reconcile",
            "started",
            idempotency_key=f"reconcile:started:{reconcile_input_hash[:24]}",
            payload={
                "analysis_hash": sha256_bytes(canonical_analysis_bytes(analysis)),
                "proof_capsule_hashes": [
                    capsule.capsule_hash for capsule in verified_capsules
                ],
            },
        )
        degradation = [
            item for item in pending.get("degradation_reasons", []) if isinstance(item, str)
        ]
        snapshot = self._snapshot_from_pending(pending)
        try:
            assert_snapshot_intact(snapshot)
            assert_snapshot_fresh(snapshot)
        except SnapshotError as error:
            degradation.append(str(error))
        requires_reconciliation = bool(pending.get("requires_reconciliation"))
        if dispositions is None:
            if requires_reconciliation and not automatic and not degradation:
                raise ReceiptError("self-driven run requires explicit finding dispositions")
            dispositions = automatic_external_dispositions(analysis)
        analysis = reconcile_analysis(
            analysis,
            dispositions,
            require_all=not bool(degradation),
        )
        signoff_payload: JsonObject = {
            "run_id": run_id,
            "analysis_hash": sha256_bytes(canonical_analysis_bytes(analysis)),
            "automatic": automatic,
            "disposition_count": len(dispositions),
        }
        signoff = next(
            (
                record
                for record in ledger.verify()
                if record.kind == "signoff" and record.payload == signoff_payload
            ),
            None,
        )
        if signoff is None:
            signoff = ledger.append("signoff", signoff_payload)
        journal.record(
            "reconcile",
            "completed",
            idempotency_key="reconcile:completed",
            payload={
                "analysis_hash": signoff_payload["analysis_hash"],
                "signoff_attestation_hash": signoff.record_hash,
            },
        )
        route = self._route_from_pending(pending)
        calls = self._call_records(pending)
        context_complete = bool(analysis.get("context_complete"))
        fused = (
            not degradation
            and context_complete
            and route.topology != "advisor"
            and self._required_calls_completed(route, calls)
        )
        decision = analysis.get("decision_impact")
        effect = decision.get("effect") if isinstance(decision, dict) else "human-required"
        if degradation:
            state, verdict = RunState.DEGRADED.value, "degraded"
        elif effect == "human-required":
            state, verdict = RunState.ESCALATED.value, "blocked"
        elif effect == "blocked":
            state, verdict = RunState.SIGNED_OFF.value, "blocked"
        elif effect == "revised":
            state, verdict = RunState.SIGNED_OFF.value, "revise"
        else:
            state, verdict = RunState.SIGNED_OFF.value, "ship"
        finished_at = isoformat_z(utc_now())
        receipt = self._build_receipt(
            pending=pending,
            route=route,
            analysis=analysis,
            calls=calls,
            state=state,
            verdict=verdict,
            fused=fused,
            degradation=tuple(dict.fromkeys(degradation)),
            finished_at=finished_at,
            signoff_hash=signoff.record_hash,
        )
        repo_root = Path(str(pending["repo_root"]))
        receipt_settings = self.config.section("receipts")
        relative_receipt_root = Path(str(receipt_settings.get("directory", ".fusion/runs")))
        if relative_receipt_root.is_absolute() or ".." in relative_receipt_root.parts:
            raise PolicyError("receipt directory must remain inside the repository")
        artifacts = ReceiptStore(repo_root / relative_receipt_root, self.config).persist(
            analysis, receipt
        )
        terminal = journal.record(
            "terminal",
            "completed",
            idempotency_key="terminal:completed",
            payload={
                "state": state,
                "verdict": verdict,
                "fused": fused,
                "receipt_hash": artifacts.receipt["receipt_hash"],
            },
        )
        atomic_write_json(
            run_directory / "finalized.json",
            {
                "run_id": run_id,
                "receipt_path": str(artifacts.receipt_path),
                "receipt_hash": artifacts.receipt["receipt_hash"],
                "finished_at": finished_at,
                "journal_head_hash": terminal.attestation_hash,
            },
        )
        return artifacts

    def status(self, run_id: str) -> JsonObject:
        """Return verified durable state without resuming provider execution."""

        run_directory = self._run_directory(run_id)
        if not run_directory.is_dir():
            raise ReceiptError(f"run does not exist: {run_id}")
        journal = RunJournal(run_directory / "journal.jsonl", run_id)
        summary = journal.summary().as_json()
        pending_path = run_directory / "pending.json"
        finalized_path = run_directory / "finalized.json"
        finalized = read_json_object(finalized_path) if finalized_path.is_file() else None
        receipt: JsonObject | None = None
        if finalized is not None:
            receipt_path = Path(str(finalized.get("receipt_path", "")))
            if receipt_path.is_file():
                receipt = read_json_object(receipt_path)
        return {
            **summary,
            "pending": pending_path.is_file() and finalized is None,
            "finalized": finalized is not None,
            "state": receipt.get("state") if receipt else None,
            "verdict": receipt.get("verdict") if receipt else None,
            "fused": receipt.get("fused") if receipt else None,
            "can_resume_reconciliation": pending_path.is_file() and finalized is None,
            "interrupted_provider_dispatch_resumable": False,
        }

    def _prepare(self, request: FusionRunRequest) -> PendingRun:
        repo_root = request.repo_root.resolve()
        if not repo_root.is_dir():
            raise ValueError(f"repository root does not exist: {repo_root}")
        started_at = isoformat_z(utc_now())
        run_id = f"run-{utc_now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:10]}"
        run_directory = self._run_directory(run_id)
        run_directory.mkdir(parents=True, exist_ok=False)
        evidence_path = run_directory / "evidence.jsonl"
        ledger = EvidenceLedger(evidence_path)
        journal = RunJournal(run_directory / "journal.jsonl", run_id)
        journal.record(
            "prepare",
            "completed",
            idempotency_key="state:prepared",
            payload={
                "artifact_kind": request.artifact_kind,
                "external_only": request.external_only,
                "pack": request.pack,
            },
        )
        state = RunStateMachine()
        route, pack = self._resolve_route(request)
        assert_callable_participants(
            self.config, tuple(handle for handle in route.participants if handle != "self")
        )
        state.transition(RunState.ROUTED, reason="deterministic policy route resolved")
        journal.record(
            "route",
            "completed",
            idempotency_key="state:routed",
            payload={
                "panel": route.panel,
                "topology": route.topology,
                "participants": list(route.participants),
                "pack": request.pack,
            },
        )
        self_identity_hash: str | None = None
        self_identity: SessionIdentity | None = None
        if "self" in route.participants:
            self_identity_hash, self_identity = self._attest_self(request, ledger)
        snapshot = build_snapshot(repo_root, run_directory / "snapshots")
        state.transition(RunState.FROZEN, reason="content-addressed snapshot created")
        journal.record(
            "freeze",
            "completed",
            idempotency_key="state:frozen",
            payload={
                "snapshot_hash": snapshot.snapshot_hash,
                "manifest_hash": snapshot.manifest_hash,
            },
        )
        guardrails = self.config.section("guardrails")
        dlp_action_raw = str(guardrails.get("sensitive_data_action", "redact"))
        if dlp_action_raw not in {"flag", "redact", "block"}:
            raise PolicyError("guardrails.sensitive_data_action is invalid")
        dlp_policy = DlpPolicy(action=cast(DlpAction, dlp_action_raw))
        repo_access_handles = tuple(
            handle
            for handle in route.participants
            if handle != "self" and self.config.model(handle).context != "packet"
        )
        snapshot_dlp = scan_snapshot_for_secrets(snapshot)
        repo_access_blocked = bool(repo_access_handles and snapshot_dlp.blocked)
        if repo_access_blocked:
            ledger.append(
                "dlp-block",
                {
                    "run_id": run_id,
                    "matched_files": snapshot_dlp.matched_files,
                    "rule_counts": snapshot_dlp.rule_counts,
                    "blocked_handles": list(repo_access_handles),
                },
            )
        include_contents = any(
            self.config.model(handle).context == "packet"
            for handle in route.participants
            if handle != "self"
        )
        verification = self.config.section("verification")
        constraints = dict(request.constraints or {})
        if pack is not None:
            constraints["fusion_pack"] = {
                "name": request.pack,
                "roles": pack["roles"],
                "proof_policy": pack["proof_policy"],
            }
        packet = compile_packet(
            snapshot,
            task=request.task,
            constraints=constraints,
            artifact_paths=request.artifact_paths,
            verification_registry=verification,
            focus_role=request.focus_role or request.pack,
            dlp_policy=dlp_policy,
            include_artifact_contents=include_contents,
        )
        ledger.append(
            "route",
            {
                "run_id": run_id,
                "packet_hash": packet.packet_hash,
                "panel": route.panel,
                "topology": route.topology,
                "participants": list(route.participants),
                "reasons": list(route.reasons),
                "hard_gates": list(route.hard_gates),
            },
        )
        budget = BudgetLedger(route.budget)
        state.transition(RunState.DISPATCHED, reason="external participants dispatched")
        journal.record(
            "dispatch",
            "started",
            idempotency_key="dispatch:started",
            payload={"packet_hash": packet.packet_hash, "required": list(route.participants)},
        )
        degradation: list[str] = []
        topology_result: OrchestrationResult | PanelRankResult
        packet_dlp = packet.payload.get("dlp")
        packet_match_counts = (
            packet_dlp.get("match_counts") if isinstance(packet_dlp, dict) else None
        )
        if isinstance(packet_match_counts, dict) and packet_match_counts:
            degradation.append("sensitive packet content matched the configured DLP policy")
        if repo_access_blocked:
            reason = "repo-access dispatch blocked because the snapshot matched secret patterns"
            results = self._policy_blocked_results(route, reason)
            topology_result = OrchestrationResult(
                topology=route.topology,
                results=results,
                required_handles=tuple(
                    handle for handle in route.participants if handle != "self"
                ),
                context_complete=False,
                fused=False,
                degraded=True,
                reason=reason,
            )
            result_payload = {
                "topology": route.topology,
                "outputs": [],
                "policy_blocked": True,
            }
        else:
            results, topology_result, result_payload = self._dispatch(
                route, packet, run_id, budget
            )
        results = self._attest_calls(results, ledger)
        journal.record(
            "dispatch",
            "completed",
            idempotency_key="dispatch:completed",
            payload={
                "call_ids": [result.call_id for result in results],
                "statuses": [result.status.value for result in results],
            },
        )
        analysis_inputs = self._analysis_inputs(
            request=request,
            route=route,
            packet=packet,
            results=results,
            self_identity_hash=self_identity_hash,
            self_identity=self_identity,
        )
        analysis = build_analysis(
            run_id=run_id,
            packet_hash=packet.packet_hash,
            panel=route.panel,
            topology=route.topology,
            inputs=analysis_inputs,
            required_handles=route.participants,
        )
        if isinstance(topology_result, PanelRankResult):
            analysis["selection"] = {
                "winner_id": topology_result.winner_id,
                "abstained": topology_result.abstained,
                "order_sensitive": any(
                    comparison.order_sensitive for comparison in topology_result.comparisons
                ),
                "comparisons": [asdict(comparison) for comparison in topology_result.comparisons],
            }
        state.transition(RunState.ANALYZED, reason="structured analysis completed")
        journal.record(
            "analyze",
            "completed",
            idempotency_key="state:analyzed",
            payload={"analysis_hash": sha256_json(analysis)},
        )
        if request.run_grounding and request.artifact_kind == "diff":
            analysis = self._ground(analysis, snapshot, ledger)
            state.transition(RunState.GROUNDED, reason="eligible findings grounded")
            journal.record(
                "ground",
                "completed",
                idempotency_key="state:grounded",
                payload={"grounding_result_count": len(analysis["grounding_results"])},
            )
        if self._topology_degraded(topology_result):
            degradation.append(self._topology_reason(topology_result))
        try:
            assert_snapshot_intact(snapshot)
            assert_snapshot_fresh(snapshot)
        except SnapshotError as error:
            degradation.append(str(error))
        active_wallclock_s = self._wallclock_seconds(started_at, isoformat_z(utc_now()))
        if active_wallclock_s > route.budget.max_wallclock_s:
            degradation.append("active execution exceeded the configured wallclock budget")
        validate_analysis(analysis)
        analysis_path = run_directory / "analysis.json"
        atomic_write_bytes(analysis_path, canonical_analysis_bytes(analysis))
        result_path = run_directory / "result.json"
        atomic_write_json(result_path, result_payload)
        pending_payload = self._pending_payload(
            request=request,
            route=route,
            snapshot=snapshot,
            packet=packet,
            results=results,
            analysis_path=analysis_path,
            started_at=started_at,
            state=state.state.value,
            self_identity_hash=self_identity_hash,
            self_identity=self_identity,
            degradation=tuple(dict.fromkeys(degradation)),
            active_wallclock_s=active_wallclock_s,
            journal_head_hash=journal.summary().journal_head_hash,
        )
        pending_path = run_directory / "pending.json"
        atomic_write_json(pending_path, pending_payload)
        ledger.append(
            "pending-state",
            {"run_id": run_id, "pending_hash": sha256_bytes(pending_path.read_bytes())},
        )
        requires_reconciliation = "self" in route.participants and not degradation
        journal.record(
            "reconcile",
            "waiting" if requires_reconciliation else "started",
            idempotency_key=(
                "reconcile:waiting" if requires_reconciliation else "reconcile:automatic"
            ),
            payload={
                "requires_reconciliation": requires_reconciliation,
                "degraded": bool(degradation),
                "pending_hash": sha256_bytes(pending_path.read_bytes()),
            },
        )
        return PendingRun(
            run_id=run_id,
            state=state.state.value,
            analysis_path=analysis_path,
            pending_path=pending_path,
            evidence_path=evidence_path,
            journal_path=journal.path,
            result_path=result_path,
            requires_reconciliation=requires_reconciliation,
            fused=False,
            degraded=bool(degradation),
        )

    def _dispatch(
        self,
        route: RouteDecision,
        packet: Packet,
        run_id: str,
        budget: BudgetLedger,
    ) -> tuple[tuple[ProviderResult, ...], OrchestrationResult | PanelRankResult, JsonObject]:
        dispatcher = _AsyncRegistryDispatcher(self.registry)
        panel = self.config.panel(route.panel)
        if route.topology == "panel-rank":
            proposer_handles = tuple(
                item for item in panel.get("proposers", []) if isinstance(item, str)
            )
            judge_handle = str(panel.get("judge", ""))
            proposal_schema = load_response_schema("proposal-output.schema.json")
            judge_schema = load_response_schema("pairwise-judge.schema.json")
            proposals = tuple(
                self._request(
                    run_id=run_id,
                    profile=self.config.model(handle),
                    packet=packet,
                    prompt=proposal_prompt(packet),
                    schema=proposal_schema,
                )
                for handle in proposer_handles
            )

            def pairwise_factory(
                left_id: str,
                left: ProviderResult,
                right_id: str,
                right: ProviderResult,
            ) -> ProviderRequest:
                return self._request(
                    run_id=run_id,
                    profile=self.config.model(judge_handle),
                    packet=packet,
                    prompt=pairwise_prompt(
                        left_id=left_id,
                        left=left,
                        right_id=right_id,
                        right=right,
                        packet_hash=packet.packet_hash,
                    ),
                    schema=judge_schema,
                )

            panel_outcome = asyncio.run(
                run_panel_rank(
                    dispatcher,
                    proposals,
                    pairwise_request=pairwise_factory,
                    budget=budget,
                    max_concurrency=min(3, len(proposals)),
                )
            )
            results = (*panel_outcome.proposals, *panel_outcome.judge_results)
            selected: JsonObject | None = None
            if panel_outcome.winner_id is not None:
                index = int(panel_outcome.winner_id.removeprefix("p")) - 1
                if 0 <= index < len(panel_outcome.proposals):
                    selected = panel_outcome.proposals[index].structured_output
            return results, panel_outcome, {
                "topology": "panel-rank",
                "winner_id": panel_outcome.winner_id,
                "abstained": panel_outcome.abstained,
                "selected": selected,
            }
        reviewers = tuple(item for item in panel.get("reviewers", []) if isinstance(item, str))
        if not reviewers:
            advisor = panel.get("advisor")
            if isinstance(advisor, str):
                reviewers = (advisor,)
        schema = load_response_schema("reviewer-output.schema.json")
        requests = tuple(
            self._request(
                run_id=run_id,
                profile=self.config.model(handle),
                packet=packet,
                prompt=review_prompt(
                    packet,
                    role="adversary" if route.topology == "adversarial-review" else "reviewer",
                    adversarial=route.topology == "adversarial-review",
                ),
                schema=schema,
            )
            for handle in reviewers
        )
        if not requests:
            raise PolicyError(f"panel {route.panel} has no callable reviewer")
        if route.topology == "review":
            review_outcome = asyncio.run(run_review(dispatcher, requests[0], budget=budget))
        elif route.topology == "adversarial-review":
            if len(requests) == 1:
                review_outcome = asyncio.run(
                    run_adversarial_review(dispatcher, requests[0], budget=budget)
                )
            else:
                review_outcome = asyncio.run(
                    run_adversarial_council(
                        dispatcher,
                        requests,
                        budget=budget,
                        max_concurrency=min(2, len(requests)),
                    )
                )
        elif route.topology == "dual-review":
            review_outcome = asyncio.run(
                run_dual_review(dispatcher, requests, budget=budget)
            )
        elif route.topology == "advisor":
            review_outcome = asyncio.run(run_advisor(dispatcher, requests[0], budget=budget))
        else:
            raise PolicyError(f"unsupported topology: {route.topology}")
        outputs = [
            {
                "handle": result.handle,
                "status": result.status.value,
                "output": result.structured_output,
            }
            for result in review_outcome.results
        ]
        return (
            review_outcome.results,
            review_outcome,
            {"topology": route.topology, "outputs": outputs},
        )

    def _request(
        self,
        *,
        run_id: str,
        profile: ModelProfile,
        packet: Packet,
        prompt: str,
        schema: JsonObject,
    ) -> ProviderRequest:
        call_id = f"call-{uuid.uuid4().hex[:16]}"
        return ProviderRequest(
            run_id=run_id,
            call_id=call_id,
            handle=profile.handle,
            prompt=prompt,
            response_schema=schema,
            working_directory=packet.snapshot.root,
            timeout_s=min(float(profile.params.get("timeout_s", 600)), 900.0),
            max_output_chars=int(
                self.config.section("guardrails").get("max_output_chars_per_call", 120_000)
            ),
            environment_allowlist=self.credential_broker.for_handle(profile.handle),
            metadata={
                "packet_hash": packet.packet_hash,
                "requested_model": profile.model,
                "effective_model": profile.canonical_model,
                "vendor": profile.vendor,
                "family": profile.family,
                "mode": profile.effort,
                "compound": profile.compound,
                "worker_visibility": profile.worker_visibility,
            },
        )

    def _policy_blocked_results(
        self, route: RouteDecision, reason: str
    ) -> tuple[ProviderResult, ...]:
        results: list[ProviderResult] = []
        for handle in dict.fromkeys(route.participants):
            if handle == "self":
                continue
            profile = self.config.model(handle)
            results.append(
                ProviderResult(
                    call_id=f"call-{uuid.uuid4().hex[:16]}",
                    handle=handle,
                    requested_model=profile.model,
                    effective_model=None,
                    vendor=profile.vendor,
                    family=profile.family,
                    mode=profile.effort,
                    compound=profile.compound,
                    worker_visibility=profile.worker_visibility,
                    status=CallStatus.POLICY_BLOCKED,
                    duration_ms=0,
                    output_text="",
                    structured_output=None,
                    output_hash=None,
                    error=reason,
                )
            )
        return tuple(results)

    def _attest_self(
        self, request: FusionRunRequest, ledger: EvidenceLedger
    ) -> tuple[str, SessionIdentity]:
        if request.self_model is None:
            raise PolicyError("a self-driven panel requires the active session model identity")
        raw_self = self.config.section("models").get("self")
        allowed = raw_self.get("allowed_models", []) if isinstance(raw_self, dict) else []
        if request.self_model not in allowed:
            raise PolicyError(f"active self model is not allowed: {request.self_model}")
        identity = self.config.session_identity(request.self_model)
        fingerprint_hash = (
            sha256_bytes(request.self_session_fingerprint.encode("utf-8"))
            if request.self_session_fingerprint
            else None
        )
        record = ledger.append(
            "self-identity",
            {
                "model": request.self_model,
                "family": identity.family,
                "vendor": identity.vendor,
                "source": request.self_identity_source,
                "session_fingerprint_hash": fingerprint_hash,
            },
        )
        return record.record_hash, identity

    def _attest_calls(
        self, results: tuple[ProviderResult, ...], ledger: EvidenceLedger
    ) -> tuple[ProviderResult, ...]:
        attested: list[ProviderResult] = []
        for result in results:
            routing = ledger.append(
                "provider-routing",
                {
                    "call_id": result.call_id,
                    "handle": result.handle,
                    "requested_model": result.requested_model,
                    "effective_model": result.effective_model,
                    "vendor": result.vendor,
                    "family": result.family,
                    "status": result.status.value,
                    "output_hash": result.output_hash,
                    "provider_route_hash": result.routing_attestation_hash,
                },
            )
            usage_hash: str | None = None
            if result.cost_verified:
                usage = ledger.append(
                    "provider-cost",
                    {
                        "call_id": result.call_id,
                        "cost_usd": result.cost_usd,
                        "cost_source": result.cost_source,
                        "provider_usage_hash": result.provider_usage_hash,
                    },
                )
                usage_hash = usage.record_hash
            attested.append(
                replace(
                    result,
                    routing_attestation_hash=routing.record_hash,
                    provider_usage_hash=usage_hash,
                )
            )
        return tuple(attested)

    def _analysis_inputs(
        self,
        *,
        request: FusionRunRequest,
        route: RouteDecision,
        packet: Packet,
        results: tuple[ProviderResult, ...],
        self_identity_hash: str | None,
        self_identity: SessionIdentity | None,
    ) -> tuple[AnalysisInput, ...]:
        inputs: list[AnalysisInput] = []
        if "self" in route.participants:
            assert request.self_model is not None and self_identity is not None
            self_result = ProviderResult(
                call_id="self",
                handle="self",
                requested_model="self",
                effective_model=request.self_model,
                vendor=self_identity.vendor,
                family=self_identity.family,
                mode="active-session",
                compound=False,
                worker_visibility="not-applicable",
                status=CallStatus.COMPLETED,
                duration_ms=0,
                output_text="",
                structured_output={"findings": []},
                output_hash=sha256_json(
                    {
                        "task": request.task,
                        "packet_hash": packet.packet_hash,
                        "artifact_paths": list(request.artifact_paths),
                    }
                ),
            )
            inputs.append(AnalysisInput(self_result, identity_hash=self_identity_hash))
        grouped: dict[str, list[ProviderResult]] = defaultdict(list)
        for result in results:
            grouped[result.handle].append(result)
        for handle in route.participants:
            if handle == "self":
                continue
            calls = grouped.get(handle, [])
            if not calls:
                profile = self.config.model(handle)
                missing = ProviderResult(
                    call_id="",
                    handle=handle,
                    requested_model=profile.model,
                    effective_model=None,
                    vendor=profile.vendor,
                    family=profile.family,
                    mode=profile.effort,
                    compound=profile.compound,
                    worker_visibility=profile.worker_visibility,
                    status=CallStatus.FAILED,
                    duration_ms=0,
                    output_text="",
                    structured_output=None,
                    output_hash=None,
                    error="participant was not dispatched",
                )
                inputs.append(AnalysisInput(missing, context_complete=False))
                continue
            representative = calls[-1]
            complete = all(call.status is CallStatus.COMPLETED for call in calls)
            if not complete:
                failed = next(
                    call for call in calls if call.status is not CallStatus.COMPLETED
                )
                representative = failed
            identity_hash = representative.routing_attestation_hash
            inputs.append(
                AnalysisInput(
                    representative,
                    context_complete=complete,
                    identity_hash=identity_hash,
                )
            )
        return tuple(inputs)

    def _ground(
        self,
        analysis: JsonObject,
        snapshot: SnapshotManifest,
        ledger: EvidenceLedger,
    ) -> JsonObject:
        candidates = analysis.get("grounding_candidates")
        if not isinstance(candidates, list):
            return analysis
        cached: dict[str, GroundingResult] = {}
        links: list[GroundingLink] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            finding_id = str(candidate.get("finding_id", ""))
            verification_id = str(candidate.get("verification_id", ""))
            result = cached.get(verification_id)
            if result is None:
                try:
                    command = resolve_verification(self.config, verification_id)
                    result = run_grounding(
                        command,
                        snapshot_root=snapshot.root,
                        runner=self.grounding_runner,
                        max_output_chars=32_000,
                    )
                except (GroundingError, PolicyError) as error:
                    result = GroundingResult(
                        verification_id=verification_id,
                        verdict="inconclusive",
                        execution_status="policy-blocked",
                        exit_code=None,
                        failure_class="policy",
                        matched_expected_failure=False,
                        output="",
                        output_truncated=False,
                        output_hash=sha256_bytes(b""),
                        invocation_hash=sha256_json(
                            {"verification_id": verification_id, "blocked": str(error)}
                        ),
                    )
                cached[verification_id] = result
            attestation = ledger.append(
                "grounding",
                {
                    "finding_id": finding_id,
                    "verification_id": verification_id,
                    "verdict": result.verdict,
                    "execution_status": result.execution_status,
                    "exit_code": result.exit_code,
                    "failure_class": result.failure_class,
                    "matched_expected_failure": result.matched_expected_failure,
                    "output_hash": result.output_hash,
                    "invocation_hash": result.invocation_hash,
                },
            )
            links.append(GroundingLink(finding_id, result, attestation.record_hash))
        return attach_grounding(analysis, tuple(links))

    def _pending_payload(
        self,
        *,
        request: FusionRunRequest,
        route: RouteDecision,
        snapshot: SnapshotManifest,
        packet: Packet,
        results: tuple[ProviderResult, ...],
        analysis_path: Path,
        started_at: str,
        state: str,
        self_identity_hash: str | None,
        self_identity: SessionIdentity | None,
        degradation: tuple[str, ...],
        active_wallclock_s: int,
        journal_head_hash: str | None,
    ) -> JsonObject:
        return {
            "version": 1,
            "run_id": str(analysis_path.parent.name),
            "repo_root": str(request.repo_root.resolve()),
            "started_at": started_at,
            "state": state,
            "artifact_kind": request.artifact_kind,
            "pack": request.pack,
            "task_hash": sha256_bytes(request.task.encode("utf-8")),
            "route": self._route_json(route),
            "snapshot": {
                "root": str(snapshot.root),
                "source_root": str(snapshot.source_root),
                "snapshot_hash": snapshot.snapshot_hash,
                "manifest_hash": snapshot.manifest_hash,
                "entries": list(snapshot.entries),
            },
            "packet_hash": packet.packet_hash,
            "analysis_path": str(analysis_path),
            "calls": [self._result_json(result) for result in results],
            "self_model": request.self_model if "self" in route.participants else None,
            "self_identity_source": (
                request.self_identity_source if "self" in route.participants else None
            ),
            "self_identity_hash": self_identity_hash,
            "self_family": self_identity.family if self_identity else None,
            "self_vendor": self_identity.vendor if self_identity else None,
            "requires_reconciliation": "self" in route.participants,
            "degradation_reasons": list(degradation),
            "active_wallclock_s": active_wallclock_s,
            "policy_hash": sha256_json(self.config.data),
            "journal_head_hash": journal_head_hash,
        }

    @staticmethod
    def _route_json(route: RouteDecision) -> JsonObject:
        return {
            "requested_preset": route.requested_preset,
            "selected_preset": route.selected_preset,
            "panel": route.panel,
            "topology": route.topology,
            "participants": list(route.participants),
            "reasons": list(route.reasons),
            "hard_gates": list(route.hard_gates),
            "budget": {
                "max_calls": route.budget.max_calls,
                "max_wallclock_s": route.budget.max_wallclock_s,
                "max_cost_usd": route.budget.max_cost_usd,
                "max_output_chars_per_call": route.budget.max_output_chars_per_call,
            },
        }

    @staticmethod
    def _result_json(result: ProviderResult) -> JsonObject:
        return {
            "call_id": result.call_id,
            "handle": result.handle,
            "requested_model": result.requested_model,
            "effective_model": result.effective_model,
            "vendor": result.vendor,
            "family": result.family,
            "mode": result.mode,
            "compound": result.compound,
            "worker_visibility": result.worker_visibility,
            "status": result.status.value,
            "duration_ms": result.duration_ms,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": result.cost_usd,
            "output_hash": result.output_hash,
            "cost_verified": result.cost_verified,
            "cost_source": result.cost_source,
            "provider_usage_hash": result.provider_usage_hash,
            "routing_attestation_hash": result.routing_attestation_hash,
        }

    def _build_receipt(
        self,
        *,
        pending: JsonObject,
        route: RouteDecision,
        analysis: JsonObject,
        calls: list[JsonObject],
        state: str,
        verdict: str,
        fused: bool,
        degradation: tuple[str, ...],
        finished_at: str,
        signoff_hash: str,
    ) -> JsonObject:
        costs_known = bool(calls) and all(call.get("cost_verified") is True for call in calls)
        cost = (
            sum(float(call.get("cost_usd", 0.0)) for call in calls)
            if costs_known
            else None
        )
        started_at = str(pending["started_at"])
        wallclock_s = int(pending.get("active_wallclock_s", 0))
        counts = self._finding_counts(analysis)
        families = {
            str(call["family"])
            for call in calls
            if call.get("status") == "completed" and call.get("family")
        }
        self_family = pending.get("self_family")
        if isinstance(self_family, str):
            families.add(self_family)
        return {
            "schema_version": "0.2",
            "run_id": pending["run_id"],
            "started_at": started_at,
            "finished_at": finished_at,
            "state": state,
            "verdict": verdict,
            "topology": route.topology,
            "panel": route.panel,
            "preset": route.selected_preset,
            "pack": pending.get("pack"),
            "fused": fused,
            "consulted": route.topology == "advisor",
            "cross_model": len(families) >= 2,
            "compound": any(call.get("compound") is True for call in calls),
            "self_model": pending.get("self_model"),
            "self_identity_source": pending.get("self_identity_source"),
            "self_identity_hash": pending.get("self_identity_hash"),
            "task_hash": pending["task_hash"],
            "snapshot_hash": self._snapshot_object(pending)["snapshot_hash"],
            "packet_hash": pending["packet_hash"],
            "context_complete": bool(analysis.get("context_complete")),
            "requested_participants": list(route.participants),
            "calls": calls,
            "analysis_hash": None,
            "policy_hash": pending["policy_hash"],
            "signoff_attestation_hash": signoff_hash,
            "journal_head_hash": pending.get("journal_head_hash"),
            "budgets": {
                "max_calls": route.budget.max_calls,
                "used_calls": len(calls),
                "max_wallclock_s": route.budget.max_wallclock_s,
                "wallclock_s": wallclock_s,
                "max_cost_usd": route.budget.max_cost_usd,
                "cost_usd": cost,
                "cost_verified": costs_known,
            },
            "findings": counts,
            "degradation_reasons": list(degradation),
            "privacy": "metadata-only",
        }

    @staticmethod
    def _wallclock_seconds(started_at: str, finished_at: str) -> int:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        return max(0, math.ceil((finished - started).total_seconds()))

    @staticmethod
    def _finding_counts(analysis: JsonObject) -> JsonObject:
        findings = analysis.get("findings")
        items = (
            [item for item in findings if isinstance(item, dict)]
            if isinstance(findings, list)
            else []
        )
        grounding = analysis.get("grounding_results")
        results = (
            [item for item in grounding if isinstance(item, dict)]
            if isinstance(grounding, list)
            else []
        )
        proof = analysis.get("proof_results")
        proof_results = (
            [item for item in proof if isinstance(item, dict)]
            if isinstance(proof, list)
            else []
        )
        return {
            "blocker": sum(item.get("severity") == "blocker" for item in items),
            "major": sum(item.get("severity") == "major" for item in items),
            "minor": sum(item.get("severity") == "minor" for item in items),
            "confirmed_by_exec": len(
                {
                    str(item.get("finding_id"))
                    for item in results
                    if item.get("verdict") == "confirmed"
                }
            ),
            "confirmed_by_proof": len(
                {
                    str(item.get("finding_id"))
                    for item in proof_results
                    if item.get("verdict") == "confirmed"
                    and item.get("mutation_gate_passed") is True
                }
            ),
            "deadlocks": sum(item.get("status") == "deadlock" for item in items),
        }

    @staticmethod
    def _required_calls_completed(route: RouteDecision, calls: list[JsonObject]) -> bool:
        completed = {
            str(call["handle"]) for call in calls if call.get("status") == "completed"
        }
        return set(route.participants) - {"self"} <= completed

    @staticmethod
    def _topology_degraded(result: OrchestrationResult | PanelRankResult) -> bool:
        return result.degraded

    @staticmethod
    def _topology_reason(result: OrchestrationResult | PanelRankResult) -> str:
        if isinstance(result, OrchestrationResult):
            return result.reason or "orchestration degraded"
        if result.abstained:
            return "panel rank abstained because comparison was unstable or incomplete"
        return "panel rank degraded"

    def _run_directory(self, run_id: str) -> Path:
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
        if not run_id or any(character not in allowed for character in run_id):
            raise ReceiptError("run_id contains unsafe characters")
        return self.work_root / run_id

    @staticmethod
    def _snapshot_object(pending: JsonObject) -> JsonObject:
        raw = pending.get("snapshot")
        if not isinstance(raw, dict):
            raise ReceiptError("pending snapshot record is invalid")
        return dict(raw)

    def _snapshot_from_pending(self, pending: JsonObject) -> SnapshotManifest:
        raw = self._snapshot_object(pending)
        entries_raw = raw.get("entries")
        if not isinstance(entries_raw, list) or not all(
            isinstance(item, dict) for item in entries_raw
        ):
            raise ReceiptError("pending snapshot manifest entries are invalid")
        entries = tuple(dict(item) for item in entries_raw if isinstance(item, dict))
        return SnapshotManifest(
            root=Path(str(raw["root"])),
            source_root=Path(str(raw["source_root"])),
            snapshot_hash=str(raw["snapshot_hash"]),
            manifest_hash=str(raw["manifest_hash"]),
            entries=entries,
        )

    @staticmethod
    def _route_from_pending(pending: JsonObject) -> RouteDecision:
        raw = pending.get("route")
        if not isinstance(raw, dict):
            raise ReceiptError("pending route record is invalid")
        budget = raw.get("budget")
        if not isinstance(budget, dict):
            raise ReceiptError("pending budget record is invalid")
        from autofusion.models import RunBudget

        return RouteDecision(
            requested_preset=str(raw["requested_preset"]),
            selected_preset=str(raw["selected_preset"]),
            panel=str(raw["panel"]),
            topology=str(raw["topology"]),
            participants=tuple(str(item) for item in raw["participants"]),
            reasons=tuple(str(item) for item in raw["reasons"]),
            hard_gates=tuple(str(item) for item in raw["hard_gates"]),
            budget=RunBudget(
                max_calls=int(budget["max_calls"]),
                max_wallclock_s=int(budget["max_wallclock_s"]),
                max_cost_usd=(
                    float(budget["max_cost_usd"])
                    if budget.get("max_cost_usd") is not None
                    else None
                ),
                max_output_chars_per_call=int(budget["max_output_chars_per_call"]),
            ),
        )

    @staticmethod
    def _call_records(pending: JsonObject) -> list[JsonObject]:
        raw = pending.get("calls")
        if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
            raise ReceiptError("pending call records are invalid")
        mapping = {
            "timeout": "timed-out",
            "cancelled": "failed",
            "policy_blocked": "policy-blocked",
        }
        records: list[JsonObject] = []
        for item in raw:
            record = dict(item)
            record["status"] = mapping.get(str(record.get("status")), record.get("status"))
            records.append(record)
        return records
