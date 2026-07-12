"""Command line interface for the callable autofusion runtime."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

from autofusion.config import FusionConfig, load_config
from autofusion.doctor import inspect_environment
from autofusion.engine import FusionEngine, FusionRunRequest, PendingRun
from autofusion.errors import AutofusionError, PolicyError
from autofusion.evaluation import EvaluationObservation, ReviewArm, aggregate_metrics
from autofusion.grounding import resolve_verification, run_grounding
from autofusion.models import ProviderRequest, RunArtifacts
from autofusion.policy_signing import (
    PolicyBundleSignature,
    sign_policy_bundle,
    verify_policy_bundle,
)
from autofusion.receipt import ReceiptStore
from autofusion.reconcile import FindingDisposition
from autofusion.registry import ProviderRegistry
from autofusion.sandbox import detect_grounding_runner
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
    _emit(inspect_environment(_config(args), grounding_runner=detect_grounding_runner()))
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
        }
    )
    return 0


def _call(args: argparse.Namespace) -> int:
    config = _config(args)
    registry = ProviderRegistry.from_config(config)
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
    run.add_argument("--kind", choices=["plan", "diff", "answer", "migration"], required=True)
    run.add_argument("--path", action="append", default=[])
    run.add_argument("--preset", default="adaptive")
    run.add_argument("--panel")
    run.add_argument("--external-only", action="store_true")
    run.add_argument("--self-model")
    run.add_argument("--self-session-fingerprint-env")
    run.add_argument("--focus")
    run.add_argument("--no-grounding", action="store_true")
    run.add_argument("--work-root")
    run.set_defaults(handler=_run)
    finalize = sub.add_parser("finalize")
    _common(finalize)
    finalize.add_argument("run_id")
    finalize.add_argument("--dispositions")
    finalize.add_argument("--automatic", action="store_true")
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
