# Safety model

autofusion treats model output and repository content as untrusted data. Evidence may override model opinion, but model text never becomes executable authority.

## Trust boundaries

Trusted control inputs are:

1. The operator request.
2. The installed autofusion policy.
3. Explicitly approved global configuration.
4. Approved verification IDs and argv arrays.
5. The orchestrator state machine.

Untrusted inputs are:

1. Repository files and instructions until explicitly accepted.
2. Source comments and documentation.
3. Reviewer outputs.
4. Model-generated tool suggestions.
5. Verifier stdout and stderr.
6. Web content.
7. Compound-provider summaries.
8. Repository-local model, MCP, hook, and plugin configuration.
9. Proof overlays and verification intents.
10. Precedent records until scope, expiry, and integrity checks pass.

## Immutable review input

External reviewers and verifiers should not inspect the live working tree.

The target engine builds a content-addressed snapshot, rejects path escapes, records a manifest, and binds the packet to that snapshot hash. A run becomes stale when the source artifact changes after freezing.

Read-only model flags are defense in depth. They are not the only isolation boundary.

## Command origin

Reviewer findings may reference a verification ID. Reviewer-provided command strings are never executed.

Trusted verification definitions use argv arrays and declare:

1. Working directory.
2. Timeout.
3. Network policy.
4. Resource limits.
5. Expected output class.
6. Applicable file patterns.

The verifier runs untrusted project code in a separate sandbox with network denied by default.

## Prompt injection controls

Prompt injection handling has three policy actions:

1. flag records the signal but preserves input.
2. redact replaces a matched segment before provider dispatch.
3. block stops the call.

Pattern scanning is a guardrail, not a proof of safety. Backend isolation, tool restrictions, egress control, and immutable snapshots remain required.

Repository text is framed as data in provider prompts. It cannot grant new permissions, change model allowlists, enable network access, or modify the verification registry.

## Sensitive data controls

The context compiler scans packets before provider dispatch.

Policies may redact or block:

1. Credentials and tokens.
2. Email addresses and personal identifiers.
3. Private keys.
4. Cloud and database connection strings.
5. Operator-defined project names or restricted paths.

Raw prompts and source content are excluded from receipts by default. Metadata-only receipts are the safe default.

A successful cross-model receipt records the canonical active **self_model** when Claude Code participates. It must also contain a completed, identity-resolved call for every required external participant. Handle aliases alone never prove model diversity.

## Model and provider restrictions

Global policy defines allowed model handles and denied providers. Repository configuration may become more restrictive but cannot weaken the global policy.

Provider restrictions may include:

1. Data-retention requirements.
2. Region requirements.
3. Approved endpoints.
4. Model-family exclusions.
5. Compound-provider exclusions.
6. Maximum context exposure.

Optional Sakana Fugu and OpenRouter Fusion reference profiles are disabled by default. Compound handles require an explicit handle-and-role allowlist before panel use. The built-in gpt-sol-ultra profile may be allowed as reviewer or judge without treating its hidden workers as independent participants.

## Recursion and compound systems

Nested orchestration defaults to maximum depth one.

Panel and judge calls cannot start another fusion unless an explicit policy allows it. Opaque hidden workers:

1. Count as one participant.
2. Do not satisfy model independence.
3. Cannot create hidden verification authority.
4. Must report compound true in the receipt.

## Budgets

Every run has hard limits for:

1. Panel size.
2. Model calls.
3. Wall-clock time.
4. Per-call output.
5. Retry count.
6. Tool calls.
7. Paid cost when the transport reports it.

A budget overrun terminates the run. It never triggers an unapproved cheaper fallback.

## Web access

Web search is disabled for repository review by default.

Research profiles may enable search only with:

1. Explicit operator intent.
2. Source-quality policy.
3. Domain allowlists or denylists.
4. Citation capture.
5. Evaluation-domain exclusions when running benchmarks.

This prevents benchmark rubric leakage and limits unrelated data exposure.

## Reviewer isolation

A reviewer reviews. It does not edit the source artifact.

Before and after manifests verify source immutability. If mutation is detected, the run fails safety validation. autofusion does not automatically revert user changes because concurrent edits may be legitimate.

## Verdict asymmetry

Grounding verdicts do not carry symmetric weight:

