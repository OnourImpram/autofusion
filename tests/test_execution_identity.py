"""Configured routes remain separate from provider-observed identity evidence."""

from __future__ import annotations

import json
import runpy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from autofusion.config import load_config
from autofusion.contracts import validate_receipt
from autofusion.errors import ProviderError, ReceiptError
from autofusion.models import CallStatus, ModelProfile, ProviderRequest
from autofusion.providers.base import build_result
from autofusion.providers.cli import CliTransportProvider, CodexExecAdapter
from autofusion.providers.process import CommandOutcome
from autofusion.util import sha256_json

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_VALIDATOR = runpy.run_path(str(ROOT / "scripts/validate_contract_instances.py"))
IDENTITY_FIELDS = {"configured_model", "observed_model", "identity_evidence", "quota_group"}


def _profile() -> ModelProfile:
    return replace(load_config().model("gpt-sol"), model="sol-alias")


def _request(directory: Path) -> ProviderRequest:
    return ProviderRequest(
        run_id="identity-run", call_id="identity-call", handle="gpt-sol",
        prompt="return an answer", response_schema={"type": "object"},
        working_directory=directory, timeout_s=1, max_output_chars=2048,
    )


@dataclass
class IdentityRunner:
    events: list[dict[str, Any]]
    returncode: int = 0

    def run(self, argv: tuple[str, ...], **kwargs: object) -> CommandOutcome:
        del kwargs
        output_path = Path(argv[argv.index("--output-last-message") + 1])
        output_path.write_text('{"answer":"ok"}', encoding="utf-8")
        return CommandOutcome(
            argv=argv, returncode=self.returncode,
            stdout="\n".join(json.dumps(event) for event in self.events),
            stderr="", duration_ms=1, timed_out=False, truncated=False,
        )


