"""Configuration loading with monotonic policy precedence."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from autofusion.errors import ConfigurationError
from autofusion.identity import FABLE_MIGRATION, SessionIdentity, names_fable, reject_fable_routes
from autofusion.models import ModelProfile
from autofusion.util import JsonObject, deep_copy_json, read_json_object

_MAXIMUM_KEYS = {
    "max_panel_size",
    "max_calls_per_run",
    "max_wallclock_s",
    "max_output_chars_per_call",
}
_PROOF_MAXIMUM_KEYS = {
    "max_overlay_files",
    "max_overlay_bytes",
    "max_output_chars",
    "memory_mb",
    "cpus",
    "pids_limit",
}
_ARTIFACT_KINDS = {
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
}
_TOPOLOGIES = {"review", "adversarial-review", "dual-review", "panel-rank", "advisor"}
_ENVIRONMENT_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SIGNING_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_DOCKER_DIGEST_REFERENCE = re.compile(
    r"(?=.{1,256}\Z)[^\s,]+@sha256:[0-9a-f]{64}\Z"
)


def _as_object(value: object, name: str) -> JsonObject:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigurationError(f"{name} must be a JSON object")
    return dict(value)


def _deep_merge(base: JsonObject, overlay: JsonObject) -> JsonObject:
    merged = _as_object(deep_copy_json(base), "base")
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(_as_object(existing, key), _as_object(value, key))
        else:
            merged[key] = deep_copy_json(value)
    return merged


def _merge_guardrails(base: JsonObject, overlay: JsonObject) -> JsonObject:
    merged = _deep_merge(base, overlay)
    base_allow = base.get("model_allowlist")
    overlay_allow = overlay.get("model_allowlist")
    if isinstance(base_allow, list) and isinstance(overlay_allow, list):
        overlay_set = {item for item in overlay_allow if isinstance(item, str)}
        merged["model_allowlist"] = [
            item for item in base_allow if isinstance(item, str) and item in overlay_set
        ]
    base_deny = {item for item in base.get("provider_denylist", []) if isinstance(item, str)}
    overlay_deny = {
        item for item in overlay.get("provider_denylist", []) if isinstance(item, str)
    }
    merged["provider_denylist"] = sorted(base_deny | overlay_deny)
    for key in _MAXIMUM_KEYS:
        left = base.get(key)
        right = overlay.get(key)
        if isinstance(left, int) and isinstance(right, int):
            merged[key] = min(left, right)
    if base.get("fail_closed") is True:
        merged["fail_closed"] = True
    return merged


def merge_layers(base: JsonObject, overlay: JsonObject) -> JsonObject:
    """Merge one approved layer without allowing guardrail weakening."""

    reject_fable_routes(overlay)
    merged = _deep_merge(base, overlay)
    base_guardrails = _as_object(base.get("guardrails", {}), "guardrails")
    overlay_guardrails = _as_object(overlay.get("guardrails", {}), "guardrails")
    merged["guardrails"] = _merge_guardrails(base_guardrails, overlay_guardrails)
    base_routing = _as_object(base.get("routing", {}), "routing")
    overlay_routing = _as_object(overlay.get("routing", {}), "routing")
    merged_routing = _deep_merge(base_routing, overlay_routing)
    base_compound = _as_object(base_routing.get("compound", {}), "routing.compound")
    overlay_compound = _as_object(overlay_routing.get("compound", {}), "routing.compound")
    merged_compound = _deep_merge(base_compound, overlay_compound)
    if isinstance(base_compound.get("max_depth"), int) and isinstance(
        overlay_compound.get("max_depth"), int
    ):
        merged_compound["max_depth"] = min(
            int(base_compound["max_depth"]), int(overlay_compound["max_depth"])
        )
    merged_routing["compound"] = merged_compound
    merged["routing"] = merged_routing
    base_proof = _as_object(base.get("proof", {}), "proof")
    overlay_proof = _as_object(overlay.get("proof", {}), "proof")
    merged_proof = _deep_merge(base_proof, overlay_proof)
    for key in _PROOF_MAXIMUM_KEYS:
        left = base_proof.get(key)
        right = overlay_proof.get(key)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            merged_proof[key] = min(left, right)
    base_patterns = base_proof.get("approved_overlay_patterns")
    overlay_patterns = overlay_proof.get("approved_overlay_patterns")
    if isinstance(base_patterns, list) and isinstance(overlay_patterns, list):
        allowed = {item for item in overlay_patterns if isinstance(item, str)}
        merged_proof["approved_overlay_patterns"] = [
            item for item in base_patterns if isinstance(item, str) and item in allowed
        ]
    if base_proof.get("enabled") is False:
        merged_proof["enabled"] = False
    if base_proof.get("require_mutation") is True:
        merged_proof["require_mutation"] = True
    if base_proof.get("require_independent_test_author") is True:
        merged_proof["require_independent_test_author"] = True
    if base_proof.get("candidate_fix_visible") is False:
        merged_proof["candidate_fix_visible"] = False
    if isinstance(base_proof.get("docker_image"), str):
        merged_proof["docker_image"] = base_proof["docker_image"]
    if base_proof.get("require_signed_capsules") is True:
        merged_proof["require_signed_capsules"] = True
    for key in ("attestation_key_env", "attestation_key_id"):
        if isinstance(base_proof.get(key), str):
            merged_proof[key] = base_proof[key]
    merged_proof["network"] = base_proof.get("network", "deny")
    merged_proof["isolation"] = base_proof.get("isolation", "disposable-docker")
    merged["proof"] = merged_proof
    base_precedent = _as_object(base.get("precedent", {}), "precedent")
    overlay_precedent = _as_object(overlay.get("precedent", {}), "precedent")
    merged_precedent = _deep_merge(base_precedent, overlay_precedent)
    if base_precedent.get("enabled") is False:
        merged_precedent["enabled"] = False
    if base_precedent.get("automatic_authority") is False:
        merged_precedent["automatic_authority"] = False
    if base_precedent.get("automatic_first_pass_injection") is False:
        merged_precedent["automatic_first_pass_injection"] = False
    merged_precedent["retrieval_phase"] = "post-blind-review"
    merged_precedent["privacy"] = "metadata-only"
    merged["precedent"] = merged_precedent
    return merged


@dataclass(frozen=True, slots=True)
class FusionConfig:
    data: JsonObject
    source_paths: tuple[Path, ...]

    def section(self, name: str) -> JsonObject:
        return _as_object(self.data.get(name, {}), name)

    def model(self, handle: str) -> ModelProfile:
        models = self.section("models")
        if handle != "self" and names_fable(handle):
            raise ConfigurationError(FABLE_MIGRATION)
        raw = _as_object(models.get(handle), f"models.{handle}")
        if handle != "self" and names_fable(raw):
            raise ConfigurationError(FABLE_MIGRATION)
        transport = str(raw.get("transport", ""))
        callable_value = raw.get("callable", transport != "self")
        model = str(raw.get("model", handle))
        canonical = str(raw.get("canonical_model", model))
        return ModelProfile(
            handle=handle,
            transport=transport,
            callable=bool(callable_value),
            model=model,
            canonical_model=canonical,
            vendor=str(raw.get("vendor", "unknown")),
            family=str(raw.get("family", "unknown")),
            effort=str(raw.get("effort", "high")),
            context=str(raw.get("context", "packet")),
            compound=bool(raw.get("compound", False)),
            worker_visibility=str(raw.get("worker_visibility", "not-applicable")),
            enabled=bool(raw.get("enabled", True)),
            params=_as_object(raw.get("params", {}), f"models.{handle}.params"),
            capabilities=_as_object(
                raw.get("capabilities", {}), f"models.{handle}.capabilities"
            ),
        )

    def panel(self, name: str) -> JsonObject:
        if names_fable(name):
            raise ConfigurationError(FABLE_MIGRATION)
        return _as_object(self.section("panels").get(name), f"panels.{name}")

    def session_identity(self, model: str) -> SessionIdentity:
        raw = _as_object(self.section("models").get("self"), "models.self")
        if model not in raw.get("allowed_models", []):
            raise ConfigurationError(f"active self model is not allowed: {model}")
        identities = _as_object(raw.get("identities", {}), "self identity metadata")
        identity = identities.get(model)
        if not isinstance(identity, dict) or any(
            not isinstance(identity.get(key), str) or not identity[key].strip()
            or identity[key] == "unknown" for key in ("vendor", "family")
        ):
            raise ConfigurationError(f"self identity metadata is required for {model}")
        return SessionIdentity(vendor=identity["vendor"], family=identity["family"])

    def pack(self, name: str) -> JsonObject:
        return _as_object(self.section("packs").get(name), f"packs.{name}")

    def preset(self, name: str) -> tuple[str, JsonObject]:
        presets = self.section("presets")
        raw = _as_object(presets.get(name), f"presets.{name}")
        seen = {name}
        resolved_name = name
        while "alias_for" in raw:
            resolved_name = str(raw["alias_for"])
            if resolved_name in seen:
                raise ConfigurationError(f"preset alias cycle at {resolved_name}")
            seen.add(resolved_name)
            raw = _as_object(presets.get(resolved_name), f"presets.{resolved_name}")
        return resolved_name, raw


def _default_data() -> JsonObject:
    resource = files("autofusion").joinpath("default_config.json")
    parsed: object = __import__("json").loads(resource.read_text(encoding="utf-8"))
    return _as_object(parsed, "default_config.json")


def validate_config(config: FusionConfig) -> None:
    reject_fable_routes(config.data)
    models = config.section("models")
    panels = config.section("panels")
    presets = config.section("presets")
    guardrails = config.section("guardrails")
    allowlist = {
        item for item in guardrails.get("model_allowlist", []) if isinstance(item, str)
    }
    denylist = {
        item for item in guardrails.get("provider_denylist", []) if isinstance(item, str)
    }
    if "self" not in models:
        raise ConfigurationError("models.self is required")
    self_profile = config.model("self")
    if self_profile.transport != "self" or self_profile.callable:
        raise ConfigurationError("self must be a non-callable self transport")
    raw_self = _as_object(models["self"], "models.self")
    allowed_self = raw_self.get("allowed_models")
    if not isinstance(allowed_self, list) or not allowed_self or not all(
        isinstance(model, str) for model in allowed_self
    ):
        raise ConfigurationError("self allowed_models must name validated session identities")
    for model in allowed_self:
        config.session_identity(model)
    for name in presets:
        config.preset(name)
    for panel_name, panel_value in panels.items():
        panel = _as_object(panel_value, f"panels.{panel_name}")
        panel_enabled = panel.get("enabled", True)
        if not isinstance(panel_enabled, bool):
            raise ConfigurationError(f"panel {panel_name} enabled must be boolean")
        topology = str(panel.get("topology", ""))
        participant_fields = ("drafter", "reviewers", "proposers", "judge")
        participants: list[str] = []
        for field_name in participant_fields:
            value = panel.get(field_name)
            if isinstance(value, str):
                participants.append(value)
            elif isinstance(value, list):
                participants.extend(item for item in value if isinstance(item, str))
        for handle in participants:
            if handle not in models:
                raise ConfigurationError(f"panel {panel_name} references unknown model {handle}")
            profile = config.model(handle)
            if handle != "self" and handle not in allowlist:
                raise ConfigurationError(
                    f"panel {panel_name} uses model outside allowlist: {handle}"
                )
            if profile.vendor in denylist:
                raise ConfigurationError(f"panel {panel_name} uses denied vendor: {profile.vendor}")
            if not profile.enabled and panel_enabled:
                raise ConfigurationError(
                    f"enabled panel {panel_name} uses disabled model: {handle}"
                )
        if topology in {"panel-rank", "parallel"} and "self" in participants:
            raise ConfigurationError(f"fully external panel {panel_name} cannot contain self")
    max_panel_size = guardrails.get("max_panel_size")
    if not isinstance(max_panel_size, int) or max_panel_size < 1:
        raise ConfigurationError("guardrails.max_panel_size must be a positive integer")
    for name in ("max_calls_per_run", "max_wallclock_s", "max_output_chars_per_call"):
        value = guardrails.get(name)
        if not isinstance(value, int) or value < 1:
            raise ConfigurationError(f"guardrails.{name} must be a positive integer")
    if guardrails.get("sensitive_data_action") not in {"flag", "redact", "block"}:
        raise ConfigurationError(
            "guardrails.sensitive_data_action must be flag, redact, or block"
        )
    for pack_name, pack_value in config.section("packs").items():
        pack = _as_object(pack_value, f"packs.{pack_name}")
        artifact_kinds = pack.get("artifact_kinds")
        topologies = pack.get("allowed_topologies")
        roles = pack.get("roles")
        minimum = pack.get("minimum_preset")
        if (
            not isinstance(artifact_kinds, list)
            or not artifact_kinds
            or not all(isinstance(item, str) and item in _ARTIFACT_KINDS for item in artifact_kinds)
        ):
            raise ConfigurationError(f"pack {pack_name} has invalid artifact kinds")
        if (
            not isinstance(topologies, list)
            or not topologies
            or not all(isinstance(item, str) and item in _TOPOLOGIES for item in topologies)
        ):
            raise ConfigurationError(f"pack {pack_name} has invalid topologies")
        if not isinstance(roles, list) or not roles or not all(
            isinstance(item, str) and item for item in roles
        ):
            raise ConfigurationError(f"pack {pack_name} has invalid roles")
        if not isinstance(minimum, str) or minimum not in presets:
            raise ConfigurationError(f"pack {pack_name} references an unknown preset")
        if pack.get("proof_policy") not in {
            "required-for-blocker-major",
            "preferred",
            "not-applicable",
        }:
            raise ConfigurationError(f"pack {pack_name} has an invalid proof policy")
    proof = config.section("proof")
    patterns = proof.get("approved_overlay_patterns")
    if not isinstance(patterns, list) or not patterns or not all(
        isinstance(item, str) and item for item in patterns
    ):
        raise ConfigurationError("proof approved overlay patterns are invalid")
    for name in ("max_overlay_files", "max_overlay_bytes", "max_output_chars"):
        value = proof.get(name)
        if not isinstance(value, int) or value < 1:
            raise ConfigurationError(f"proof.{name} must be a positive integer")
    if proof.get("network") != "deny" or proof.get("isolation") != "disposable-docker":
        raise ConfigurationError("proof execution requires denied network and disposable Docker")
    if proof.get("require_mutation") is not True:
        raise ConfigurationError("proof execution must require mutation testing")
    if proof.get("require_signed_capsules") is not True:
        raise ConfigurationError("proof capsules must require local attestation signatures")
    docker_image = proof.get("docker_image")
    if docker_image is not None and (
        not isinstance(docker_image, str)
        or not _DOCKER_DIGEST_REFERENCE.fullmatch(docker_image)
    ):
        raise ConfigurationError("proof Docker image must be pinned by a SHA-256 digest")
    key_env = proof.get("attestation_key_env")
    key_id = proof.get("attestation_key_id")
    if not isinstance(key_env, str) or not _ENVIRONMENT_KEY.fullmatch(key_env):
        raise ConfigurationError("proof attestation key environment variable is invalid")
    if not isinstance(key_id, str) or not _SIGNING_KEY_ID.fullmatch(key_id):
        raise ConfigurationError("proof attestation key ID is invalid")
    precedent = config.section("precedent")
    precedent_path = Path(str(precedent.get("directory", "")))
    if (
        not str(precedent_path)
        or precedent_path.is_absolute()
        or ".." in precedent_path.parts
    ):
        raise ConfigurationError("precedent directory must remain inside the repository")
    if precedent.get("retrieval_phase") != "post-blind-review":
        raise ConfigurationError("precedent retrieval must remain post blind review")
    if precedent.get("privacy") != "metadata-only":
        raise ConfigurationError("precedent storage must remain metadata only")
    if precedent.get("automatic_authority") is not False:
        raise ConfigurationError("precedent cannot become automatic authority")
    if precedent.get("automatic_first_pass_injection") is not False:
        raise ConfigurationError("precedent cannot enter the blind first pass")


def load_config(
    repo_root: Path | None = None,
    *,
    explicit_path: Path | None = None,
    global_path: Path | None = None,
    trust_repo_config: bool = False,
    overrides: JsonObject | None = None,
) -> FusionConfig:
    """Load built-in, global, approved repository, explicit, and in-memory layers."""

    data = _default_data()
    sources: list[Path] = []
    resolved_global = global_path or Path(
        os.environ.get("AUTOFUSION_GLOBAL_CONFIG", Path.home() / ".fusion" / "config.json")
    )
    if resolved_global.is_file():
        data = merge_layers(data, read_json_object(resolved_global))
        sources.append(resolved_global.resolve())
    if repo_root is not None:
        repository_path = repo_root.resolve() / ".fusion.json"
        if repository_path.is_file():
            if not trust_repo_config:
                raise ConfigurationError(
                    "repository .fusion.json exists but --trust-repo-config was not provided"
                )
            data = merge_layers(data, read_json_object(repository_path))
            sources.append(repository_path)
    if explicit_path is not None:
        if not explicit_path.is_file():
            raise ConfigurationError(f"explicit config does not exist: {explicit_path}")
        data = merge_layers(data, read_json_object(explicit_path))
        sources.append(explicit_path.resolve())
    if overrides:
        data = merge_layers(data, overrides)
    config = FusionConfig(data=data, source_paths=tuple(sources))
    validate_config(config)
    return config
