"""Conservative structured output parsing with syntax-only normalization."""

from __future__ import annotations

import json
from dataclasses import dataclass

from jsonschema import Draft202012Validator

from autofusion.errors import OutputValidationError
from autofusion.util import JsonObject, sha256_text


@dataclass(frozen=True, slots=True)
class ParsedOutput:
    value: JsonObject
    original_hash: str
    normalized_hash: str
    repaired: bool
    transformations: tuple[str, ...]


def _unwrap_fence(value: str) -> tuple[str, bool]:
    lines = value.splitlines()
    if len(lines) < 3:
        return value, False
    opening = lines[0].strip().lower()
    closing = lines[-1].strip()
    if opening not in {"```", "```json"} or closing != "```":
        return value, False
    return "\n".join(lines[1:-1]), True


def parse_structured_output(
    text: str,
    schema: JsonObject,
    *,
    max_chars: int,
) -> ParsedOutput:
    """Parse JSON after only non-semantic transport-wrapper removal."""

    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if len(text) > max_chars:
        raise OutputValidationError(
            f"structured output has {len(text)} characters, limit is {max_chars}"
        )
    original_hash = sha256_text(text)
    normalized = text
    transformations: list[str] = []
    if normalized.startswith("\ufeff"):
        normalized = normalized.removeprefix("\ufeff")
        transformations.append("utf8-bom")
    stripped = normalized.strip()
    if stripped != normalized:
        normalized = stripped
        transformations.append("surrounding-whitespace")
    unwrapped, changed = _unwrap_fence(normalized)
    if changed:
        normalized = unwrapped
        transformations.append("json-code-fence")
    try:
        parsed: object = json.loads(normalized)
    except json.JSONDecodeError as error:
        raise OutputValidationError(
            f"structured output is not valid JSON at line {error.lineno}, column {error.colno}"
        ) from error
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise OutputValidationError("structured output root must be an object")
    value = dict(parsed)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "<root>"
        raise OutputValidationError(f"schema validation failed at {location}: {first.message}")
    return ParsedOutput(
        value=value,
        original_hash=original_hash,
        normalized_hash=sha256_text(normalized),
        repaired=bool(transformations),
        transformations=tuple(transformations),
    )

