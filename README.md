# autofusion

autofusion is a Claude Code first cross model escalation workflow for serious engineering work. It is designed for situations where the cost of a wrong plan, risky diff, migration, security change, or architectural decision is higher than the extra review latency.

The first public state is intentionally modest. This repository contains the product contract, Claude Code plugin skeleton, configuration examples, and safety model. It does not pretend that the full engine is already finished.

## Core idea

A strong single model still has blind spots. autofusion makes those blind spots visible by combining a working Claude Code session with one or more independent reviewers, usually Codex first, then grounding review findings against real project verification commands when possible.

The default workflow is asymmetric.

1. Claude Code remains the working session and drafter.
2. A reviewer model inspects the task, plan, answer, or diff from outside the drafter context.
3. Checkable findings are grounded only through project approved verification commands.
4. The drafter accepts, rejects, or escalates each finding with evidence.
5. A receipt records what happened, including failures and degraded runs.

## Why this is not just another panel

autofusion is not an autopilot. It is an escalation layer. Routine work should stay single model. Fusion is for work where a missed issue would be expensive.

The cost claim is also bounded. When Claude Code and Codex subscriptions, OAuth, or local entitlements are already available, the common Claude plus Codex path can add zero extra API cost. That is different from saying every model call is universally free.

## Two execution modes

### `/autofusion:fusion`

This is the Claude Code driven mode. The running Claude Code session is the drafter and reconciler. Python helpers may call external reviewers, validate schemas, run grounding commands, and write receipts, but they do not and cannot call the active Claude session as `self`.

Use it for plan fusion, diff fusion, and high stakes answers inside Claude Code.

### `/autofusion:fusion-parallel`

This is the fully external mode. Every participant must be callable through a configured transport such as `codex-exec`, `anthropic`, or `openai-compatible`. This mode is suitable for CI, batch evaluation, and model panels outside an active Claude Code session.

## Install from Claude Code

After the marketplace is registered, install the plugin with Claude Code:

```text
/plugin marketplace add OnourImpram/autofusion
/plugin install autofusion@autofusion
```

Then invoke the skills:

```text
/autofusion:fusion plan
/autofusion:fusion diff
/autofusion:fusion-parallel --panel default
```

## Configuration sketch

See `.fusion.example.json` for a starter configuration. The important rule is that reviewer supplied `check` strings are advisory only. autofusion runs only verification commands that are already declared in trusted config.

## Current status

Status: design and plugin skeleton.

Implemented in this initial repo state:

1. Claude Code marketplace shape.
2. Installable plugin skeleton.
3. Safety and architecture documentation.
4. Example configuration.
5. Roadmap for the first working vertical slice.

Not yet implemented:

1. Python package engine.
2. `codex-exec` transport helper.
3. Grounding runner.
4. Receipt writer.
5. A/B harness.

## Design principles

1. Escalation, not autopilot.
2. Verification before confidence.
3. Receipts over vibes.
4. Model independence by configuration.
5. Honest degradation. If fusion fails, the receipt must say `fused: false`.
6. Reviewer isolation. A reviewer reviews. It does not edit the working tree in review mode.

## Roadmap

The first milestone is a real diff fusion run with Claude Code as drafter, Codex as reviewer, approved test commands as grounding, and a receipt that proves whether the decision changed.

See `docs/roadmap.md` for the phased plan.
