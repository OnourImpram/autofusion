# Architecture

autofusion separates the active agent, adaptive orchestration, callable transports, evidence, and run accounting.

~~~mermaid
flowchart TD
    A[Claude Code skill or external CLI] --> B[Policy and adaptive router]
    B --> C[Context compiler and immutable snapshot]
    C --> D[Scaffold plan]
    D --> E[Callable model providers]
    E --> F[Structured panel analyzer]
    F --> G[Grounding sandbox]
    G --> H[Finding and disposition ledger]
    H --> I[Receipt and final state]
~~~

## Layers

1. Claude Code skills define the operator-facing workflow.
2. The policy layer applies model allowlists, provider restrictions, privacy rules, budgets, and hard risk gates.
3. The adaptive router selects an approved preset and topology.
4. The context compiler freezes the artifact and builds one hash-addressed packet.
5. The scaffold planner assigns bounded roles and records the plan before model dispatch.
6. The provider layer exposes one completion interface for callable profiles.
7. The panel analyzer preserves agreement, disagreement, coverage, unique insight, and blind spots.
8. The grounding sandbox runs only approved verification IDs.
9. The finding ledger records claims, evidence, provenance, dispositions, and unresolved conflicts.
10. The receipt layer records requested and effective execution, including failed and degraded runs.

## The self boundary

The active Claude Code session is not a transport. It is the agent running the workflow.

self may draft, reconcile, and make the final operator-facing recommendation. A Python process cannot invoke the active session as a model endpoint.

This boundary produces two execution models:

1. Claude Code driven mode. self is the drafter and reconciler. Helpers call external reviewers and evidence services.
2. Fully external mode. Every participant is callable, so the engine can run without an active Claude Code session.

A fully external panel containing self is invalid at configuration time.

## Provider profile contract

Each callable profile declares:

1. Handle and effective model.
2. Vendor and model family.
3. Transport.
4. Reasoning or execution mode.
5. Context access mode.
6. Structured output capability.
7. Read-only capability.
8. Compound or single-model status.
9. Worker visibility.
10. Callable status and effective-identity allowlist.
11. Privacy, parameter, region, and fallback constraints.
12. Cost, latency, and throughput metadata when available.

The first implementation targets codex-exec and claude-exec. OpenAI-compatible and Anthropic API transports follow after the review vertical slice.

Provider fallback is safe only when the concrete effective model remains allowed and the resulting panel still satisfies context and independence quorum. Same-model endpoint fallback is the default. Different-model substitution requires explicit policy and cannot bypass a moderation or policy block.

## Compound execution

gpt-sol-ultra, OpenRouter Fusion, and Sakana Fugu may coordinate hidden workers. The provider contract represents each compound system as one participant unless it returns verifiable worker provenance.

A receipt distinguishes:

1. Single model execution.
2. Same-family replication.
3. Cross-model review.
4. Opaque compound execution.
5. Successful fusion.

Compound execution is not automatically cross-model fusion.

Cross-model status is derived from canonical resolved model identities, not handle names. A self-driven receipt records the active Claude session as **self_model**. A successful fused receipt also requires at least one completed call from every required external participant.

## Adaptive orchestration

The initial router is deterministic.

It evaluates:

1. Artifact kind.
2. Reversibility.
3. Verification strength.
4. Sensitive file patterns.
5. Cross-module scope.
6. Transport availability.
7. Context quorum.
8. Latency and cost budgets.

The router outputs:

1. Minimum allowed preset.
2. Selected preset.
3. Topology.
4. Roles.
5. Required model independence.
6. Budget.
7. Hard gates and reasons.
8. Fallback policy.

A future learned router may recommend a scaffold, but policy validation remains deterministic. Learned routing starts in shadow mode and cannot affect production outcomes until calibrated against receipts.

## Trajectory state and communication

A multi-step scaffold records selected models, subtasks, access lists, function-call ownership, and per-step routing decisions. Blind first passes keep tool traces isolated so one worker cannot collapse later workers onto its trajectory.

Inter-workflow memory is not ambient chat history. It is hash-addressed, policy-approved input listed in the next packet manifest. Per-step routing and marginal-value stopping remain deterministic or shadow-only until outcome calibration is complete.

## Context compiler

Reviewers must evaluate the same frozen artifact.

The packet includes:

1. Task.
2. Operator constraints.
3. Artifact.
4. Repository snapshot identity.
5. Relevant files and tests.
6. Applicable repository instructions.
7. Verification registry.
8. Focus role.
9. Packet and content hashes.

Repository access is implemented against an immutable snapshot rather than the live working tree. API-only models receive a packet derived from the same snapshot.

Context limits are evaluated across all required participants. If the complete mandatory packet does not fit every member, the run fails context quorum or selects a policy-approved smaller scaffold.

## Structured panel analyzer

The analyzer receives independent outputs after all required first-pass calls complete.

It does not erase provenance and does not assume agreement is truth. It produces the fusion analysis schema with:

1. Consensus.
2. Contradictions.
3. Partial coverage.
4. Unique insights.
5. Blind spots.
6. Grounding candidates.
7. Executed grounding results.
8. Decision impact.
9. Findings.

For panel ranking, model identities are replaced by random proposal IDs. Pairwise comparisons run in both orders. Order-sensitive outcomes abstain.

## Grounding

A reviewer may name a verification ID. It may not provide executable command text.

The grounding runner resolves the ID from trusted policy, executes its argv array without a shell, captures bounded output, and classifies the result:

1. confirmed
2. not_reproduced
3. inconclusive
4. environment_error
5. timeout
6. policy_blocked

A passing test is weak negative evidence. A failing relevant assertion may strongly confirm a finding. Environment errors do not confirm the claim. Candidate verification and executed grounding are separate records.

## State machine

A run moves through explicit states:

1. prepared
2. routed
3. frozen
4. dispatched
5. analyzed
6. grounded
7. reconciled
8. signed_off
9. escalated
10. degraded
11. failed
12. cancelled

Terminal product verdicts are ship, revise, blocked, degraded, failed, cancelled, and not-run.

## Configuration precedence

Configuration merges in this order:

1. Built-in safe defaults.
2. Global user policy.
3. Repository configuration approved by the operator.
4. Explicit CLI or skill arguments.

A more local layer may select among permitted models and presets. It may not weaken global deny rules, privacy constraints, maximum depth, or execution limits.