# Architecture

autofusion separates orchestration from callable helpers.

## Layers

1. Claude Code plugin skills define the operator workflow.
2. The fusion engine coordinates review, panel, and future architect editor topologies.
3. The provider layer exposes one uniform completion interface for callable models.
4. The grounding layer runs only approved project verification commands.
5. The judge layer reconciles structured findings with evidence.
6. The receipt layer records every run, including degraded runs.

## The `self` boundary

The active Claude Code session is not a transport. It is the agent running the workflow. That means `self` can draft, reconcile, and decide what to do next, but a Python process cannot call it as a model endpoint.

This boundary creates two execution modes.

1. Claude Code driven review mode. The active session is the drafter. Helpers call external reviewers and write evidence.
2. Fully external panel mode. All participants are callable through transports, so a CLI can run the whole loop without a live Claude Code session.

## Initial transports

The first target transport is `codex-exec`. It should call Codex non interactively with read only sandboxing, structured output where available, and no persistent session requirement.

Later transports may include Anthropic API, OpenAI compatible endpoints, OpenRouter, local gateways, and organization specific model routers.

## Topologies

### Review

The default topology is cross model review. A drafter produces a plan, answer, or diff. A reviewer returns structured findings. Checkable findings are grounded through approved commands. The drafter reconciles. Unresolved blocker and major deadlocks escalate to the operator.

### Panel

The panel topology asks multiple callable models to propose or review independently. An aggregator synthesizes the result. This mode costs more and is intended for batch, CI, or unusually consequential decisions.

### Architect editor

The architect editor topology is reserved for later work. An architect produces reasoning and constraints. An editor turns that into diffs. This is not part of the first vertical slice.
