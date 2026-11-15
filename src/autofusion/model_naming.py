"""Naming rule for model handles: a handle names a family, never a version.

A model family has a name (``sol``, ``terra``, ``luna``, ``astra``, ``opus``) and a version
(``6.1``, ``5-5``). Handles carry the name only, so the handle a panel refers to survives a move
to the next version; the version lives in the profile's ``model`` and ``canonical_model``. One
handle exists per family name, effort and compound flag. Pure functions, no configuration loading.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_SEPARATORS = re.compile(r"[-/]")


def _has_digit(text: str) -> bool:
    return any(character.isdigit() for character in text)


def carries_version(handle: str) -> bool:
    """A digit in a handle is read as a version."""

    return _has_digit(handle)


def family_name(profile: Mapping[str, object]) -> str | None:
    """Return the version-free family name of a profile, or None when it names no model.

    ``gpt-6.1-sol`` gives ``sol`` and ``gemini-4-argon-high`` at effort ``high`` gives ``argon``.
    The canonical identity is preferred over the alias, so ``claude-opus-5-5`` gives ``opus``.
    Tokens that contain a digit are versions; a trailing token equal to the configured effort is
    the effort baked into the identifier.
    """

    identity = profile.get("canonical_model") or profile.get("model")
    if not isinstance(identity, str) or not identity:
        return None
    effort = profile.get("effort")
    tokens = [token for token in _SEPARATORS.split(identity.casefold()) if token]
    tokens = [token for token in tokens if not _has_digit(token)]
    if isinstance(effort, str) and len(tokens) > 1 and tokens[-1] == effort.casefold():
        tokens = tokens[:-1]
    return tokens[-1] if tokens else None


def naming_violations(models: Mapping[str, object]) -> list[str]:
    """List every way the handles of ``models`` break the naming rule; empty means conforming.

    The ``self`` sentinel is not a model profile and is skipped.
    """

    violations: list[str] = []
    seen: dict[tuple[str, str | None, bool], str] = {}
    for handle, raw in models.items():
        if carries_version(handle):
            violations.append(f"handle {handle} carries a version; a handle names a family only")
        if not isinstance(raw, Mapping) or raw.get("transport") == "self":
            continue
        name = family_name(raw)
        if name is None:
            continue
        effort = raw.get("effort")
        key = (name, effort if isinstance(effort, str) else None, bool(raw.get("compound", False)))
        first = seen.setdefault(key, handle)
        if first != handle:
            violations.append(
                f"handles {first} and {handle} both name family {name} at effort {key[1]} "
                f"with compound={key[2]}; one handle per family, effort and compound flag"
            )
    return violations
