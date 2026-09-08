"""Correlated, durable request/result handoff owned by the active session.

Python prepares and verifies evidence; only the session dispatches delegates.
Returned identities are session assertions, not independently signed provider evidence.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

from autofusion.budget import BudgetLedger
from autofusion.contracts import _validate_schema
from autofusion.dlp import scan_snapshot_for_secrets
from autofusion.engine import (
    FusionEngine,
    FusionRunRequest,
    PendingRun,
    PreparedRun,
    _finalization_lock,
)
from autofusion.errors import PolicyError, ReceiptError
from autofusion.evidence import EvidenceLedger
from autofusion.identity import SessionIdentity, assert_executable_identity, names_fable
from autofusion.models import CallStatus, Packet, ProviderResult, SnapshotManifest
from autofusion.packet import context_fit_error, shared_context_limit
from autofusion.prompts import load_response_schema, review_prompt
from autofusion.snapshot import assert_snapshot_fresh, assert_snapshot_intact
from autofusion.topologies import OrchestrationResult
from autofusion.util import (
    JsonObject,
    atomic_write_json,
    canonical_json_bytes,
    read_json_object,
    sha256_bytes,
    sha256_json,
    utc_now,
)

# Fixed registered delegate contract, not CLI executable names:
# https://github.com/OnourImpram/claude-oauth/blob/d8404b0/src/supervisor/launcher.ts
DELEGATE_MODELS: dict[str, tuple[str, str]] = {
    "sol-delege": ("gpt-5.6-sol", "openai"),
    "astra-delege": ("gpt-6-astra", "openai"),
    "terra-delege": ("gpt-5.6-terra", "openai"),
    "gemini-delege": ("gemini-3.8-flash-high", "google"),
    "grok-delege": ("grok-4.6", "xai"),
}
CORRELATION_FIELDS = (
    "schema_version",
    "run_id",
    "call_id",
    "packet_hash",
    "artifact_manifest_hash",
    "reviewer_schema_id",
    "reviewer_schema_hash",
)
_MAX_BUNDLE_BYTES = 4 * 1024 * 1024
_MAX_RESPONSE_BYTES = 1024 * 1024


def validate_session_request(request: JsonObject) -> None:
    """Validate the exported contract and its content bindings without dispatch."""
    _validate_schema(request, "session-request.schema.json")
    for field, identifier in (
        ("packet", "packet_hash"),
        ("artifact_manifest", "artifact_manifest_hash"),
        ("reviewer_schema", "reviewer_schema_hash"),
    ):
        if sha256_json(request[field]) != request[identifier]:
            raise ReceiptError(f"session request {identifier} does not match its content")
    schema = load_response_schema("reviewer-output.schema.json")
    if request["reviewer_schema"] != schema or request["reviewer_schema_id"] != schema["$id"]:
        raise ReceiptError("session request does not use the installed reviewer schema")
    identity = request["requested_identity"]
    if names_fable(identity) or identity["handle"] == "self":
        raise ReceiptError("Fable and self cannot be delegate targets")
    delegate = identity["delegate"]
    if delegate is not None and DELEGATE_MODELS.get(delegate) != (
        identity["model"],
        identity["vendor"],
    ):
        raise ReceiptError("delegate does not match the requested model and vendor")
    if request["dispatchable"] != (delegate is not None):
        raise ReceiptError("session dispatchability must match delegate availability")
    if request["artifact_manifest"] != request["packet"]["snapshot"]:
        raise ReceiptError("session artifact manifest must match the frozen packet")
    manifest = request["artifact_manifest"]
    packet = Packet(
        request["packet"],
        request["packet_hash"],
        SnapshotManifest(
            root=Path("."),
            source_root=Path("."),
            snapshot_hash=manifest["snapshot_hash"],
            manifest_hash=manifest["manifest_hash"],
            entries=tuple(manifest["entries"]),
        ),
    )
    if request["prompt"] != review_prompt(packet, role="reviewer"):
        raise ReceiptError("session prompt does not match its frozen review packet")


def validate_session_result(result: JsonObject) -> None:
    """Validate a result envelope; reviewer payload validity is checked on import."""
    _validate_schema(result, "session-result.schema.json")
    identity = result["actual_identity"]
    if names_fable(identity):
        raise ReceiptError("Fable cannot be a callable reviewer or returned delegate identity")


@contextmanager
def _bridge_lock(directory: Path) -> Iterator[None]:
    """Share the finalizer's stable cross-process lock, allowing short contention."""
    if not directory.is_dir():
        raise ReceiptError("session bridge run does not exist")
    deadline = time.monotonic() + 10
    while True:
        lock = _finalization_lock(directory)
        try:
            lock.__enter__()
            break
        except ReceiptError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


