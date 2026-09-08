"""Small fail-closed DLP preflight for external review packets."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from autofusion.errors import PolicyError
from autofusion.models import SnapshotManifest

DlpAction = Literal["flag", "redact", "block"]

_CREDENTIAL_KEYS = (
    r"(?:api[_-]?key|(?:access|refresh|auth|bearer)[_-]?token|token|password|secret|"
    r"client[_-]?secret|private[_-]?key|authorization|credentials?)"
)
_CREDENTIAL_ASSIGNMENT = (
    rf"(?i)(?<![\w-])(?:\\*[\"'])?{_CREDENTIAL_KEYS}(?:\\*[\"'])?\s*[:=]\s*"
)
_CREDENTIAL_KEY = re.compile(rf"{_CREDENTIAL_KEYS}\Z", re.IGNORECASE)
_STRING_PREFIX = re.compile(r"(?i)[rubf]{1,2}[\"']")
_JSON_SCALAR = re.compile(
    r"(?:-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null)"
    r"(?=\s*[,}\]]|\s*\Z)"
)
_MARKER_SPACE = r"(?:[ \t\r\n]|\\[rn])"
_PRIVATE_LABEL = rf"(?:(?!(?:BEGIN|END)\b)[A-Z0-9]+{_MARKER_SPACE}+)*?PRIVATE\b"
_PRIVATE_HEADER = (
    rf"(?i)(?<![\w-])-*\bBEGIN{_MARKER_SPACE}+{_PRIVATE_LABEL}"
    rf"(?:{_MARKER_SPACE}+KEY\b[ \t]*-*|(?=-)-+)"
)
_PRIVATE_OPENING = re.compile(_PRIVATE_HEADER)
_PRIVATE_FOOTER = re.compile(
    rf"(?i)(?<![\w-])-*\bEND{_MARKER_SPACE}+{_PRIVATE_LABEL}{_MARKER_SPACE}+KEY\b"
    r"(?:[ \t]*-+|[ \t]*(?=\r|\n|\\[rn]|\Z|[\"']))"
)


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
        ("private-key", _PRIVATE_HEADER),
        ("credential-assignment", _CREDENTIAL_ASSIGNMENT),
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


def _credential_value_end(text: str, start: int) -> int:
    """Consume one quoted, structured or line-delimited credential value.

    Missing quotes or mismatched containers consume the remaining text, so a
    malformed credential cannot expose an unmatched suffix.
    """

    if start == len(text):
        return start
    if text[start] in "|>":
        # A block scalar may span the rest of this field, including malformed indentation.
        return len(text)
    quoted = text[start] in "\"'" or _STRING_PREFIX.match(text, start) is not None
    structured = text[start] in "[{("
    quote: str | None = None
    stack: list[str] = []
    index = start
    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 3 if text[index + 1:index + 3] == "\r\n" else 2
            continue
        if quote is not None:
            if text.startswith(quote, index):
                index += len(quote)
                quote = None
                continue
        elif not stack and (
            character in "\r\n" or ((quoted or structured) and character in ",;]})")
        ):
            return index
        elif character in "\"'":
            quote = character * 3 if text.startswith(character * 3, index) else character
            index += len(quote)
            continue
        elif character in "[{(":
            stack.append(character)
        elif character in "]})" and stack:
            if (stack[-1], character) not in {("[", "]"), ("{", "}"), ("(", ")")}:
                return len(text)
            stack.pop()
        index += 1
    return min(index, len(text))


def preflight_text(text: str, policy: DlpPolicy | None = None) -> DlpResult:
    """Flag, redact, or reject sensitive text before provider dispatch."""

    active_policy = policy or DlpPolicy()
    if active_policy.action not in {"flag", "redact", "block"}:
        raise PolicyError("DLP policy action is invalid")
    matches: list[DlpMatch] = []
    spans: list[tuple[int, int]] = []
    for name, pattern in active_policy.rules:
        try:
            found = re.finditer(pattern, text)
        except re.error as exc:
            raise PolicyError(f"invalid DLP pattern for {name}") from exc
        covered_until = -1
        for item in found:
            if item.start() < covered_until:
                continue
            end = item.end()
            if pattern == _PRIVATE_HEADER:
                footer = _PRIVATE_FOOTER.search(text, end)
                nested = (
                    footer is not None
                    and _PRIVATE_OPENING.search(text, end, footer.start()) is not None
                )
                end = footer.end() if footer is not None and not nested else len(text)
            elif pattern == _CREDENTIAL_ASSIGNMENT:
                prefix = item.group().lstrip("\\")
                if (
                    prefix.startswith(("\"", "'")) and prefix.rstrip().endswith(":")
                    and _JSON_SCALAR.match(text, end) is not None
                ):
                    # Numeric/boolean JSON fields include DLP counters, not credential text.
                    continue
                end = _credential_value_end(text, end)
                value = text[item.end():end].strip().strip("\"'")
                if value in {"", "[REDACTED]"}:
                    continue
            matches.append(DlpMatch(name, item.start(), end))
            spans.append((item.start(), end))
            covered_until = end
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
    payload: Mapping[str, object], policy: DlpPolicy | None = None, *, credential_keys: bool = True
) -> PacketDlpResult:
    """Sanitize JSON data, with explicit key-context exclusion for schema grammar.

    Schema callers disable credential_keys while retaining lexical string DLP.
    An untrusted object's own fields never grant it an exemption.
    """

    active_policy = policy or DlpPolicy()
    preflight_text("", active_policy)
    match_keys = credential_keys and any(
        pattern == _CREDENTIAL_ASSIGNMENT for _, pattern in active_policy.rules
    )
    matches: list[DlpMatch] = []

    def sanitize(value: object) -> object:
        if isinstance(value, str):
            result = preflight_text(value, active_policy)
            matches.extend(result.matches)
            return result.text
        if isinstance(value, Mapping):
            sanitized: dict[str, object] = {}
            for key, item in value.items():
                sanitized_item = sanitize(item)
                if (
                    match_keys and _CREDENTIAL_KEY.fullmatch(str(key))
                    and isinstance(sanitized_item, str | dict | list)
                ):
                    assignment = f"{key}={json.dumps(sanitized_item, ensure_ascii=False)}"
                    checked = preflight_text(assignment, active_policy)
                    matches.extend(checked.matches)
                    if checked.matches and active_policy.action == "redact":
                        sanitized_item = "[REDACTED]"
                sanitized[str(key)] = sanitized_item
            return sanitized
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
