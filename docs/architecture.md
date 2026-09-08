# Architecture

autofusion separates the active agent, adaptive orchestration, callable transports, evidence, and run accounting.

~~~mermaid
flowchart TD
    A[Claude Code skill or external CLI] --> B[Policy and adaptive router]
    B --> C[Context compiler and immutable snapshot]
    C --> D[Scaffold plan]
    D --> E[Callable model providers]
    E --> F[Structured panel analyzer]
    F --> G[Trusted grounding]
    F --> H[Strong-isolation proof runner]
    G --> I[Finding reconciliation]
    H --> I
    I --> J[Hash-chained run journal]
    J --> K[Receipt and final state]
~~~

## Layers

1. Claude Code skills define the operator-facing workflow.
2. The policy layer applies model allowlists, provider restrictions, privacy rules, budgets, and hard risk gates.
3. The adaptive router selects an approved preset and topology.
4. The context compiler freezes the artifact and builds one hash-addressed packet.
5. The scaffold planner assigns bounded roles and records the plan before model dispatch.
6. The provider layer exposes one completion interface for callable profiles.
7. The panel analyzer preserves agreement, disagreement, coverage, unique insight, and blind spots.
8. The grounding sandbox runs trusted project verification IDs.
9. The proof runner executes independently authored test overlays only in strong disposable isolation.
10. The finding ledger records claims, evidence, provenance, dispositions, and unresolved conflicts.
11. The run journal persists idempotent, hash-chained lifecycle events.
12. The receipt layer records requested and effective execution, including failed and degraded runs.

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

The alpha implementation includes codex-exec, claude-exec, OpenAI-compatible HTTP, Anthropic HTTP, and deterministic fake transports. Live use still depends on provider availability, credentials, effective-model attestation, and the configured policy.

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

Context admission checks the smallest usable capacity across every selected participant, including self, advisors, and panel judges. Each profile declares `context_window_tokens`, `prompt_overhead_tokens`, and `reserved_output_tokens`. Missing or invalid capacity fails closed. Matching packet hashes only establishes packet identity; `context_quorum` also checks fit.

For each request, input accounting includes the actual rendered prompt, canonical response schema, and mandatory selected artifact text that is available through the snapshot but absent from the inline packet. UTF-8 byte length provides a conservative token bound, not a tokenizer-exact measurement. Inline text is counted once. Selected binary artifacts cannot certify complete text context. Required files are never dropped or truncated to fit. The admission bound is the minimum of each participant's window minus its configured overhead and output reserve.

Initial overflow raises `PolicyError` before provider dispatch. Initial panel admission also measures the judge prompt and schema with minimum schema-valid proposal envelopes, so a known fixed judge overflow blocks proposers. Every dispatch batch also recomputes fit, so enlarged pairwise judge prompts are checked after proposals arrive. A blocked judge keeps the completed proposals and records a policy-blocked call; panel ranking abstains. Judges receive the frozen packet along with the proposals.

Portable defaults reserve 32,768 tokens of prompt/system overhead and 128,000 tokens of output capacity. These are policy reservations, not measurements or a new provider generation-token cap. Self uses a conservative 200,000-token admission ceiling across its permitted identities; an operator can configure a smaller available capacity for an occupied session. Sol uses the [documented 1,050,000-token window](https://developers.openai.com/api/docs/models/gpt-5.6-sol), Opus uses its [documented 1,000,000-token window](https://platform.claude.com/docs/en/models/opus-5/whats-new-opus-5), and Astra uses the operator-supplied 1,000,000-token limit from 8 September 2026. Capability configuration remains an explicit trusted input.

This check covers the supplied packet and selected mandatory artifacts. Opaque CLI system context, existing session history, later tool exploration, hidden workers and provider generation beyond the reserved output are not measured by this estimator.

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

## Proof execution

Proof Fusion is separate from ordinary grounding. A verification intent binds one finding, a typed base/head/fixed/control/mutant relation, revision hashes, an approved verification ID, independent authorship, candidate-fix blindness, and a mutation gate.

The proof overlay is validated before execution. It may run only through a runner that attests denied network and strong disposable isolation. The built-in Docker runner requires a full digest reference, resolves its immutable local image ID, and executes that ID directly with a read-only root, dropped capabilities, no-new-privileges, and bounded resources. Linux and WSL unshare runners do not satisfy this stronger contract.

A valid confirmed proof capsule may ground its linked finding. The capsule is content-hashed, signed with a domain-separated local HMAC, verified again during reconciliation, and bound into the evidence ledger. The environment-backed signing secret is not passed to the container or persisted. See [Proof Fusion](proof-fusion.md).

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

Every material transition is also appended to an idempotent hash-chained journal. Reusing an idempotency key with a different payload fails. The status surface can recover pending reconciliation metadata and verify the journal chain.

The alpha journal does not automatically resume a provider call interrupted mid-dispatch. Provider dispatch requires a future durable job and call-attempt protocol so a restart cannot duplicate a paid call or misstate its completion.

## Precedent and outcome memory

Precedent storage contains scoped hashes and outcome metadata, not raw claims, prompts, rationales, or source content. Retrieval happens only after blind first-pass review. A precedent can inform reconciliation, but it cannot enter the initial packet or become automatic authority. Expired and cross-repository records are excluded.

## Fusion packs

Packs are declarative policy overlays for migration, security, release, incident, API contract, dependency, and research evidence work. They select roles, minimum presets, allowed topologies, and proof expectations. Configuration merge rules allow a pack to tighten routing but never weaken global policy. See [fusion packs](fusion-packs.md).

## GitHub reporting

The GitHub report renderer converts bounded analysis data into an escaped Checks API payload. It does not authenticate, post a check, approve a review, merge a pull request, or execute a requested action. Prove, escalate, and rerun remain human-gated commands for a separate integration layer.

## Configuration precedence

Configuration merges in this order:

1. Built-in safe defaults.
2. Global user policy.
3. Repository configuration approved by the operator.
4. Explicit CLI or skill arguments.

A more local layer may select among permitted models and presets. It may not weaken global deny rules, privacy constraints, maximum depth, or execution limits.
## Runtime Policy and Linked Artifacts

Receipts must resolve their preset to a concrete panel and must match that panel's topology and participant set exactly. Analysis artifacts declare the same panel and are validated against the configured panel graph. Receipt and analysis artifacts are linked through run id, packet hash, analysis hash, participant call ids, output hashes, and self identity evidence hashes. This prevents a successful static schema check from masquerading as a valid fused run when the runtime policy or analysis artifact does not match the recorded receipt.

## Cost and Runner Attestation

Per-call cost verification records a cost source and provider usage hash. Confirmed grounding records an invocation hash, runner attestation hash, and runner trust class. These fields are structural contract inputs in the alpha repository. The future engine must bind them to captured runner logs and provider usage records before public claims can move from structural validation to runtime authenticity.

The current alpha uses `tests/fixtures/trusted-attestations.json` as a stand-in trust root for validator fixtures. It is not a production signing system, but it prevents a fixture from passing merely by inventing plausible 64-character hashes.

Proof capsules have an additional runtime gate. `autofusion prove` signs the domain-separated capsule and runner-hash bundle with an operator-controlled local HMAC key. `finalize` requires the configured active key ID and verifies the signature before proof can alter reconciliation. This establishes local signer authenticity only. Managed key rotation, external runner identity, hardware-backed custody, and production execution-log provenance remain 1.0 work.