class SessionBridge:
    """One bounded review handoff, with append-only terminal call consumption."""

    def __init__(self, engine: FusionEngine) -> None:
        self.engine = engine

    def export(self, request: FusionRunRequest, delegates: dict[str, str]) -> JsonObject:
        route, _ = self.engine._resolve_route(request)
        if route.topology not in {"review", "dual-review"} or "self" not in route.participants:
            raise PolicyError("session bridge requires review or dual-review with session self")
        handles = tuple(handle for handle in route.participants if handle != "self")
        if len(handles) != (2 if route.topology == "dual-review" else 1):
            raise PolicyError("session bridge requires exactly one or two first-pass reviewers")
        if len({self.engine.config.model(handle).canonical_model for handle in handles}) != len(
            handles
        ):
            raise PolicyError("session dual-review requires distinct requested models")
        if set(delegates) - set(handles):
            raise PolicyError("delegate mapping includes a participant outside this review")
        if route.budget.max_cost_usd is not None:
            raise PolicyError("session bridge cannot verify cost under a hard cost budget")
        for handle in handles:
            profile = self.engine.config.model(handle)
            assert_executable_identity(profile)
            delegate = delegates.get(handle)
            if delegate is not None and DELEGATE_MODELS.get(delegate) != (
                profile.canonical_model,
                profile.vendor,
            ):
                raise PolicyError("delegate must be registered for the requested model and vendor")
        prepared = self.engine._freeze(request, packet_only=True)
        # Every registered delegate inherits tools, even if its transport profile is packet-only.
        if scan_snapshot_for_secrets(prepared.snapshot).blocked:
            raise PolicyError("session delegate export blocked: snapshot matched secret patterns")
        limit = shared_context_limit(
            tuple(self.engine.config.model(handle) for handle in route.participants)
        )
        schema = load_response_schema("reviewer-output.schema.json")
        prompt = review_prompt(prepared.packet, role="reviewer")
        calls = tuple(
            self.engine._request(
                run_id=prepared.run_id,
                profile=self.engine.config.model(handle),
                packet=prepared.packet,
                prompt=prompt,
                schema=schema,
                shared_input_limit=limit,
            )
            for handle in handles
        )
        context_error = context_fit_error(calls)
        if context_error:
            raise PolicyError(context_error)
        requests: list[JsonObject] = []
        for call in calls:
            prepared.budget.reserve_call(output_chars=call.max_output_chars)
            profile = self.engine.config.model(call.handle)
            delegate = delegates.get(call.handle)
            exported: JsonObject = {
                "schema_version": "1",
                "run_id": prepared.run_id,
                "call_id": call.call_id,
                "packet_hash": prepared.packet.packet_hash,
                "packet": prepared.packet.payload,
                "artifact_manifest": prepared.packet.payload["snapshot"],
                "artifact_manifest_hash": sha256_json(prepared.packet.payload["snapshot"]),
                "reviewer_schema_id": schema["$id"],
                "reviewer_schema_hash": sha256_json(schema),
                "reviewer_schema": schema,
                "role": "reviewer",
                "dispatchable": delegate is not None,
                "requested_identity": {
                    "handle": call.handle,
                    "model": profile.canonical_model,
                    "vendor": profile.vendor,
                    "family": profile.family,
                    "delegate": delegate,
                },
                "prompt": call.prompt,
                "max_output_chars": call.max_output_chars,
            }
            validate_session_request(exported)
            requests.append(exported)
        bundle: JsonObject = {
            "schema_version": "1",
            "run_id": prepared.run_id,
            "topology": route.topology,
            "requests": requests,
        }
        if len(canonical_json_bytes(bundle)) > _MAX_BUNDLE_BYTES:
            raise PolicyError("session request bundle exceeds the 4 MiB bound")
        directory = self.engine._run_directory(prepared.run_id)
        state = self._prepared_json(prepared, bundle)
        atomic_write_json(directory / "session-bridge.json", state)
        EvidenceLedger(directory / "evidence.jsonl").append(
            "session-export",
            {
                "run_id": prepared.run_id,
                "state_hash": sha256_bytes((directory / "session-bridge.json").read_bytes()),
            },
        )
        return bundle

    def import_result(self, run_id: str, result: JsonObject) -> JsonObject:
        return self.import_results(run_id, [result])[0]

    def requests(self, run_id: str) -> JsonObject:
        """Read the original bundle after verifying its durable evidence and artifacts."""
        with _bridge_lock(self.engine._run_directory(run_id)):
            state, _ = self._load(run_id)
            return {key: state[key] for key in ("schema_version", "run_id", "topology", "requests")}

    def import_results(self, run_id: str, results: list[JsonObject]) -> list[JsonObject]:
        """Validate the entire batch before atomically consuming any outstanding call."""
        directory = self.engine._run_directory(run_id)
        with _bridge_lock(directory):
            state, accepted = self._load(run_id)
            if not results or len(results) > len(state["requests"]):
                raise ReceiptError("session result batch must fit the outstanding review")
            normalized: list[JsonObject] = []
            seen: set[str] = set()
            requests = {item["call_id"]: item for item in state["requests"]}
            for result in results:
                call_id = result.get("call_id")
                if not isinstance(call_id, str) or call_id not in requests:
                    raise ReceiptError("result does not identify an outstanding call")
                if call_id in accepted or call_id in seen or not requests[call_id]["dispatchable"]:
                    raise ReceiptError("result call is no longer outstanding")
                seen.add(call_id)
                normalized.append(self._normalize(requests[call_id], result))
            self._persist_results(directory, normalized)
            return [
                {"run_id": run_id, "call_id": item["call_id"], "status": item["status"]}
                for item in normalized
            ]

    def complete(self, run_id: str) -> PendingRun:
        """Seal missing calls and enter the direct transport evidence pipeline."""
        directory = self.engine._run_directory(run_id)
        with _bridge_lock(directory):
            state, accepted = self._load(run_id)
            missing = [
                self._missing(req) for req in state["requests"] if req["call_id"] not in accepted
            ]
            if missing:
                self._persist_results(directory, missing)
                accepted.update({item["call_id"]: item for item in missing})
            prepared = self._restore_prepared(state)
            results = tuple(
                self._provider_result(req, accepted[req["call_id"]]) for req in state["requests"]
            )
            incomplete = any(result.status != CallStatus.COMPLETED for result in results)
            abstaining = any(item["status"] == "abstaining" for item in accepted.values())
            outcome = OrchestrationResult(
                topology=prepared.route.topology,
                results=results,
                required_handles=tuple(result.handle for result in results),
                context_complete=not incomplete and not abstaining,
                fused=False,
                degraded=incomplete or abstaining,
                reason="session review contains incomplete or abstaining calls"
                if incomplete or abstaining
                else None,
            )
            payload: JsonObject = {
                "topology": prepared.route.topology,
                "outputs": [
                    {
                        "handle": result.handle,
                        "call_id": result.call_id,
                        "status": result.status.value,
                        "bridge_status": accepted[result.call_id]["status"],
                        "output": result.structured_output,
                    }
                    for result in results
                ],
            }
            EvidenceLedger(directory / "evidence.jsonl").append(
                "session-completing",
                {
                    "run_id": run_id,
                    "call_ids": [result.call_id for result in results],
                },
            )
            return self.engine._finish_prepared(prepared, results, outcome, payload)

    def _load(self, run_id: str) -> tuple[JsonObject, dict[str, JsonObject]]:
        directory = self.engine._run_directory(run_id)
        if (directory / "pending.json").exists() or (directory / "finalized.json").exists():
            raise ReceiptError("session bridge is already complete")
        try:
            raw = (directory / "session-bridge.json").read_bytes()
            state = read_json_object(directory / "session-bridge.json")
        except (OSError, ValueError) as error:
            raise ReceiptError("session bridge state is unavailable or malformed") from error
        records = EvidenceLedger(directory / "evidence.jsonl").verify()
        exports = [record for record in records if record.kind == "session-export"]
        if len(exports) != 1 or exports[0].payload.get("state_hash") != sha256_bytes(raw):
            raise ReceiptError("session bridge state is not bound to the evidence ledger")
        if state["run_id"] != run_id or state["policy_hash"] != sha256_json(
            self.engine.config.data
        ):
            raise ReceiptError("session bridge run or policy changed after export")
        if any(record.kind == "session-completing" for record in records):
            raise ReceiptError("session bridge completion already started; replay is forbidden")
        snapshot = self.engine._snapshot_from_pending(state)
        assert_snapshot_intact(snapshot)
        assert_snapshot_fresh(snapshot)
        for request in state["requests"]:
            validate_session_request(request)
        accepted: dict[str, JsonObject] = {}
        requests = {item["call_id"]: item for item in state["requests"]}
        for record in records:
            if record.kind != "session-results":
                continue
            result_hash = record.payload.get("results_hash")
            if (
                not isinstance(result_hash, str)
                or len(result_hash) != 64
                or any(char not in "0123456789abcdef" for char in result_hash)
            ):
                raise ReceiptError("session result evidence hash is invalid")
            path = directory / "session-results" / f"{result_hash}.json"
            try:
                result_bytes = path.read_bytes()
                batch = json.loads(result_bytes)
            except (OSError, ValueError) as error:
                raise ReceiptError("accepted session result bytes are unavailable") from error
            if sha256_bytes(result_bytes) != result_hash or not isinstance(batch, dict):
                raise ReceiptError("accepted session result bytes changed")
            for item in batch["results"]:
                call_id = item["call_id"]
                if call_id in accepted or call_id not in requests:
                    raise ReceiptError("accepted session call is duplicated or unknown")
                for field in CORRELATION_FIELDS:
                    if item[field] != requests[call_id][field]:
                        raise ReceiptError("accepted session result correlation changed")
                accepted[call_id] = item
        return state, accepted

    @staticmethod
    def _normalize(request: JsonObject, result: JsonObject) -> JsonObject:
        for field in CORRELATION_FIELDS:
            if result.get(field) != request[field]:
                raise ReceiptError(f"session result {field} does not match the outstanding call")
        try:
            raw = canonical_json_bytes(result)
        except (ValueError, TypeError) as error:
            raise ReceiptError("session result must be finite JSON") from error
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ReceiptError("session result exceeds the 1 MiB bound")
        actual = result.get("actual_identity")
        if names_fable(actual):
            raise ReceiptError("Fable cannot be a callable reviewer or delegate target")
        expected = {
            key: request["requested_identity"][key]
            for key in ("model", "vendor", "family", "delegate")
        }
        if actual is not None and actual != expected:
            raise ReceiptError("returned identity differs from the requested delegate identity")
        normalized = json.loads(raw)
        malformed: str | None = None
        try:
            validate_session_result(result)
        except ReceiptError:
            malformed = "result envelope is malformed"
        status = result.get("status")
        output = result.get("output")
        if status in {"completed", "abstaining"}:
            if actual is None:
                malformed = "completed result lacks an actual returned identity"
            if not isinstance(output, dict) or not Draft202012Validator(
                request["reviewer_schema"]
            ).is_valid(output):
                malformed = "reviewer output is malformed or partial"
            if isinstance(output, dict) and output.get("recommendation") == "abstain":
                normalized["status"] = "abstaining"
        if len(canonical_json_bytes(output).decode("utf-8")) > request["max_output_chars"]:
            malformed = "reviewer output exceeds the per-call bound"
        if malformed:
            normalized = {
                **{key: request[key] for key in CORRELATION_FIELDS},
                "status": "malformed",
                "actual_identity": actual,
                "output": None,
                "error": malformed,
                "rejected_result_hash": sha256_bytes(raw),
            }
        return dict(normalized)

    @staticmethod
    def _missing(request: JsonObject) -> JsonObject:
        return {
            **{key: request[key] for key in CORRELATION_FIELDS},
            "status": "missing",
            "actual_identity": None,
            "output": None,
            "error": "delegate unavailable"
            if not request["dispatchable"]
            else "outstanding delegate returned no result before completion",
        }

    @staticmethod
    def _persist_results(directory: Path, results: list[JsonObject]) -> None:
        batch: JsonObject = {"results": results}
        raw = canonical_json_bytes(batch) + b"\n"
        result_hash = sha256_bytes(raw)
        atomic_write_json(directory / "session-results" / f"{result_hash}.json", batch)
        EvidenceLedger(directory / "evidence.jsonl").append(
            "session-results",
            {
                "results_hash": result_hash,
                "calls": [
                    {"call_id": item["call_id"], "status": item["status"]} for item in results
                ],
            },
        )

    def _provider_result(self, request: JsonObject, result: JsonObject) -> ProviderResult:
        profile = self.engine.config.model(request["requested_identity"]["handle"])
        completed = result["status"] in {"completed", "abstaining"}
        output = result["output"] if completed else None
        # Abstention is a completed review with incomplete coverage, as in direct transports.
        if completed and result["status"] == "abstaining" and isinstance(output, dict):
            output = {**output, "recommendation": "abstain"}
        output_text = canonical_json_bytes(output).decode("utf-8") if output is not None else ""
        actual = result["actual_identity"]
        observed = actual["model"] if completed and isinstance(actual, dict) else None
        return ProviderResult(
            call_id=request["call_id"],
            handle=profile.handle,
            requested_model=profile.model,
            configured_model=profile.canonical_model,
            effective_model=observed,
            observed_model=observed,
            identity_evidence="session-asserted" if observed else "unavailable",
            quota_group=profile.quota_group,
            vendor=profile.vendor,
            family=profile.family,
            mode=profile.effort,
            compound=profile.compound,
            worker_visibility=profile.worker_visibility,
            status=CallStatus.COMPLETED
            if completed
            else (CallStatus.CANCELLED if result["status"] == "cancelled" else CallStatus.FAILED),
            duration_ms=0,
            output_text=output_text,
            structured_output=output,
            output_hash=sha256_json(output) if output is not None else None,
            error=result.get("error"),
        )

    def _prepared_json(self, prepared: PreparedRun, bundle: JsonObject) -> JsonObject:
        request = prepared.request
        snapshot = prepared.snapshot
        return {
            **bundle,
            "policy_hash": sha256_json(self.engine.config.data),
            "request": {
                "task": request.task,
                "repo_root": str(request.repo_root.resolve()),
                "artifact_kind": request.artifact_kind,
                "artifact_paths": list(request.artifact_paths),
                "preset": request.preset,
                "panel": request.panel,
                "external_only": request.external_only,
                "self_model": request.self_model,
                "self_identity_source": request.self_identity_source,
                "focus_role": request.focus_role,
                "constraints": request.constraints,
                "run_grounding": request.run_grounding,
                "pack": request.pack,
            },
            "route": self.engine._route_json(prepared.route),
            "snapshot": {
                "root": str(snapshot.root),
                "source_root": str(snapshot.source_root),
                "snapshot_hash": snapshot.snapshot_hash,
                "manifest_hash": snapshot.manifest_hash,
                "entries": list(snapshot.entries),
            },
            "packet": prepared.packet.payload,
            "packet_hash": prepared.packet.packet_hash,
            "started_at": prepared.started_at,
            "reviewed_tree_hash": prepared.reviewed_tree_hash,
            "self_identity_hash": prepared.self_identity_hash,
            "self_identity": {
                "family": prepared.self_identity.family,
                "vendor": prepared.self_identity.vendor,
            }
            if prepared.self_identity
            else None,
            "degradation": list(prepared.degradation),
        }

    def _restore_prepared(self, state: JsonObject) -> PreparedRun:
        request_raw = dict(state["request"])
        request_raw["repo_root"] = Path(request_raw["repo_root"])
        request_raw["artifact_paths"] = tuple(request_raw["artifact_paths"])
        request = FusionRunRequest(**request_raw)
        snapshot = self.engine._snapshot_from_pending(state)
        route = self.engine._route_from_pending(state)
        elapsed = max(
            0.0,
            (
                utc_now() - datetime.fromisoformat(state["started_at"].replace("Z", "+00:00"))
            ).total_seconds(),
        )
        identity = state["self_identity"]
        return PreparedRun(
            request=request,
            run_id=state["run_id"],
            route=route,
            snapshot=snapshot,
            packet=Packet(state["packet"], state["packet_hash"], snapshot),
            started_at=state["started_at"],
            reviewed_tree_hash=state["reviewed_tree_hash"],
            self_identity_hash=state["self_identity_hash"],
            self_identity=SessionIdentity(**identity) if identity else None,
            degradation=tuple(state["degradation"]),
            repo_access_blocked=False,
            budget=BudgetLedger(route.budget, started_at=time.monotonic() - elapsed),
        )
