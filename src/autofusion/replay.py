"""Content-addressed records for deterministic provider replay."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from autofusion.errors import ReplayError
from autofusion.util import (
    JsonObject,
    atomic_write_json,
    deep_copy_json,
    read_json_object,
    sha256_json,
    sha256_text,
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def _require_hash(name: str, value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ReplayError(f"{name} must be a lowercase SHA-256 hash")
    return value


@dataclass(frozen=True, slots=True)
class ReplayBinding:
    """The immutable inputs that a replay is allowed to stand in for."""

    packet_hash: str
    policy_hash: str
    model_hash: str
    prompt_hash: str
    schema_hash: str

    def __post_init__(self) -> None:
        for name, value in (
            ("packet_hash", self.packet_hash),
            ("policy_hash", self.policy_hash),
            ("model_hash", self.model_hash),
            ("prompt_hash", self.prompt_hash),
            ("schema_hash", self.schema_hash),
        ):
            _require_hash(name, value)

    def as_json(self) -> JsonObject:
        return {
            "packet_hash": self.packet_hash,
            "policy_hash": self.policy_hash,
            "model_hash": self.model_hash,
            "prompt_hash": self.prompt_hash,
            "schema_hash": self.schema_hash,
        }


def bind_replay_inputs(
    *,
    packet_hash: str,
    policy_bundle: Mapping[str, object],
    model_identifier: str,
    prompt: str,
    response_schema: Mapping[str, object],
) -> ReplayBinding:
    """Hash every material input before a response is recorded or replayed."""

    if not model_identifier or not prompt:
        raise ReplayError("model identifier and prompt are required for replay binding")
    return ReplayBinding(
        packet_hash=_require_hash("packet_hash", packet_hash),
        policy_hash=sha256_json(dict(policy_bundle)),
        model_hash=sha256_text(model_identifier),
        prompt_hash=sha256_text(prompt),
        schema_hash=sha256_json(dict(response_schema)),
    )


@dataclass(frozen=True, slots=True)
class ReplayRecord:
    """A self-verifying provider response with no authority outside its binding."""

    binding: ReplayBinding
    output_text: str
    structured_output: JsonObject | None
    provider_metadata: JsonObject
    record_hash: str

    def body(self) -> JsonObject:
        return {
            "version": 1,
            "binding": self.binding.as_json(),
            "output_text": self.output_text,
            "structured_output": deep_copy_json(self.structured_output)
            if self.structured_output is not None
            else None,
            "provider_metadata": deep_copy_json(self.provider_metadata),
        }

    def as_json(self) -> JsonObject:
        return {**self.body(), "record_hash": self.record_hash}


def create_replay_record(
    binding: ReplayBinding,
    *,
    output_text: str,
    structured_output: Mapping[str, object] | None = None,
    provider_metadata: Mapping[str, object] | None = None,
) -> ReplayRecord:
    """Create a content-addressed response record without persisting credentials."""

    record = ReplayRecord(
        binding=binding,
        output_text=output_text,
        structured_output=dict(structured_output) if structured_output is not None else None,
        provider_metadata=dict(provider_metadata or {}),
        record_hash="",
    )
    return replace(record, record_hash=sha256_json(record.body()))


def write_replay_record(path: Path, record: ReplayRecord) -> None:
    """Atomically persist a record after independently rechecking its content hash."""

    if record.record_hash != sha256_json(record.body()):
        raise ReplayError("refusing to persist a replay record with an invalid hash")
    atomic_write_json(path, record.as_json())


def _binding_from_json(value: object) -> ReplayBinding:
    if not isinstance(value, dict):
        raise ReplayError("replay record binding must be an object")
    try:
        return ReplayBinding(
            packet_hash=str(value["packet_hash"]),
            policy_hash=str(value["policy_hash"]),
            model_hash=str(value["model_hash"]),
            prompt_hash=str(value["prompt_hash"]),
            schema_hash=str(value["schema_hash"]),
        )
    except KeyError as exc:
        raise ReplayError("replay record binding is incomplete") from exc


def read_replay_record(path: Path) -> ReplayRecord:
    """Load and verify one content-addressed replay record."""

    try:
        raw = read_json_object(path)
        if raw.get("version") != 1:
            raise ReplayError("unsupported replay record version")
        structured = raw.get("structured_output")
        metadata = raw.get("provider_metadata")
        if structured is not None and not isinstance(structured, dict):
            raise ReplayError("structured replay output must be an object or null")
        if not isinstance(metadata, dict) or not isinstance(raw.get("output_text"), str):
            raise ReplayError("replay record has invalid output fields")
        record = ReplayRecord(
            binding=_binding_from_json(raw.get("binding")),
            output_text=raw["output_text"],
            structured_output=dict(structured) if structured is not None else None,
            provider_metadata=dict(metadata),
            record_hash=str(raw.get("record_hash", "")),
        )
    except (OSError, ValueError) as exc:
        raise ReplayError(f"unable to load replay record: {path}") from exc
    if record.record_hash != sha256_json(record.body()):
        raise ReplayError("replay record content hash verification failed")
    return record


def replay_record(path: Path, expected_binding: ReplayBinding) -> ReplayRecord:
    """Return a replay only when every material execution input matches exactly."""

    record = read_replay_record(path)
    if record.binding != expected_binding:
        raise ReplayError("replay binding does not match this execution")
    return record
