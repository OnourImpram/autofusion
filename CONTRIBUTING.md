# Contributing

Thank you for looking. The rules below are the ones the maintainer holds himself to; they are what
makes a change reviewable by a model that did not write it, which is how this repository is
developed.

## Before you open a pull request

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy --strict src scripts tests
for v in scripts/validate_*.py; do python "$v"; done
```

All of these run in CI on Python 3.11 through 3.14, on Linux and Windows.

## What a change needs

- **A failing test first.** A repair without a reproduction test is not accepted. Put it beside the
  existing tests under `tests/`.
- **A mutation note.** Revert your repair once with the test kept, run the whole suite, and write in
  the pull request which test fails. A repair whose reversal fails nothing is not measured.
- **Smallest correct change.** No new runtime dependency (`jsonschema` is the only one). If a change
  needs an architectural move, open an issue first and say why.
- **Historical evidence stays historical.** Dated measurements in `CHANGELOG.md` and
  `docs/provider-verification.md` keep their dates and model names; current contracts change.
- **No live credentials anywhere.** Fixtures are synthetic and say so. If you find a leak, report it
  by location and type, never by value (see `SECURITY.md`).
- **Fable stays self-only.** No change may make `claude-fable-5-1` callable as a reviewer, judge or
  delegate target.
- **Conventional Commits**, English, one logical change per commit, staged file by file.

## Adding a provider transport

Read `docs/architecture.md` and the two shipped adapters (`providers/agy.py`, `providers/acp.py`).
A transport ships disabled in the portable defaults until a dated, sandboxed smoke through
autofusion itself is recorded in `docs/provider-verification.md`. Model identity must come from
the provider's own terminal output, never from the requested model name.

## Writing

Documentation is plain English without em or en dashes. Every capability claim states whether it is
implemented and unit-tested, measured live on a date, supported but unverified, or planned.
