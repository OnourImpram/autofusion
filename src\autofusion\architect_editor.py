"""Validate architect-proposed unified diffs without applying them."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import PurePosixPath

from autofusion.errors import PolicyError
from autofusion.util import sha256_text

_FORBIDDEN_MARKERS = (
    "GIT binary patch",
    "Binary files ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
    "similarity index ",
    "dissimilarity index ",
    "deleted file mode ",
    "old mode ",
    "new mode ",
)


@dataclass(frozen=True, slots=True)
class ValidatedDiff:
    diff_hash: str
    paths: tuple[str, ...]
    hunk_count: int
    applied: bool = False


def _normalise_path(raw: str, *, prefix: str) -> str:
    if not raw.startswith(prefix) or "\\" in raw:
        raise PolicyError("diff path must be a forward-slash repository path")
    path = raw.removeprefix(prefix)
    candidate = PurePosixPath(path)
    if (
        not path
        or path.startswith("/")
        or path in {".", ".."}
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise PolicyError("diff path escapes the repository")
    return path


def _approved(path: str, approved_paths: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in approved_paths)


def validate_architect_diff(diff_text: str, *, approved_paths: tuple[str, ...]) -> ValidatedDiff:
    """Accept only textual same-path edits to explicitly approved repository paths."""

    if not diff_text or "\x00" in diff_text:
        raise PolicyError("architect diff must be non-empty text")
    if not approved_paths:
        raise PolicyError("architect diff requires explicit approved paths")
    paths: list[str] = []
    current_path: str | None = None
    current_minus = False
    current_plus = False
    current_hunks = 0
    saw_header = False
    hunk_count = 0

    def finalize_current() -> None:
        if current_path is not None and not (current_minus and current_plus and current_hunks):
            raise PolicyError("architect diff has an incomplete file header or no hunk")

    for line in diff_text.splitlines():
        if line.startswith(_FORBIDDEN_MARKERS):
            raise PolicyError(
                "architect diff contains an unsupported binary, rename, or mode operation"
            )
        if line.startswith("diff --git "):
            finalize_current()
            parts = line.split(" ")
            if len(parts) != 4:
                raise PolicyError("architect diff has an invalid git header")
            left = _normalise_path(parts[2], prefix="a/")
            right = _normalise_path(parts[3], prefix="b/")
            if left != right:
                raise PolicyError("architect diff cannot rename files")
            if not _approved(left, approved_paths):
                raise PolicyError(f"architect diff touches unapproved path: {left}")
            current_path = left
            current_minus = False
            current_plus = False
            current_hunks = 0
            paths.append(left)
            saw_header = True
        elif line.startswith("--- ") or line.startswith("+++ "):
            if current_path is None:
                raise PolicyError("architect diff file header appears before its git header")
            raw_path = line[4:]
            if raw_path == "/dev/null":
                raise PolicyError("architect diff cannot create or delete files")
            expected_prefix = "a/" if line.startswith("--- ") else "b/"
            if _normalise_path(raw_path, prefix=expected_prefix) != current_path:
                raise PolicyError("architect diff file headers do not match")
            if line.startswith("--- "):
                current_minus = True
            else:
                current_plus = True
        elif line.startswith("@@ "):
            if current_path is None or not (current_minus and current_plus):
                raise PolicyError("architect diff hunk appears before complete file headers")
            hunk_count += 1
            current_hunks += 1
    finalize_current()
    if not saw_header or hunk_count == 0:
        raise PolicyError("architect diff must contain at least one textual hunk")
    return ValidatedDiff(
        diff_hash=sha256_text(diff_text),
        paths=tuple(dict.fromkeys(paths)),
        hunk_count=hunk_count,
    )


def prepare_architect_edit(
    diff_text: str, *, approved_paths: tuple[str, ...], apply: bool = False
) -> ValidatedDiff:
    """Validate a proposal. Applying patches is intentionally unsupported in this boundary."""

    if apply:
        raise PolicyError("architect edits require a separate explicit application workflow")
    return validate_architect_diff(diff_text, approved_paths=approved_paths)
