from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, cast

import validate_analysis_instances as analysis_contract
import validate_contract_instances as receipt_contract
from jsonschema.exceptions import SchemaError

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger("autofusion.linked-run")


class LinkedRunError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise LinkedRunError(message)


def as_object(value: Any, message: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise LinkedRunError(message)
    return cast(dict[str, Any], value)


def as_list(value: Any, message: str) -> list[Any]:
    if not isinstance(value, list):
        raise LinkedRunError(message)
    return value


def as_string(value: Any, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise LinkedRunError(message)
    return value


def validate_linked_run(
    receipt: dict[str, Any],
    analysis: dict[str, Any],
    analysis_path: Path,
) -> None:
    require(receipt.get("run_id") == analysis.get("run_id"), "run_id mismatch")
    require(
        receipt.get("packet_hash") == analysis.get("packet_hash"),
        "packet_hash mismatch",
    )
    require(receipt.get("topology") == analysis.get("topology"), "topology mismatch")
    require(receipt.get("panel") == analysis.get("panel"), "panel mismatch")
    expected_analysis_hash = hashlib.sha256(analysis_path.read_bytes()).hexdigest()
    require(
        receipt.get("analysis_hash") == expected_analysis_hash,
        "receipt analysis_hash does not match analysis artifact bytes",
    )

    requested = set(
        as_string(item, "requested participant must be a string")
        for item in as_list(
            receipt.get("requested_participants"),
            "requested_participants must be an array",
        )
    )
    participants = as_list(analysis.get("participants"), "participants must be an array")
    participants_by_source: dict[str, dict[str, Any]] = {}
    for raw_participant in participants:
        participant = as_object(raw_participant, "participant must be an object")
        source_id = as_string(participant.get("source_id"), "source_id must be a string")
        require(source_id not in participants_by_source, "duplicate participant source_id")
        participants_by_source[source_id] = participant
    require(set(participants_by_source) == requested, "participant set mismatch")

    calls = as_list(receipt.get("calls"), "calls must be an array")
    calls_by_id: dict[str, dict[str, Any]] = {}
    for raw_call in calls:
        call = as_object(raw_call, "call must be an object")
        call_id = as_string(call.get("call_id"), "call_id must be a string")
        require(call_id not in calls_by_id, "duplicate call_id")
        calls_by_id[call_id] = call

    for source_id, participant in participants_by_source.items():
        if source_id == "self":
            require(participant.get("call_id") is None, "self must not link to a call")
            require(
                participant.get("effective_model") == receipt.get("self_model"),
                "self effective model mismatch",
            )
            require(
                participant.get("identity_hash") == receipt.get("self_identity_hash"),
                "self identity hash mismatch",
            )
            continue

        call_id = as_string(
            participant.get("call_id"),
            f"participant {source_id} call_id must be a string",
        )
        require(call_id in calls_by_id, f"participant {source_id} references unknown call")
        call = calls_by_id[call_id]
        require(call.get("handle") == source_id, f"call handle mismatch for {source_id}")
        for field in (
            "effective_model",
            "family",
            "compound",
            "worker_visibility",
            "output_hash",
        ):
            require(
                participant.get(field) == call.get(field),
                f"participant {source_id} {field} does not match receipt call",
            )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    receipt_schema = receipt_contract.load_object(
        ROOT / "schemas" / "fusion-receipt.schema.json"
    )
    analysis_schema = analysis_contract.load_object(
        ROOT / "schemas" / "fusion-analysis.schema.json"
    )
    config = receipt_contract.load_object(ROOT / ".fusion.example.json")
    receipt = receipt_contract.load_object(ROOT / "tests" / "fixtures" / "receipt-valid.json")
    analysis_path = ROOT / "tests" / "fixtures" / "analysis-valid.json"
    analysis = analysis_contract.load_object(analysis_path)
    try:
        receipt_contract.validate_receipt(receipt, receipt_schema, config)
        analysis_contract.validate_analysis(analysis, analysis_schema, config)
        validate_linked_run(receipt, analysis, analysis_path)
    except (
        LinkedRunError,
        receipt_contract.ContractError,
        analysis_contract.AnalysisError,
        SchemaError,
    ) as exc:
        LOGGER.error("%s", exc)
        return 1
    LOGGER.info("linked run provenance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
