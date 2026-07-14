"""Render a bounded GitHub Checks payload without performing network writes."""

from __future__ import annotations

import re
from collections.abc import Mapping

from autofusion.util import JsonObject

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MARKDOWN_META = "\\`*_{}[]<>()#+-.!|"
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


def _safe_text(value: object, *, limit: int = 1000) -> str:
    text = _CONTROL_RE.sub("", str(value)).replace("\r", " ").replace("\n", " ")
    text = " ".join(text.split())[:limit]
    for character in _MARKDOWN_META:
        text = text.replace(character, f"\\{character}")
    return text


def _findings(analysis: Mapping[str, object]) -> list[Mapping[str, object]]:
    raw = analysis.get("findings")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _conclusion(receipt: Mapping[str, object]) -> str:
    verdict = receipt.get("verdict")
    if verdict == "ship":
        return "success"
    if verdict in {"revise", "blocked"}:
        return "action_required"
    if verdict == "cancelled":
        return "cancelled"
    return "neutral"


def build_github_check_report(
    analysis: Mapping[str, object], receipt: Mapping[str, object], *, head_sha: str
) -> JsonObject:
    """Create a GitHub App ready payload while keeping all actions human gated."""

    if not _GIT_SHA_RE.fullmatch(head_sha):
        raise ValueError("GitHub check head_sha must be a 40 or 64 character Git hash")
    findings = _findings(analysis)
    lines = [
        "## autofusion evidence report",
        "",
        f"Verdict: **{_safe_text(receipt.get('verdict', 'unknown'), limit=32)}**",
        f"Fusion completed: **{str(receipt.get('fused') is True).lower()}**",
        f"Topology: `{_safe_text(receipt.get('topology', 'unknown'), limit=64)}`",
        f"Panel: `{_safe_text(receipt.get('panel', 'unknown'), limit=64)}`",
        "",
        "### Findings",
        "",
    ]
    if not findings:
        lines.append("No findings were reported.")
    for finding in findings[:50]:
        severity = _safe_text(finding.get("severity", "unknown"), limit=16)
        finding_id = _safe_text(finding.get("id", "unknown"), limit=64)
        claim = _safe_text(finding.get("claim", ""), limit=500)
        status = _safe_text(finding.get("status", "unknown"), limit=32)
        lines.append(f"1. **{severity}** `{finding_id}`. {claim} Status: `{status}`.")
    if len(findings) > 50:
        lines.append(f"1. {len(findings) - 50} additional findings were omitted from this view.")
    lines.extend(
        [
            "",
            "### Human authority",
            "",
            "Requested actions create a follow up request only. They never merge, approve, "
            "or apply a patch automatically.",
        ]
    )
    return {
        "name": "autofusion",
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": _conclusion(receipt),
        "output": {
            "title": f"autofusion: {_safe_text(receipt.get('verdict', 'unknown'), limit=32)}",
            "summary": "\n".join(lines),
        },
        "actions": [
            {
                "label": "Prove finding",
                "description": "Request an isolated proof capsule for a selected finding.",
                "identifier": "autofusion-prove",
            },
            {
                "label": "Escalate",
                "description": "Request bounded human adjudication for unresolved evidence.",
                "identifier": "autofusion-escalate",
            },
            {
                "label": "Rerun",
                "description": "Request a new policy-bound fusion run.",
                "identifier": "autofusion-rerun",
            },
        ],
        "autofusion_policy": {
            "automatic_merge": False,
            "automatic_approval": False,
            "raw_prompts_included": False,
        },
    }
