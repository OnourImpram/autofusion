---
name: fusion
description: Use for high stakes Claude Code work that needs cross model review before shipping a plan, answer, or diff.
---

# autofusion

Use this skill when the task is serious enough that a single model review is not enough. This is an escalation workflow, not an autopilot.

## Current repository status

This public repository is currently a design and plugin skeleton. If the Python helper package is not installed or the `autofusion` CLI is unavailable, run the workflow manually and say that helper execution is not available yet.

## Invocation

Expected forms:

```text
/autofusion:fusion plan
/autofusion:fusion diff
/autofusion:fusion answer
```

## Operating contract

1. Treat the active Claude Code session as `self`, the drafter and reconciler.
2. Do not claim Python can call `self`. Python helpers can call external models, validate schemas, run approved grounding commands, and write receipts.
3. Use cross model review only when the error cost justifies the latency.
4. For `plan`, do not run project verification commands unless the user explicitly asks for feasibility checks.
5. For `diff`, prefer real verification commands from `.fusion.json` when available.
6. Never run a reviewer supplied `check` string as shell. It is advisory only.
7. If a reviewer cannot be called, record honest degradation. Do not describe the result as fused.

## Workflow

1. Identify the mode from the user arguments: `plan`, `diff`, or `answer`.
2. Build a compact packet with task, constraints, artifact, relevant files, and verification commands.
3. Call configured reviewers if helper tooling exists.
4. Ask reviewers for structured findings with severity, evidence, suggested fix, and checkability.
5. Ground only checkable findings through approved commands from config.
6. Reconcile each finding as accepted, rejected, or deadlock.
7. Use evidence over model opinion.
8. Escalate unresolved blocker or major deadlocks to the operator.
9. Write or request a receipt when helper tooling is available.

## Output shape

Return:

1. Fusion mode.
2. Reviewers used or unavailable.
3. Accepted findings.
4. Rejected findings with rationale.
5. Grounding evidence.
6. Deadlocks and required operator decisions.
7. Final verdict: ship, revise, or blocked.
