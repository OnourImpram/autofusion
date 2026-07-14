# Fusion topologies

autofusion composes four independent dimensions:

1. Artifact type.
2. Topology.
3. Role cards.
4. Model and safety policy.

This prevents a combinatorial collection of nearly identical workflows.

## Artifact types

1. plan
2. diff
3. answer
4. migration
5. incident
6. architecture decision record
7. research synthesis
8. release
9. API contract
10. dependency change

Diffs permit execution grounding. Plans and architecture decisions permit repository-fact and constraint grounding, but they cannot claim executable validation before implementation.

## Presets

### fast

Use one independent reviewer, one round, and conditional signoff only for blockers. The selected reviewer should be latency-compatible with the task.

### balanced

Use two blind reviewers from different model families where possible. Preserve separate provenance and run them with bounded concurrency.

### high

Use the strongest allowed profiles, adversarial role cards, full signoff, and stricter human escalation.

### quality

Compatibility alias for high. New configurations should use high.

### budget

Minimize additional paid calls and prefer available subscriptions or OAuth routes. The preset never asserts that calls are universally free.

### adaptive

Select among approved presets using deterministic policy. Inputs include artifact type, reversibility, verification strength, sensitive paths, cross-module scope, transport health, latency budget, and cost budget.

Adaptive routing may escalate automatically. Before version 1 it may not automatically route below a hard gate.

## Topology contracts

### review

1. Freeze one artifact and context packet.
2. Run one independent reviewer.
3. Validate evidence locations.
4. Ground checkable findings.
5. Reconcile every finding.
6. Request signoff only when rejected blocker or major findings remain.

### adversarial-review

1. Freeze one artifact and its claimed invariants.
2. Ask the adversary to disprove the invariants.
3. Require concrete counterexamples, failure preconditions, and impact.
4. Distinguish patchable defects from architectural defects.
5. Require a rewrite mandate only when local fixes would preserve the root problem.

### dual-review

1. Give both reviewers the same packet hash.
2. Hide reviewer identity from the other reviewer.
3. Do not expose first-review output before the second review completes.
4. Preserve unique findings and conflicts.
5. Deduplicate only after provenance is recorded.
6. Do not use majority vote as evidence.

### panel-rank

1. Generate proposals independently.
2. Replace model names with random proposal IDs.
3. Score proposals against one fixed rubric.
4. Compare pairwise in both answer orders.
5. Abstain when order reversal changes the winner.
6. Select one base proposal.
7. Add components from other proposals only through explicit, cited dispositions.
8. Never perform a naive merge.

### advisor

1. Ask one bounded question.
2. Return guidance to the drafter.
3. Record the consultation and its effect.
4. Mark consulted true.
5. Do not mark fused true unless a qualifying fusion topology also completed.

### deadlock challenge

Deadlock handling is not an open debate.

1. The reviewer states one unresolved blocker or major claim.
2. The drafter provides one evidence-bound rebuttal.
3. Grounding runs if an approved verification ID applies.
4. A final unresolved conflict is escalated to the operator.
5. No additional debate round is spawned automatically.

## Adaptive scaffold

The future engine follows this bounded sequence:

1. Freeze the artifact.
2. Classify task risk and verifiability.
3. Resolve hard policy gates.
4. Select a preset and topology.
5. Assign targeted roles.
6. Validate model capability and context quorum.
7. Record the scaffold plan.
8. Declare the subtask access list and function-call ownership.
9. Dispatch isolated first-pass calls.
10. Route later steps only through the approved communication graph.
11. Produce structured panel analysis.
12. Ground eligible findings.
13. Reconcile and sign off.
14. Stop as ship, revise, blocked, degraded, failed, cancelled, or not-run.

The scaffold plan records why each role and model was selected. Model-written routing remains advisory until the deterministic policy validates it.

## Fusion packs

A pack constrains the adaptive scaffold for a recurring artifact class. It declares accepted artifact kinds, the minimum preset, allowed topologies, targeted roles, and the proof policy. Pack selection happens before packet freezing so every reviewer sees the effective role and gate contract.

