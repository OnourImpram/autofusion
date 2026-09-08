"""Provider protocol, response validation, and auditable result construction."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from jsonschema import (
    Draft202012Validator,
    SchemaError,
    ValidationError,
)

from autofusion.errors import NonCallableProviderError, OutputValidationError, ProviderError
from autofusion.models import CallStatus, ModelProfile, ProviderRequest, ProviderResult
from autofusion.util import JsonObject, sha256_json, sha256_text


class Provider(Protocol):
    """A callable transport with no ambient configuration dependency."""

    @property
    def profile(self) -> ModelProfile:
        """Return the immutable profile bound to this transport."""

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        """Perform one bounded provider invocation."""


@dataclass(frozen=True, slots=True)
class CostEvidence:
    """Cost evidence that is explicitly separate from an estimate."""

    cost_usd: float | None = None
    verified: bool = False
    source: str = "unknown"


CostResolver = Callable[[ModelProfile, JsonObject], CostEvidence]


def no_cost_evidence(_: ModelProfile, __: JsonObject) -> CostEvidence:
    """Default policy. A missing invoice is never represented as zero cost."""

    return CostEvidence()


def decode_json_object(value: str, *, source: str) -> JsonObject:
    """Decode one JSON object and reject scalars and arrays at the trust boundary."""

    try:
        parsed: object = json.loads(value)
    except json.JSONDecodeError as exc:
        raise OutputValidationError(f"{source} did not return valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise OutputValidationError(f"{source} must return a JSON object")
    return dict(parsed)


def validate_structured_output(value: JsonObject, schema: JsonObject) -> JsonObject:
    """Validate provider output against the request schema before it is trusted."""

    try:
        validator = Draft202012Validator(schema)
    except SchemaError as exc:
        raise OutputValidationError(f"invalid response schema: {exc.message}") from exc
    try:
        validator.validate(value)
    except ValidationError as exc:
        location = "/".join(str(item) for item in exc.absolute_path) or "<root>"
        message = f"provider output violates schema at {location}: {exc.message}"
        raise OutputValidationError(message) from exc
    return value


def assert_effective_identity(*, expected: str, actual: object) -> str:
    """Require an attested effective model. Requested identity is insufficient."""

    if not isinstance(actual, str) or not actual:
        raise ProviderError("provider response omitted effective model identity")
    if actual != expected:
        raise ProviderError(f"provider identity mismatch: expected {expected}, received {actual}")
    return actual


def usage_evidence(usage: JsonObject | None) -> tuple[int | None, int | None, str | None]:
    """Extract standard token counters and hash the full provider-supplied record."""

    if usage is None:
        return None, None, None
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    if not isinstance(input_tokens, int):
        input_tokens = None
    if not isinstance(output_tokens, int):
        output_tokens = None
    return input_tokens, output_tokens, sha256_json(usage)


def build_result(
    *,
    profile: ModelProfile,
    request: ProviderRequest,
    effective_model: str,
    duration_ms: int,
    output_text: str,
    structured_output: JsonObject,
    usage: JsonObject | None = None,
    cost_resolver: CostResolver = no_cost_evidence,
    stderr_tail: str = "",
    truncated: bool = False,
    identity_evidence: str = "provider-response",
) -> ProviderResult:
    """Construct a result with evidence hashes after validation and identity checks."""

    effective_model = assert_effective_identity(
        expected=profile.canonical_model, actual=effective_model
    )
    if identity_evidence not in {"provider-response", "configured-route"}:
        raise ProviderError(
            "completed result requires provider-response or configured-route evidence"
        )
    observed_model = effective_model if identity_evidence == "provider-response" else None
    validated = validate_structured_output(structured_output, request.response_schema)
    input_tokens, output_tokens, usage_hash = usage_evidence(usage)
    cost = cost_resolver(profile, usage or {})
    if cost.verified and (
        cost.cost_usd is None
        or cost.cost_usd < 0
        or cost.source not in {"provider-usage", "subscription-entitlement"}
    ):
        raise ProviderError("verified cost requires a nonnegative amount and trusted source")
    if not cost.verified and (cost.cost_usd is not None or cost.source != "unknown"):
        raise ProviderError("unverified cost must remain unknown rather than estimated")
    routing_attestation = {
        "handle": profile.handle,
        "requested_model": profile.model,
        "effective_model": effective_model,
        "vendor": profile.vendor,
        "family": profile.family,
        "call_id": request.call_id,
        "configured_model": profile.canonical_model,
        "observed_model": observed_model,
        "identity_evidence": identity_evidence,
        "quota_group": profile.quota_group,
    }
    return ProviderResult(
        call_id=request.call_id,
        handle=profile.handle,
        requested_model=profile.model,
        effective_model=effective_model,
        configured_model=profile.canonical_model,
        observed_model=observed_model,
        identity_evidence=identity_evidence,
        quota_group=profile.quota_group,
        vendor=profile.vendor,
        family=profile.family,
        mode=profile.effort,
        compound=profile.compound,
        worker_visibility=profile.worker_visibility,
        status=CallStatus.COMPLETED,
        duration_ms=duration_ms,
        output_text=output_text,
        structured_output=validated,
        output_hash=sha256_text(output_text),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost.cost_usd,
        cost_verified=cost.verified,
        cost_source=cost.source,
        provider_usage_hash=usage_hash if cost.verified else None,
        routing_attestation_hash=sha256_json(routing_attestation),
        stderr_tail=stderr_tail,
        truncated=truncated,
    )


@dataclass(frozen=True, slots=True)
class SelfProvider:
    """The active-session sentinel. It is addressable but never executable."""

    profile: ModelProfile

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        del request
        raise NonCallableProviderError(
            "the self provider is a non-callable active-session sentinel"
        )
