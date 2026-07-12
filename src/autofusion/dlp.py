"""Small fail-closed DLP preflight for external review packets."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from autofusion.errors import PolicyError
from autofusion.models import SnapshotManifest

DlpAction = Literal["flag", "redact", "block"]


@dataclass(frozen=True, slots=True)
class DlpMatch:
    rule: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class DlpResult:
    text: str
    matches: tuple[DlpMatch, ...]
    action: DlpAction


@dataclass(frozen=True, slots=True)
class DlpPolicy:
    action: DlpAction = "redact"
    rules: tuple[tuple[str, str], ...] = (
        ("private-key", r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
        (
            "credential-assignment",
            r"(?i)\b(?:api[_-]?key|token|password|secret)\s*[:=]\s*[\"']?[^\s\"']{6,}",
        ),
        ("bearer-token", r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}"),
        ("openai-key", r"\bsk-[A-Za-z0-9_-]{16,}"),
        ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_-]{16,}"),
        ("github-token", r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
        ("aws-access-key", r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
        (
            "connection-string",
            r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s]+",
        ),
        ("email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    )


@dataclass(frozen=True, slots=True)
class PacketDlpResult:
    payload: dict[str, object]
    matches: tuple[DlpMatch, ...]
    action: DlpAction


@dataclass(frozen=True, slots=True)
class SnapshotDlpReport:
    """Aggregate secret-pattern evidence without retaining matched values."""

    matched_files: int
    rule_counts: dict[str, int]

    @property
    def blocked(self) -> bool:
        return bool(self.rule_counts)


_REPO_SECRET_RULES = tuple(
    rule for rule in DlpPolicy().rules if rule[0] != "email"
)


def preflight_text(text: str, policy: DlpPolicy | None = None) -> DlpResult:
    """Flag, redact, or reject sensitive text before provider dispatch."""

    active_policy = policy or DlpPolicy()
    if active_policy.action not in {"flag", "redact", "block"}:
        raise PolicyError("DLP policy action is invalid")
    matches: list[DlpMatch] = []
    spans: list[tuple[int, int]] = []
    for name, pattern in active_policy.rules:
        try:
            found = list(re.finditer(pattern, text))
        except re.error as exc:
            raise PolicyError(f"invalid DLP pattern for {name}") from exc
        matches.extend(DlpMatch(name, item.start(), item.end()) for item in found)
        spans.extend((item.start(), item.end()) for item in found)
    if spans and active_policy.action == "block":
        raise PolicyError("DLP preflight blocked sensitive packet content")
    if active_policy.action == "flag" or not spans:
        return DlpResult(text=text, matches=tuple(matches), action=active_policy.action)
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    redacted = text
    for start, end in reversed(merged):
        redacted = redacted[:start] + "[REDACTED]" + redacted[end:]
    return DlpResult(text=redacted, matches=tuple(matches), action=active_policy.action)


def preflight_packet(
    payload: Mapping[str, object], policy: DlpPolicy | None = None
) -> PacketDlpResult:
    """Apply DLP recursively without converting structured packet data into prompt text."""

    active_policy = policy or DlpPolicy()
    matches: list[DlpMatch] = []

    def sanitize(value: object) -> object:
        if isinstance(value, str):
            result = preflight_text(value, active_policy)
            matches.extend(result.matches)
            return result.text
        if isinstance(value, Mapping):
            return {str(key): sanitize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        if isinstance(value, tuple):
            return [sanitize(item) for item in value]
        if value is None or isinstance(value, bool | int | float):
            return value
        raise PolicyError("packet includes a non-JSON value")

    sanitized = sanitize(payload)
    if not isinstance(sanitized, dict):
        raise PolicyError("packet must remain a JSON object")
    return PacketDlpResult(
        payload=sanitized, matches=tuple(matches), action=active_policy.action
    )


def scan_snapshot_for_secrets(snapshot: SnapshotManifest) -> SnapshotDlpReport:
    """Scan the full frozen tree before granting a local CLI model repo access.

    Email addresses are packet-level PII and can be redacted. Credential material is
    different because a repo-access subprocess can inspect files outside the selected
    packet. Any credential-pattern match therefore blocks that dispatch path.
    """

    policy = DlpPolicy(action="flag", rules=_REPO_SECRET_RULES)
    rule_counts: dict[str, int] = {}
    matched_files = 0
    for entry in snapshot.entries:
        relative = entry.get("path")
        if not isinstance(relative, str):
            raise PolicyError("snapshot manifest contains an invalid path")
        path = snapshot.root / Path(relative)
        raw = path.read_bytes()
        result = preflight_text(raw.decode("utf-8", errors="replace"), policy)
        if result.matches:
            matched_files += 1
        for match in result.matches:
            rule_counts[match.rule] = rule_counts.get(match.rule, 0) + 1
    return SnapshotDlpReport(matched_files=matched_files, rule_counts=rule_counts)
