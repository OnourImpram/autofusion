"""Command line interface for the callable autofusion runtime."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from dataclasses import asdict, replace
from pathlib import Path

from autofusion.config import FusionConfig, load_config
from autofusion.contracts import (
    validate_linked_paths,
    validate_proof_capsule,
    validate_proof_intent,
)
from autofusion.doctor import inspect_environment
from autofusion.engine import FusionEngine, FusionRunRequest, PendingRun
from autofusion.errors import AutofusionError, PolicyError
from autofusion.evaluation import EvaluationObservation, ReviewArm, aggregate_metrics
from autofusion.github_report import build_github_check_report
from autofusion.grounding import resolve_verification, run_grounding
from autofusion.models import ProviderRequest, RunArtifacts
from autofusion.policy_signing import (
    PolicyBundleSignature,
    sign_policy_bundle,
    verify_policy_bundle,
)
from autofusion.precedent import (
    OutcomeVerdict,
    PrecedentLedger,
    finding_fingerprint,
    repository_scope_hash,
)
from autofusion.proof import (
    ProofCapsule,
    VerificationIntent,
    capsule_from_json,
    hash_proof_tree,
    proof_policy_from_config,
    run_proof,
    sign_proof_capsule,
)
from autofusion.receipt import ReceiptStore
from autofusion.reconcile import FindingDisposition
from autofusion.registry import ProviderRegistry
from autofusion.sandbox import detect_grounding_runner, detect_proof_runner
from autofusion.snapshot import build_snapshot
from autofusion.util import (
    JsonObject,
    atomic_write_json,
    read_json_object,
)

LOGGER = logging.getLogger("autofusion")


def _emit(value: object) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _path(value: str | None) -> Path | None:
    return Path(value).resolve() if value else None


def _config(args: argparse.Namespace) -> FusionConfig:
    repo = _path(getattr(args, "repo", None))
    return load_config(
        repo,
        explicit_path=_path(getattr(args, "config", None)),
        trust_repo_config=bool(getattr(args, "trust_repo_config", False)),
    )


def _engine(args: argparse.Namespace) -> FusionEngine:
    config = _config(args)
    return FusionEngine(
        config,
        ProviderRegistry.from_config(config),
        work_root=_path(getattr(args, "work_root", None)),
        grounding_runner=detect_grounding_runner(),
    )


def _task(args: argparse.Namespace) -> str:
    if args.task_file:
        return Path(args.task_file).read_text(encoding="utf-8")
    if args.task:
        return str(args.task)
    raise ValueError("one of --task or --task-file is required")


def _dispositions(path: str | None) -> tuple[FindingDisposition, ...] | None:
    if path is None:
        return None
    parsed: object = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(parsed, dict):
        parsed = parsed.get("dispositions")
    if not isinstance(parsed, list):
        raise ValueError("dispositions file must contain an array")
    values: list[FindingDisposition] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("each disposition must be an object")
        values.append(
            FindingDisposition(
                finding_id=str(item.get("finding_id", "")),
                disposition=str(item.get("disposition", "")),
                rationale=str(item.get("rationale", "")),
            )
        )
    return tuple(values)


def _proof_capsules(paths: list[str]) -> tuple[ProofCapsule, ...]:
    capsules: list[ProofCapsule] = []
    for path in paths:
        raw = read_json_object(Path(path))
        validate_proof_capsule(raw)
        capsules.append(capsule_from_json(raw))
    return tuple(capsules)


def _run(args: argparse.Namespace) -> int:
    engine = _engine(args)
    fingerprint = None
    if args.self_session_fingerprint_env:
        fingerprint = os.environ.get(args.self_session_fingerprint_env)
        if fingerprint is None:
            raise PolicyError("self session fingerprint variable is not set")
    result = engine.run(
        FusionRunRequest(
            task=_task(args),
            repo_root=Path(args.repo),
            artifact_kind=args.kind,
            artifact_paths=tuple(args.path),
            preset=args.preset,
            panel=args.panel,
            external_only=args.external_only,
            self_model=args.self_model,
            self_session_fingerprint=fingerprint,
            focus_role=args.focus,
            run_grounding=not args.no_grounding,
            pack=args.pack,
        )
    )
    if isinstance(result, PendingRun):
        _emit(
            {
                "run_id": result.run_id,
                "state": result.state,
                "requires_reconciliation": result.requires_reconciliation,
                "analysis_path": str(result.analysis_path),
                "result_path": str(result.result_path),
                "pending_path": str(result.pending_path),
                "journal_path": str(result.journal_path),
                "degraded": result.degraded,
            }
        )
    else:
        _emit(_artifacts_json(result))
    return 0


def _finalize(args: argparse.Namespace) -> int:
    artifacts = _engine(args).finalize(
        args.run_id,
        dispositions=_dispositions(args.dispositions),
        automatic=args.automatic,
        proof_capsules=_proof_capsules(args.proof_capsule),
    )
    _emit(_artifacts_json(artifacts))
    return 0


def _artifacts_json(artifacts: RunArtifacts) -> JsonObject:
    return {
        "run_id": artifacts.run_id,
        "analysis_path": str(artifacts.analysis_path),
        "receipt_path": str(artifacts.receipt_path),
        "state": artifacts.receipt["state"],
        "verdict": artifacts.receipt["verdict"],
        "fused": artifacts.receipt["fused"],
        "receipt_hash": artifacts.receipt["receipt_hash"],
    }


def _doctor(args: argparse.Namespace) -> int:
    config = _config(args)
    _emit(
        inspect_environment(
            config,
            grounding_runner=detect_grounding_runner(),
            proof_runner=detect_proof_runner(config.section("proof")),
        )
    )
    return 0


def _config_validate(args: argparse.Namespace) -> int:
    config = _config(args)
    _emit(
        {
            "valid": True,
            "sources": [str(path) for path in config.source_paths],
            "models": sorted(config.section("models")),
            "panels": sorted(config.section("panels")),
            "presets": sorted(config.section("presets")),
            "packs": sorted(config.section("packs")),
        }
    )
    return 0


def _call(args: argparse.Namespace) -> int:
    config = _config(args)
    registry = replace(ProviderRegistry.from_config(config), config=config)
    repo = Path(args.repo).resolve()
    work = _path(args.work_root) or Path.home() / ".fusion" / "work"
    snapshot = build_snapshot(repo, work / f"call-{uuid.uuid4().hex}" / "snapshots")
    schema = read_json_object(Path(args.schema))
    prompt = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else args.prompt
    if not prompt:
        raise ValueError("one of --prompt or --prompt-file is required")
    profile = config.model(args.handle)
    result = registry.invoke(
        ProviderRequest(
            run_id=f"call-{uuid.uuid4().hex[:12]}",
            call_id=f"call-{uuid.uuid4().hex[:16]}",
            handle=args.handle,
            prompt=prompt,
            response_schema=schema,
            working_directory=snapshot.root,
            timeout_s=args.timeout,
            max_output_chars=args.max_output_chars,
            metadata={"packet_hash": "0" * 64},
        )
    )
    _emit(
        {
            "status": result.status.value,
            "handle": result.handle,
            "requested_model": profile.model,
            "effective_model": result.effective_model,
            "duration_ms": result.duration_ms,
            "output_hash": result.output_hash,
            "structured_output": result.structured_output,
            "error": result.error,
            "stderr_tail": result.stderr_tail,
            "truncated": result.truncated,
        }
    )
    return 0 if result.status.value == "completed" else 3


def _ground(args: argparse.Namespace) -> int:
    config = _config(args)
    runner = detect_grounding_runner()
    if runner is None:
        raise PolicyError("no verified network-denied grounding runner is available")
    repo = Path(args.repo).resolve()
    work = _path(args.work_root) or Path.home() / ".fusion" / "work"
    snapshot = build_snapshot(repo, work / f"ground-{uuid.uuid4().hex}" / "snapshots")
    result = run_grounding(
        resolve_verification(config, args.verification_id),
        snapshot_root=snapshot.root,
        runner=runner,
        max_output_chars=args.max_output_chars,
    )
    _emit(result.as_json())
    return 0


def _run_status(args: argparse.Namespace) -> int:
    _emit(_engine(args).status(args.run_id))
    return 0


def _revision_paths(values: list[str]) -> dict[str, Path]:
    revisions: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or not raw_path:
            raise ValueError("proof revisions must use name=path")
        if name in revisions:
            raise ValueError(f"duplicate proof revision: {name}")
        revisions[name] = Path(raw_path).resolve()
    return revisions


def _proof_hash(args: argparse.Namespace) -> int:
    _emit({"root": str(Path(args.root).resolve()), "tree_hash": hash_proof_tree(Path(args.root))})
    return 0


def _prove(args: argparse.Namespace) -> int:
    config = _config(args)
    settings = config.section("proof")
    key_env = str(settings["attestation_key_env"])
    signing_key = os.environ.get(key_env)
    if signing_key is None:
        raise PolicyError(f"proof attestation key variable is not set: {key_env}")
    runner = detect_proof_runner(settings)
    if runner is None:
        raise PolicyError(
            "no attested disposable Docker proof runner is available for the configured image"
        )
    raw_intent = read_json_object(Path(args.intent))
    validate_proof_intent(raw_intent)
    intent = VerificationIntent.from_json(raw_intent)
    command = resolve_verification(config, intent.verification_id)
    work_root = _path(args.work_root) or Path.home() / ".fusion" / "proof-work"
    capsule = run_proof(
        intent,
        revisions=_revision_paths(args.revision),
        overlay_root=Path(args.overlay).resolve(),
        command=command,
        runner=runner,
        policy=proof_policy_from_config(settings),
        work_root=work_root,
    )
    capsule = sign_proof_capsule(
        capsule,
        key_id=str(settings["attestation_key_id"]),
        signing_key=signing_key.encode("utf-8"),
    )
    payload = capsule.as_json()
    validate_proof_capsule(payload)
    atomic_write_json(Path(args.output), payload)
    _emit(payload)
    return 0 if capsule.verdict.value == "confirmed" else 3


def _packs(args: argparse.Namespace) -> int:
    config = _config(args)
    _emit({"packs": config.section("packs")})
    return 0


def _precedent_ledger(args: argparse.Namespace) -> tuple[PrecedentLedger, FusionConfig]:
    config = _config(args)
    settings = config.section("precedent")
    if settings.get("enabled") is not True:
        raise PolicyError("precedent storage is disabled by policy")
    relative = Path(str(settings["directory"]))
    root = Path(args.repo).resolve()
    ledger = PrecedentLedger(
        root / relative,
        repository_scope_hash(args.repository_id),
    )
    return ledger, config


def _precedent_fingerprint(args: argparse.Namespace) -> int:
    claim = (
        Path(args.claim_file).read_text(encoding="utf-8")
        if args.claim_file
        else str(args.claim or "")
    )
    value = finding_fingerprint(
        category=args.category,
        claim=claim,
        path_hashes=tuple(args.path_hash),
    )
    _emit({"finding_fingerprint": value})
    return 0


def _precedent_record(args: argparse.Namespace) -> int:
    ledger, config = _precedent_ledger(args)
    receipt = read_json_object(Path(args.receipt))
    settings = config.section("precedent")
    match = ledger.record_decision(
        fingerprint=args.fingerprint,
        category=args.category,
        disposition=args.disposition,
        source_receipt_hash=str(receipt.get("receipt_hash", "")),
        policy_hash=str(receipt.get("policy_hash", "")),
        ttl_days=args.ttl_days or int(settings.get("default_ttl_days", 180)),
    )
    _emit(match.as_json())
    return 0


def _precedent_outcome(args: argparse.Namespace) -> int:
    ledger, _ = _precedent_ledger(args)
    match = ledger.record_outcome(
        args.record_id,
        outcome=OutcomeVerdict(args.outcome),
        evidence_hash=args.evidence_hash,
    )
    _emit(match.as_json())
    return 0


def _precedent_query(args: argparse.Namespace) -> int:
    ledger, _ = _precedent_ledger(args)
    matches = ledger.query(args.fingerprint, phase=args.phase)
    _emit({"matches": [match.as_json() for match in matches], "count": len(matches)})
    return 0


def _github_report(args: argparse.Namespace) -> int:
    config = _config(args)
    receipt_path = Path(args.receipt)
    analysis_path = Path(args.analysis)
    validate_linked_paths(receipt_path, analysis_path, config)
    payload = build_github_check_report(
        read_json_object(analysis_path),
        read_json_object(receipt_path),
        head_sha=args.head_sha,
    )
    if args.output:
        atomic_write_json(Path(args.output), payload)
    _emit(payload)
    return 0


def _receipt_verify(args: argparse.Namespace) -> int:
    config = _config(args)
    receipt_root = Path(args.repo).resolve() / str(
        config.section("receipts").get("directory", ".fusion/runs")
    )
    chain = ReceiptStore(receipt_root, config).verify_chain()
    _emit({"valid": True, "runs": list(chain), "count": len(chain)})
    return 0


def _policy_sign(args: argparse.Namespace) -> int:
    bundle = read_json_object(Path(args.bundle))
    secret = os.environ.get(args.key_env)
    if secret is None:
        raise PolicyError("policy signing key variable is not set")
    signature = sign_policy_bundle(
        bundle, key_id=args.key_id, signing_key=secret.encode("utf-8")
    )
    payload = signature.as_json()
    if args.output:
        atomic_write_json(Path(args.output), payload)
    _emit(payload)
    return 0


def _policy_verify(args: argparse.Namespace) -> int:
    bundle = read_json_object(Path(args.bundle))
    raw = read_json_object(Path(args.signature))
    signature = PolicyBundleSignature(
        key_id=str(raw["key_id"]),
        bundle_hash=str(raw["bundle_hash"]),
        signature=str(raw["signature"]),
        algorithm=str(raw.get("algorithm", "")),
        version=int(raw.get("version", 0)),
    )
    secret = os.environ.get(args.key_env)
    if secret is None:
        raise PolicyError("policy verification key variable is not set")
    verify_policy_bundle(
        bundle,
        signature,
        {signature.key_id: secret.encode("utf-8")},
        revoked_key_ids=set(args.revoked_key_id),
    )
    _emit({"valid": True, "key_id": signature.key_id, "bundle_hash": signature.bundle_hash})
    return 0


def _eval_summarize(args: argparse.Namespace) -> int:
    parsed: object = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(parsed, list):
        raise ValueError("evaluation input must be an array")
    grouped: dict[ReviewArm, list[EvaluationObservation]] = {arm: [] for arm in ReviewArm}
    for raw in parsed:
        if not isinstance(raw, dict):
            raise ValueError("evaluation observation must be an object")
        arm = ReviewArm(str(raw["arm"]))
        grouped[arm].append(
            EvaluationObservation(
                task_id=str(raw["task_id"]),
                arm=arm,
                input_hash=str(raw["input_hash"]),
                budget_hash=str(raw["budget_hash"]),
                passed=bool(raw["passed"]),
                verified_resolved=bool(raw["verified_resolved"]),
                reported_findings=int(raw["reported_findings"]),
                confirmed_findings=int(raw["confirmed_findings"]),
                expected_critical=int(raw["expected_critical"]),
                confirmed_critical=int(raw["confirmed_critical"]),
                regressions=int(raw["regressions"]),
                latency_ms=int(raw["latency_ms"]),
                cost_usd=float(raw["cost_usd"]),
                used_calls=int(raw.get("used_calls", 0)),
                confirmed_finding_ids=tuple(
                    str(item) for item in raw.get("confirmed_finding_ids", [])
                ),
            )
        )
    metrics = {
        arm.value: asdict(aggregate_metrics(values, k=args.k))
        for arm, values in grouped.items()
        if values
    }
    _emit(metrics)
    return 0


def _common(parser: argparse.ArgumentParser, *, require_repo: bool = True) -> None:
    parser.add_argument("--repo", required=require_repo, default=".")
    parser.add_argument("--config")
    parser.add_argument("--trust-repo-config", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autofusion")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    _common(doctor, require_repo=False)
    doctor.set_defaults(handler=_doctor)
    config_validate = sub.add_parser("config-validate")
    _common(config_validate, require_repo=False)
    config_validate.set_defaults(handler=_config_validate)
    run = sub.add_parser("run")
    _common(run)
    run.add_argument("--task")
    run.add_argument("--task-file")
    run.add_argument(
        "--kind",
        choices=[
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
        ],
        required=True,
    )
    run.add_argument("--path", action="append", default=[])
    run.add_argument("--preset", default="adaptive")
    run.add_argument("--panel")
    run.add_argument("--external-only", action="store_true")
    run.add_argument("--self-model")
    run.add_argument("--self-session-fingerprint-env")
    run.add_argument("--focus")
    run.add_argument("--pack")
    run.add_argument("--no-grounding", action="store_true")
    run.add_argument("--work-root")
    run.set_defaults(handler=_run)
    finalize = sub.add_parser("finalize")
    _common(finalize)
    finalize.add_argument("run_id")
    finalize.add_argument("--dispositions")
    finalize.add_argument("--automatic", action="store_true")
    finalize.add_argument("--proof-capsule", action="append", default=[])
    finalize.add_argument("--work-root")
    finalize.set_defaults(handler=_finalize)
    call = sub.add_parser("call")
    _common(call)
    call.add_argument("handle")
    call.add_argument("--prompt")
    call.add_argument("--prompt-file")
    call.add_argument("--schema", required=True)
    call.add_argument("--timeout", type=float, default=600.0)
    call.add_argument("--max-output-chars", type=int, default=120_000)
    call.add_argument("--work-root")
    call.set_defaults(handler=_call)
    ground = sub.add_parser("ground")
    _common(ground)
    ground.add_argument("verification_id")
    ground.add_argument("--max-output-chars", type=int, default=32_000)
    ground.add_argument("--work-root")
    ground.set_defaults(handler=_ground)
    status = sub.add_parser("run-status")
    _common(status, require_repo=False)
    status.add_argument("run_id")
    status.add_argument("--work-root")
    status.set_defaults(handler=_run_status)
    proof_hash = sub.add_parser("proof-hash")
    proof_hash.add_argument("--root", required=True)
    proof_hash.set_defaults(handler=_proof_hash)
    prove = sub.add_parser("prove")
    _common(prove)
    prove.add_argument("--intent", required=True)
    prove.add_argument("--overlay", required=True)
    prove.add_argument("--revision", action="append", required=True)
    prove.add_argument("--output", required=True)
    prove.add_argument("--work-root")
    prove.set_defaults(handler=_prove)
    packs = sub.add_parser("packs")
    _common(packs, require_repo=False)
    packs.set_defaults(handler=_packs)
    fingerprint = sub.add_parser("precedent-fingerprint")
    fingerprint.add_argument("--category", required=True)
    fingerprint.add_argument("--claim")
    fingerprint.add_argument("--claim-file")
    fingerprint.add_argument("--path-hash", action="append", default=[])
    fingerprint.set_defaults(handler=_precedent_fingerprint)
    precedent_record = sub.add_parser("precedent-record")
    _common(precedent_record)
    precedent_record.add_argument("--repository-id", required=True)
    precedent_record.add_argument("--fingerprint", required=True)
    precedent_record.add_argument("--category", required=True)
    precedent_record.add_argument(
        "--disposition",
        choices=["accepted", "rejected", "deadlock", "resolved", "waived"],
        required=True,
    )
    precedent_record.add_argument("--receipt", required=True)
    precedent_record.add_argument("--ttl-days", type=int)
    precedent_record.set_defaults(handler=_precedent_record)
    precedent_outcome = sub.add_parser("precedent-outcome")
    _common(precedent_outcome)
    precedent_outcome.add_argument("--repository-id", required=True)
    precedent_outcome.add_argument("record_id")
    precedent_outcome.add_argument(
        "--outcome", choices=[item.value for item in OutcomeVerdict], required=True
    )
    precedent_outcome.add_argument("--evidence-hash", required=True)
    precedent_outcome.set_defaults(handler=_precedent_outcome)
    precedent_query = sub.add_parser("precedent-query")
    _common(precedent_query)
    precedent_query.add_argument("--repository-id", required=True)
    precedent_query.add_argument("--fingerprint", required=True)
    precedent_query.add_argument("--phase", default="post-blind-review")
    precedent_query.set_defaults(handler=_precedent_query)
    github_report = sub.add_parser("github-report")
    _common(github_report)
    github_report.add_argument("--analysis", required=True)
    github_report.add_argument("--receipt", required=True)
    github_report.add_argument("--head-sha", required=True)
    github_report.add_argument("--output")
    github_report.set_defaults(handler=_github_report)
    receipt = sub.add_parser("receipt-verify")
    _common(receipt)
    receipt.set_defaults(handler=_receipt_verify)
    sign = sub.add_parser("policy-sign")
    sign.add_argument("--bundle", required=True)
    sign.add_argument("--key-id", required=True)
    sign.add_argument("--key-env", required=True)
    sign.add_argument("--output")
    sign.set_defaults(handler=_policy_sign)
    verify = sub.add_parser("policy-verify")
    verify.add_argument("--bundle", required=True)
    verify.add_argument("--signature", required=True)
    verify.add_argument("--key-env", required=True)
    verify.add_argument("--revoked-key-id", action="append", default=[])
    verify.set_defaults(handler=_policy_verify)
    evaluation = sub.add_parser("eval-summarize")
    evaluation.add_argument("--input", required=True)
    evaluation.add_argument("-k", type=int, default=1)
    evaluation.set_defaults(handler=_eval_summarize)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        handler = args.handler
        if not callable(handler):
            raise ValueError("CLI handler is unavailable")
        return int(handler(args))
    except (AutofusionError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        LOGGER.error("%s", error)
        return 2
