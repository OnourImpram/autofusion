---
name: fusion-parallel
description: Use for fully external adaptive panels where every proposer, reviewer, and judge is callable through configured transports.
---

# autofusion parallel

Use this skill when the active Claude Code session should not act as self or when a deterministic external panel is required for batch, CI, comparison, or evaluation.

## Current repository status

The repository is a pre-engine alpha. If the autofusion CLI is unavailable, do not claim a parallel panel ran.

## Invocation

Expected forms:

~~~text
/autofusion:fusion-parallel --panel external-council
/autofusion:fusion-parallel --preset balanced --topology dual-review
/autofusion:fusion-parallel --preset quality --artifact plan.json
~~~

## Operating contract

1. self is invalid in a fully external panel.
2. Every required participant must be callable before dispatch.
3. Freeze one artifact and one context packet.
4. Enforce context quorum across all required participants.
5. Run independent first passes without peer visibility.
6. Use bounded concurrency, calls, time, output, and cost.
7. Apply model and provider allowlists.
8. Treat compound systems as one opaque participant.
9. Prevent nested fusion beyond policy depth.
10. Do not silently replace a missing model.
11. Do not naive-merge proposals.
12. Preserve contradictions and unique findings.
13. Require a receipt for a successful fully external run.

## Workflow

1. Load global and repository configuration.
2. Resolve explicit settings over presets.
3. Validate panel membership, capability, independence, and policy.
4. Freeze the artifact and compile the packet.
5. Verify context quorum.
6. Record the scaffold plan.
7. Dispatch independent participants.
8. Produce the fusion analysis schema.
9. For panel-rank, blind proposal identities and compare in both orders.
10. Abstain when ordering changes the winner.
11. Ground eligible findings through trusted verification IDs.
12. Persist the receipt.
13. Return the final state.

## Output shape

Return:

1. Panel and preset.
2. Topology and role assignment.
3. Requested and effective participants.
4. Model-family and compound status.
5. Context quorum.
6. Consensus, contradictions, coverage, unique insights, and blind spots.
7. Findings and grounding.
8. Budget usage.
9. Missing or failed transports.
10. Receipt path.
11. Final verdict: ship, revise, blocked, degraded, failed, cancelled, or not-run.