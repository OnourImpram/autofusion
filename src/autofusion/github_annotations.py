"""Injection-safe GitHub Actions workflow command formatting."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

AnnotationLevel = Literal["notice", "warning", "error"]


def _escape(value: str, *, property_value: bool) -> str:
    escaped = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if property_value:
        escaped = escaped.replace(":", "%3A").replace(",", "%2C")
    return escaped


def format_github_annotation(
    level: AnnotationLevel,
    message: str,
    *,
    title: str | None = None,
    path: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Format one Actions annotation while encoding command-injection delimiters."""

    if level not in {"notice", "warning", "error"}:
        raise ValueError("unsupported GitHub annotation level")
    if start_line is not None and start_line < 1:
        raise ValueError("annotation start_line must be positive")
    if end_line is not None and (end_line < 1 or start_line is None or end_line < start_line):
        raise ValueError("annotation end_line must follow start_line")
    properties: list[tuple[str, str]] = []
    if title is not None:
        properties.append(("title", title))
    if path is not None:
        normalized_path = path.replace("\\", "/")
        parsed_path = PurePosixPath(normalized_path)
        if normalized_path.startswith("/") or ".." in parsed_path.parts:
            raise ValueError("annotation path must be repository relative")
        properties.append(("file", path))
    if start_line is not None:
        properties.append(("line", str(start_line)))
    if end_line is not None:
        properties.append(("endLine", str(end_line)))
    rendered_properties = ""
    if properties:
        rendered_properties = " " + ",".join(
            f"{name}={_escape(value, property_value=True)}" for name, value in properties
        )
    return f"::{level}{rendered_properties}::{_escape(message, property_value=False)}"