A pack may escalate a preset. It cannot silently downgrade a hard gate or select a topology outside its allowlist. See [fusion packs](fusion-packs.md).

## Proof stage

Proof execution follows first-pass review and precedes final reconciliation. It is not a topology and does not add a panel vote. A proof intent must be authored independently of the candidate patch, bind immutable revision hashes, remain blind to the candidate fix, use a trusted verification ID, and satisfy a mutation gate under strong disposable isolation.

Only a verified confirmed capsule can force the linked finding to stand. Not-reproduced and inconclusive results remain asymmetric evidence. See [Proof Fusion](proof-fusion.md).

## Roles

### Thinker

Decomposes the task, identifies assumptions, and defines success criteria. It does not approve its own plan.

### Worker

Produces an artifact in fully external modes. It is not used as a hidden editor during review.

### Reviewer

Finds consequential defects and supplies artifact evidence.

### Verifier

Maps a checkable claim to a trusted verification ID. It cannot create executable commands.

### Adversary

Searches for counterexamples, unsafe assumptions, missing rollback paths, and structural failure modes.

### Judge

Analyzes agreement, contradiction, coverage, unique insight, blind spots, and decision impact. It does not erase dissent.

## Analysis contract

The analysis schema includes:

1. consensus
2. contradictions
3. partial_coverage
4. unique_insights
5. blind_spots
6. grounding_candidates
7. grounding_results
8. proof_results
9. decision_impact
10. findings

Consensus records overlap only. Evidence strength is evaluated separately.

## Communication and memory

Each adaptive scaffold declares a directed access list. A participant sees only the frozen packet, its own tool trace, and explicitly approved predecessor outputs. Every function call retains its participant owner.

Current-workflow tool traces remain isolated to preserve independent solution paths. Memory from earlier workflows is shared only when it is hash-addressed, policy-approved, and listed in the context manifest.

Per-step routing may switch workers at critical trajectory points. Before calibration, model-written routing is advisory and deterministic policy remains authoritative.

## Context quorum

A panel is valid only when every required participant receives the same artifact version and all mandatory packet sections fit its supported context.

The context compiler prioritizes:

1. Task and operator constraints.
2. Frozen artifact.
3. Changed code and tests.
4. Direct dependencies and interfaces.
5. Relevant repository instructions.
6. Optional supporting documentation.

If required context is truncated, the receipt records context_complete false and the run cannot silently claim a complete review.

## Provider fallback and output repair

Same-model endpoint fallback may handle rate limits, provider unavailability, and timeouts. A different-model fallback requires explicit policy. Every fallback revalidates effective identity, required parameters, privacy policy, context capacity, and model independence.

Policy blocks, moderation blocks, and unverified identities never trigger fallback. Session stickiness may apply within one run, but not across evaluation runs.

Structured output receives at most one syntax-only repair. The original and repaired payloads are hashed. A semantic-field change fails the run.

## Compound providers

A compound provider may hide multiple internal workers.

1. It is represented as one callable participant.
2. Hidden workers do not become independent votes.
3. Worker provenance is opaque unless the provider returns verifiable identities.
4. Compound depth defaults to one.
5. Compound providers are excluded from panels unless global policy explicitly allowlists the handle and role.
6. gpt-sol-ultra may be allowlisted as reviewer or judge, but its hidden workers still count as one participant.
7. Third-party compound providers remain disabled by default and may be enabled for evaluation, comparison, or explicitly approved workflows.

## Stop rules

A run ends immediately when:

1. A required transport is unavailable.
2. The artifact changes after freezing.
3. Context quorum fails.
4. The configured call, time, or cost budget is exhausted.
5. A provider returns invalid output after bounded repair.
6. Reviewer or verifier isolation is violated.
7. A receipt cannot be persisted.
8. Human escalation is required.

Partial findings remain useful, but the run state must remain degraded, failed, or blocked.
