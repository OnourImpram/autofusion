"""Configuration loading with monotonic policy precedence."""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from autofusion.errors import ConfigurationError
from autofusion.models import ModelProfile
from autofusion.util import JsonObject, deep_copy_json, read_json_object

_MAXIMUM_KEYS = {
    "max_panel_size",
    "max_calls_per_run",
    "max_wallclock_s",
    "max_output_chars_per_call",
}


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
    return merged


@dataclass(frozen=True, slots=True)
class FusionConfig:
    data: JsonObject
    source_paths: tuple[Path, ...]

    def section(self, name: str) -> JsonObject:
        return _as_object(self.data.get(name, {}), name)

    def model(self, handle: str) -> ModelProfile:
        models = self.section("models")
        raw = _as_object(models.get(handle), f"models.{handle}")
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
        return _as_object(self.section("panels").get(name), f"panels.{name}")

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
    for name in presets:
        config.preset(name)
    for panel_name, panel_value in panels.items():
        panel = _as_object(panel_value, f"panels.{panel_name}")
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