def _receipt() -> dict[str, Any]:
    value: object = json.loads(
        (ROOT / "tests/fixtures/receipt-valid.json").read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return dict(value)


def _metadata(evidence: str = "provider-response") -> dict[str, Any]:
    profile = load_config().model("gpt-sol")
    return {
        "configured_model": profile.canonical_model,
        "observed_model": profile.canonical_model if evidence == "provider-response" else None,
        "identity_evidence": evidence,
        "quota_group": getattr(profile, "quota_group", None),
    }


def test_provider_result_records_observed_identity_and_quota(tmp_path: Path) -> None:
    profile = _profile()
    provider = CliTransportProvider(
        profile, CodexExecAdapter(), IdentityRunner([{"model": profile.canonical_model}])
    )
    result = provider.invoke(_request(tmp_path))
    assert result.requested_model == "sol-alias"
    assert result.configured_model == profile.canonical_model
    assert result.observed_model == profile.canonical_model
    assert result.identity_evidence == "provider-response"
    assert result.quota_group == profile.quota_group
    assert result.routing_attestation_hash == sha256_json({
        "handle": profile.handle, "requested_model": profile.model,
        "effective_model": result.effective_model, "vendor": profile.vendor,
        "family": profile.family, "call_id": result.call_id,
        **_metadata(),
    })


def test_codex_route_fallback_does_not_claim_observation(tmp_path: Path) -> None:
    profile = _profile()
    provider = CliTransportProvider(
        profile, CodexExecAdapter(), IdentityRunner([{"type": "turn.completed", "usage": {}}])
    )
    result = provider.invoke(_request(tmp_path))
    assert result.effective_model == profile.canonical_model
    assert result.configured_model == profile.canonical_model
    assert result.observed_model is None
    assert result.identity_evidence == "configured-route"
    observed = build_result(
        profile=profile, request=_request(tmp_path), effective_model=profile.canonical_model,
        duration_ms=1, output_text=result.output_text, structured_output={"answer": "ok"},
    )
    assert observed.routing_attestation_hash != result.routing_attestation_hash


def test_cli_failure_preserves_configuration_without_observation(tmp_path: Path) -> None:
    profile = _profile()
    provider = CliTransportProvider(profile, CodexExecAdapter(), IdentityRunner([], returncode=1))
    result = provider.invoke(_request(tmp_path))
    assert result.status is CallStatus.FAILED
    assert result.effective_model is None
    assert result.configured_model == profile.canonical_model
    assert result.observed_model is None
    assert result.identity_evidence == "unavailable"
    assert result.quota_group == profile.quota_group


def test_codex_observed_mismatch_is_not_replaced_by_configured_route(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = [
        {"model": "different-model"}, {"type": "turn.completed", "usage": {}}
    ]
    provider = CliTransportProvider(_profile(), CodexExecAdapter(), IdentityRunner(events))
    with pytest.raises(ProviderError, match="identity mismatch"):
        provider.invoke(_request(tmp_path))


@pytest.mark.parametrize(
    "evidence", ["provider-response", "configured-route", "legacy-unspecified"]
)
def test_receipt_accepts_explicit_identity_evidence(
    evidence: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = _receipt()
    metadata = _metadata(evidence)
    if evidence == "legacy-unspecified":
        metadata["configured_model"] = None
    receipt["calls"][0].update(metadata)
    validate_receipt(receipt, load_config())
    _trust_metadata(monkeypatch, receipt, metadata)
    REPOSITORY_VALIDATOR["validate_receipt"](
        receipt,
        json.loads((ROOT / "schemas/fusion-receipt.schema.json").read_text(encoding="utf-8")),
        load_config().data,
    )


def _trust_metadata(
    monkeypatch: pytest.MonkeyPatch, receipt: dict[str, Any], metadata: dict[str, Any]
) -> None:
    validator_globals = REPOSITORY_VALIDATOR["validate_receipt_semantics"].__globals__
    trusted = REPOSITORY_VALIDATOR["load_trusted_attestations"]()
    trusted["routing"][receipt["calls"][0]["routing_attestation_hash"]].update({
        "call_id": receipt["calls"][0]["call_id"], **metadata,
    })
    monkeypatch.setitem(validator_globals, "trusted_section", lambda name: trusted[name])


def test_repository_rejects_observation_claim_absent_from_trusted_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _receipt()
    receipt["calls"][0].update(_metadata())
    _trust_metadata(monkeypatch, receipt, _metadata("configured-route"))
    with pytest.raises(REPOSITORY_VALIDATOR["ContractError"], match="identity evidence"):
        REPOSITORY_VALIDATOR["validate_receipt"](
            receipt,
            json.loads((ROOT / "schemas/fusion-receipt.schema.json").read_text(encoding="utf-8")),
            load_config().data,
        )


def test_repository_rejects_observation_borrowed_from_another_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _receipt()
    receipt["calls"][0].update(_metadata())
    _trust_metadata(monkeypatch, receipt, {"call_id": "other-call", **_metadata()})
    with pytest.raises(REPOSITORY_VALIDATOR["ContractError"], match="identity evidence"):
        REPOSITORY_VALIDATOR["validate_receipt"](
            receipt,
            json.loads((ROOT / "schemas/fusion-receipt.schema.json").read_text(encoding="utf-8")),
            load_config().data,
        )


def test_historical_receipt_without_execution_metadata_remains_valid() -> None:
    receipt = _receipt()
    for field in IDENTITY_FIELDS:
        receipt["calls"][0].pop(field, None)
    validate_receipt(receipt, load_config())


@pytest.mark.parametrize("field,value", [
    ("configured_model", "different-model"),
    ("observed_model", "different-model"),
    ("observed_model", None),
    ("identity_evidence", "unavailable"),
    ("quota_group", "independent-quota"),
])
@pytest.mark.parametrize("consumer", ["runtime", "repository"])
def test_receipt_rejects_misrepresented_identity(field: str, value: object, consumer: str) -> None:
    receipt = _receipt()
    receipt["calls"][0].update(_metadata())
    receipt["calls"][0][field] = value
    if consumer == "runtime":
        with pytest.raises(ReceiptError):
            validate_receipt(receipt, load_config())
    else:
        with pytest.raises(REPOSITORY_VALIDATOR["ContractError"]):
            REPOSITORY_VALIDATOR["validate_call_registry"](
                receipt["calls"][0], load_config().data["models"], {"self", "gpt-sol"}
            )


@pytest.mark.parametrize("field", sorted(IDENTITY_FIELDS))
def test_receipt_requires_identity_fields_together(field: str) -> None:
    receipt = _receipt()
    receipt["calls"][0].update(_metadata())
    del receipt["calls"][0][field]
    for schema_path in [
        ROOT / "schemas/fusion-receipt.schema.json",
        ROOT / "src/autofusion/schemas/fusion-receipt.schema.json",
    ]:
        validator = Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))
        assert not validator.is_valid(receipt)


@pytest.mark.parametrize("evidence", ["configured-route", "legacy-unspecified"])
@pytest.mark.parametrize("consumer", ["runtime", "repository"])
def test_unobserved_receipt_evidence_cannot_claim_observation(evidence: str, consumer: str) -> None:
    receipt = _receipt()
    receipt["calls"][0].update(_metadata())
    receipt["calls"][0]["identity_evidence"] = evidence
    if consumer == "runtime":
        with pytest.raises(ReceiptError):
            validate_receipt(receipt, load_config())
    else:
        with pytest.raises(REPOSITORY_VALIDATOR["ContractError"]):
            REPOSITORY_VALIDATOR["validate_call_registry"](
                receipt["calls"][0], load_config().data["models"], {"self", "gpt-sol"}
            )

