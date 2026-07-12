"""Deterministic provider used by orchestration tests without live model access."""

from __future__ import annotations

from dataclasses import dataclass

from autofusion.models import ModelProfile, ProviderRequest, ProviderResult
from autofusion.providers.base import CostResolver, build_result, no_cost_evidence
from autofusion.util import JsonObject, canonical_json_bytes, sha256_text


@dataclass(slots=True)
class DeterministicFakeProvider:
    """Return fixed schema-valid output and deterministic routing evidence."""

    profile: ModelProfile
    structured_output: JsonObject
    effective_model: str | None = None
    cost_resolver: CostResolver = no_cost_evidence

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        output_text = canonical_json_bytes(self.structured_output).decode("utf-8")
        result = build_result(
            profile=self.profile,
            request=request,
            effective_model=self.effective_model or self.profile.canonical_model,
            duration_ms=0,
            output_text=output_text,
            structured_output=dict(self.structured_output),
            cost_resolver=self.cost_resolver,
        )
        return result

    def fingerprint(self, request: ProviderRequest) -> str:
        """Expose a stable test-only call fingerprint without prompt retention."""

        return sha256_text(f"{request.call_id}:{request.prompt_hash}:{self.profile.handle}")