1. confirmed is strong positive evidence when the approved check directly supports the claim.
2. not_reproduced is weak negative evidence.
3. inconclusive means no suitable check settled the claim.
4. environment_error says nothing about the claim.
5. policy_blocked records that the requested evidence path was not permitted.

## Generated proof isolation

Generated or externally authored proof tests are a stronger threat class than trusted project verification commands. They cannot run through the ordinary host, Linux unshare, or WSL unshare grounding path.

Proof execution requires denied network, disposable Docker isolation, a read-only container root, dropped capabilities, no-new-privileges, bounded processes, bounded memory, bounded CPU, and a runner attestation hash. The image is operator-selected, unset by default, and accepted only as a full SHA-256 digest reference. The runner executes the inspected immutable image ID, not the mutable configured name. Missing Docker, an unpinned image policy, a weak runner, overlay path escape, symlink, size violation, revision mismatch, or failed mutation gate stops confirmation.

Proof output is bounded and hashed. Persisted capsules must also carry a valid local HMAC attestation from the configured active key. The key is read from `AUTOFUSION_PROOF_ATTESTATION_KEY`, must contain at least 32 bytes, and is never passed into Docker or persisted. Missing, weak, inactive, or incorrect signing material fails closed before a capsule can affect reconciliation.

Local HMAC authenticity does not prove that the external runner identity was trustworthy. It also does not defend against a fully compromised local process. Production runner signing must bind the capsule to trusted runtime evidence, managed key custody, and durable execution logs.

## Precedent safety

Precedent is metadata-only and repository-scoped. It stores hashes, categorical outcomes, timestamps, expiry, and policy-approved dimensions. It does not store raw claims, source excerpts, prompts, or reviewer rationales.

Retrieval is restricted to post-blind-review reconciliation. It cannot bias an independent first pass, automatically settle a finding, or override current executable evidence. Expired records are ignored.

## GitHub authority boundary

The GitHub renderer emits escaped, size-bounded report data. It does not post to GitHub and holds no merge authority. Action labels are requests for a human or separately authorized integration. They are never model-granted capabilities.

## Honest degradation

fused true requires:

1. Every required reviewer completed against the same snapshot.
2. Every output passed schema validation.
3. Context quorum held.
4. Panel analysis completed.
5. Reconciliation completed.
6. The receipt was persisted.
7. The journal terminal event and receipt hash agree when a journal is present.

Partial reviewer output may be shown, but the run remains degraded or failed.

No unavailable model is silently replaced. No failed panel becomes a single-model fused answer.

## Human escalation

A model is not an oracle merely because it is called judge or arbiter.

Unresolved blocker and major findings escalate to the operator when:

1. Grounding is unavailable.
2. Pairwise order changes the result.
3. Reviewers preserve conflicting evidence.
4. Required context is incomplete.
5. A safety or policy gate is disputed.
## Grounding Failure Classification

A grounding result is confirmed only when an approved verifier completes, returns a nonzero result, classifies the failure as an assertion or static diagnostic, and matches the expected failure for the finding. Import errors, environment errors, timeouts, policy blocks, runner crashes, and unrelated failures are inconclusive. A passing verifier is only not-reproduced, never proof that the finding was false.

## Linked Provenance Boundary

The alpha contract links receipt and analysis artifacts by run id, packet hash, panel, topology, participant call ids, output hashes, and the analysis artifact hash. This is structural provenance validation. Runtime authenticity still depends on the future engine hashing trusted runtime evidence from Claude Code and external calls.

## Receipt Verdict Reconciliation

A receipt is not allowed to summarize a different reality than the linked analysis artifact. The linked validator derives blocker, major, minor, confirmed execution, and deadlock counts from the analysis artifact and rejects mismatched receipt summaries. `ship` is invalid when the linked analysis still contains blocker or major findings.

## Trusted Attestation Registry

Self-declared hashes are insufficient. Alpha contract fixtures now require runner, routing, cost, and self identity references to resolve through `tests/fixtures/trusted-attestations.json`. Proof capsules additionally require a verified local HMAC signature. In production these controls must be replaced or extended by trusted runtime evidence captured from Claude Code, verifier execution, provider routing metadata, provider or entitlement usage records, and managed signing infrastructure.
