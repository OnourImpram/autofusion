---
name: fusion-parallel
description: Use for fully external multi model panels where every participant is callable through configured transports.
---

# autofusion parallel

Use this skill when a task should be sent to a fully external panel rather than driven by the active Claude Code session as `self`.

## Current repository status

This public repository is currently a design and plugin skeleton. If the `autofusion` CLI is unavailable, do not claim that a parallel panel has run.

## Invocation

Expected form:

```text
/autofusion:fusion-parallel --panel default
```

## Operating contract

1. This mode does not use `self` as a callable drafter.
2. Every proposer, reviewer, or aggregator must be available through a configured transport.
3. If any required model transport is missing, degrade honestly and report which handle failed.
4. Prefer structured outputs and receipts.
5. For CI or batch use, require deterministic inputs and saved artifacts.

## Workflow

1. Load `.fusion.json` and global config if available.
2. Resolve the requested panel.
3. Verify that every model handle is callable.
4. Dispatch the task to the configured participants.
5. Aggregate findings or proposals with explicit evidence and uncertainty.
6. Write a receipt if helper tooling is available.
7. Return a verdict and the model handles that materially changed the result.

## Output shape

Return:

1. Panel name.
2. Model handles requested.
3. Model handles actually used.
4. Findings or proposal synthesis.
5. Missing transports.
6. Receipt path if available.
7. Final verdict: ship, revise, blocked, or not-run.
