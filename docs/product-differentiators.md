# Product differentiators

autofusion should not compete by running a larger panel. Its defensible product is a proof-oriented control plane for consequential engineering work.

The features below are independently designed. They build on public orchestration ideas, but target gaps that Fugu and OpenRouter do not expose as verifiable engineering contracts.

## 1. Claim and evidence graph

Every material claim becomes a graph node linked to:

1. The participant that made it.
2. The frozen artifact location it cites.
3. The approved verification ID that can test it.
4. The grounding result and output hash.
5. The drafter disposition.
6. The final decision impact.

This makes synthesis auditable at finding level. A judge cannot silently remove a contradiction or convert an unsupported claim into consensus.

**Priority:** P0  
**Current state:** analysis and receipt contracts exist. The persistent graph is an engine milestone.

## 2. Effective independence budget

Panel size is a weak measure because models may share training data, providers, scaffolds, or hidden workers. autofusion will estimate effective independence from:

1. Canonical model identity.
2. Model family and vendor.
3. Compound worker visibility.
4. Historical error correlation by task class.
5. Shared context and prompt lineage.
6. Provider and endpoint concentration.

The router selects a panel against an independence floor, not a raw model count. Same-model sampling remains replication.

**Priority:** P0  
**Current state:** identity, family, compound, and cross-model receipt fields exist. Historical correlation is planned for evaluation.

## 3. Attested identity and fallback invariants

Requested model names are not evidence of execution. Every completed call records the effective model and execution mode. Active self participation additionally requires runtime identity evidence.

Fallback is permitted only when:

1. The triggering failure is policy-approved.
2. The replacement satisfies required parameters and privacy policy.
3. The concrete effective identity is recorded.
4. Independence and context quorum are recalculated.
5. The fallback cannot bypass moderation or a deny decision.

This prevents a resilient transport layer from creating a false fusion claim.

**Priority:** P0  
**Current state:** contract and CI validation exist. Runtime attestation is an engine milestone.

## 4. Context integrity ledger

All participants review one frozen artifact, but they do not need every optional token. The context compiler records:

1. Mandatory packet sections.
2. Optional sections.
3. Per-section hashes.
4. Per-participant inclusion.
5. Any summary or omission.
6. Context-window and message-count limits.
7. The final quorum decision.

Mandatory code, policy, and operator constraints are never silently compressed. Lossy transforms are permitted only for optional material and must be visible in the manifest.

**Priority:** P0  
**Current state:** packet and context quorum contracts exist. The compiler is an engine milestone.

## 5. Asymmetric evidence adjudication

A failed relevant test can strongly confirm a defect. A passing suite often cannot prove the defect absent. autofusion encodes this asymmetry.

Findings change the artifact only when:

1. Execution evidence confirms them.
2. The drafter accepts them with rationale.
3. An authorized human or arbiter approves an otherwise unresolved case.

A reviewer command is always advisory. Only trusted verification IDs resolve to argv arrays.

**Priority:** P0  
**Current state:** schema, safety policy, and grounding result contracts exist.

## 6. Marginal-value stopping

Fixed rounds waste time and money. A future stopping policy estimates the expected value of another call from:

1. Remaining blocker and major uncertainty.
2. New confirmed findings per previous call.
3. Reviewer error correlation.
4. Verification coverage.
5. Remaining time and cost.
6. Whether the next participant adds a new model family or only replication.

The policy initially runs in shadow mode. Hard safety gates and unresolved blockers always override it.

**Priority:** P1  
**Current state:** shadow-only config contract exists.

## 7. Counterfactual router replay

Every adaptive routing decision should be replayable without paying for the same calls again. Recorded provider responses support questions such as:

1. Would fast have reached the same disposition as high?
2. Did a second reviewer add a unique confirmed finding?
3. Would a cheaper model have preserved the decision?
4. Did the judge change because proposal order changed?
5. Was escalation caused by evidence or by routing noise?

This creates a training and audit surface for a future learned router.

**Priority:** P1  
**Current state:** record and replay is on the roadmap.

## 8. Outcome-calibrated capability ledger

Static benchmark rankings are too broad for repository work. autofusion will maintain task-bounded model cards from its own receipts:

1. Finding precision.
2. Unique confirmed gain.
3. Critical miss rate.
4. False blocker rate.
5. Grounding usefulness.
6. Latency and verified cost.
7. Performance by artifact and repository class.
8. Drift after model or prompt changes.

Routing uses these observations only after minimum sample and calibration gates.

**Priority:** P1  
**Current state:** metrics are defined in the evaluation roadmap.

## 9. Explicit communication graph and tool-trace isolation

Multi-agent systems can collapse when later workers inherit the first worker’s path. A scaffold therefore declares:

1. Each subtask.
2. Its assigned model and role.
3. Which prior outputs it may read.
4. Which tools it may use.
5. The owner of every function call.
6. Which memory may cross workflow boundaries.

Blind first passes receive no peer output. Shared memory is hash-addressed and policy-approved.

**Priority:** P1  
**Current state:** config contract exists. Runtime enforcement is an engine milestone.

## 10. Semantic-safe structured-output repair

Syntax repair is useful, but repaired JSON can alter meaning. autofusion therefore:

1. Allows at most one automatic repair.
2. Restricts repair to syntax and wrapper removal.
3. Hashes the original and repaired payload.
4. Revalidates the full schema.
5. Compares protected semantic fields.
6. Fails closed on semantic delta.

This retains the reliability benefit of response healing without hiding model output corruption.

**Priority:** P1  
**Current state:** config contract exists.

## 11. Disagreement packet for humans

Escalation should reduce operator work. A deadlock packet contains:

1. The precise disputed claim.
2. The strongest evidence for each side.
3. What was executed.
4. What remains untestable.
5. Reversibility and blast radius.
6. The smallest decision the operator must make.
7. A default-safe action when no decision is made.

This turns human escalation into bounded adjudication rather than another open review.

**Priority:** P1  
**Current state:** deadlock topology exists. Packet schema is planned.

## 12. Fusion chaos and fault testing

The engine needs adversarial transport tests before production:

1. Model alias resolves to an unexpected identity.
2. A fallback crosses a privacy boundary.
3. One panel member receives stale context.
4. A compound provider hides worker changes.
5. Cost metadata is missing or inconsistent.
6. JSON repair changes a severity field.
7. A verifier returns environment failure.
8. A reviewer attempts command injection.
9. A provider times out after partial output.
10. A judge drops a minority blocker.

A successful test suite must prove honest degradation, not merely exception handling.

**Priority:** P1  
**Current state:** selected negative contract fixtures exist. Full fault injection follows the engine.

## 13. Evaluation claim registry

Public claims should be machine-linked to evidence. Each claim records:

1. Dataset and frozen version.
2. Compared workflows.
3. Model and prompt snapshots.
4. Cost and latency budget.
5. Human adjudication protocol.
6. Confidence interval.
7. Known exclusions.
8. Reproduction command and artifact hashes.

No general superiority claim is published from one benchmark or one judge.

**Priority:** P2  
**Current state:** evaluation principles exist. Registry implementation is planned.

## Build order

1. Finish the review vertical slice with attested identity, immutable packets, trusted grounding, and honest receipts.
2. Add provider fallback invariants and exact usage accounting.
3. Add explicit communication graphs and tool-trace ownership.
4. Add record, replay, and the outcome capability ledger.
5. Run marginal-value and learned routing in shadow mode.
6. Add disagreement packets, chaos testing, and the public claim registry.
7. Promote adaptive routing only after calibration against blind human adjudication.

## Product test

autofusion is differentiated only if it can answer all four questions with evidence:

1. Which independent model actually ran?
2. What unique defect did fusion find?
3. Which part was confirmed by reality rather than model agreement?
4. Was the extra latency and cost justified for this task?

If a run cannot answer those questions, it is orchestration activity, not proven fusion value.