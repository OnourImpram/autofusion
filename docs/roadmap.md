# Roadmap

## 0.2 alpha: product contract and plugin surface

1. Replace obsolete model examples with gpt-5.6-sol xhigh and ultra profiles.
2. Add Claude Opus 4.8 and Fable 5 profiles.
3. Add fast, balanced, high, quality compatibility, budget, and adaptive presets.
4. Define review, adversarial-review, dual-review, panel-rank, and advisor contracts.
5. Define structured panel analysis and receipt schemas.
6. Document Fugu and OpenRouter research adoption boundaries.
7. Add repository structure validation.
8. Add attested identity, exact cost, participant provenance, and executed-grounding contract validation.
9. Add per-step routing, access-list communication, safe provider fallback, and semantic-safe output repair contracts.

Exit criteria: the plugin and documentation expose a coherent, truthful target contract and do not claim the engine already exists.

## 0.2 engine: trustworthy review vertical slice

Status: implemented as alpha runtime, except the live Claude Code plus GPT Sol proof remains a release gate.

1. Add the Python package and typed provider protocol.
2. Implement self as a non-callable sentinel.
3. Implement codex-exec for gpt-sol and gpt-sol-ultra.
4. Implement claude-exec for claude-opus and claude-fable.
5. Implement configuration loading and capability negotiation.
6. Implement doctor, config validate, call, ground, and receipt commands.
7. Build immutable repository snapshots and context manifests.
8. Implement review, adversarial-review, and dual-review.
9. Implement trusted verification IDs and a network-denied grounding sandbox.
10. Implement the run state machine, budgets, cancellation, retries, and honest degradation.
11. Persist metadata-only receipts.
12. Run a real Claude Code plus GPT Sol diff fusion.

Exit criteria:

1. self cannot be called from Python.
2. Reviewer text cannot execute commands.
3. Review cannot mutate the source artifact.
4. A failed required reviewer cannot produce fused true.
5. gpt-sol and gpt-sol-ultra record the same effective model and different execution modes.
6. A real finding is confirmed by approved execution evidence.

## 0.3: adaptive and comparative orchestration

Status: core orchestration primitives are implemented as alpha. Live calibration and provider-performance evidence remain pending.

1. Add panel-rank with blind identities and answer-order reversal.
2. Add the advisor consultation mode.
3. Add deterministic adaptive routing.
4. Run a learned router only in shadow mode.
5. Add model-family and compound-provider independence metadata.
6. Add context quorum and budget-aware packet compilation.
7. Add per-reviewer latency, cost, finding precision, and unique confirmed gain.
8. Add record and replay for provider calls.
9. Add base versus head differential grounding.
10. Add optional OpenAI-compatible and Anthropic API transports.
11. Add provider capability negotiation, ZDR and data-policy routing, p90 latency and throughput evidence, and fallback quorum rechecks.
12. Add explicit communication graphs, tool-trace ownership, and hash-addressed role memory.
13. Add syntax-only structured-output repair with semantic delta detection.
14. Add OpenRouter Fusion and Sakana Fugu as disabled comparison profiles.
15. Add tamper-evident receipt chaining and opt-in telemetry.

Exit criteria:

1. Adaptive routing never routes below a hard gate.
2. Hidden compound workers never inflate panel independence.
3. Answer-order-sensitive judges abstain.
4. Replays do not repeat paid calls.
5. The router decision is reproducible from receipt inputs.

## 0.4: evaluation program

Status: replay, evaluation summaries, drift snapshots, and policy signing primitives are implemented. The public benchmark corpus and external replication are pending.

1. Build frozen real-world engineering tasks with hidden outcome graders.
2. Compare solo, same-model review, cross-model review, adversarial review, and compound-provider references.
3. Hold the initial artifact, context, tools, and budgets constant across arms.
4. Measure strict verified resolution, precision, critical misses, new regressions, latency, cost, pass at k, pass to the power of k, and unique confirmed findings.
5. Calibrate judge behavior against blind senior engineer adjudication.
6. Measure reviewer error correlation and effective panel independence.
7. Publish only task-bounded claims.

Exit criteria: cross-model review demonstrates positive verified outcome value beyond ordinary self-review under at least one declared latency and cost budget.

## 0.5 alpha: proof and durable control plane

Status: supplied proof intents, locally HMAC-attested capsules, durable reconciliation state, declarative packs, metadata-only precedent, and bounded GitHub report rendering are implemented. Automatic proof authoring, managed runner signing, and interrupted provider-dispatch resume remain pending.

1. Add typed verification intents and cryptographically self-consistent proof capsules.
2. Add mutation-gated regression, repair, feature, and counterexample relations.
3. Require disposable Docker isolation for generated or externally authored proof overlays.
4. Bind confirmed proof capsules to findings and prevent unsupported rejection or waiver.
5. Add an idempotent hash-chained run journal and status surface.
6. Add metadata-only, repository-scoped precedent and downstream outcome records.
7. Restrict precedent retrieval to post-blind-review reconciliation.
8. Add migration, security, release, incident, API contract, dependency, and research evidence packs.
9. Add an escaped GitHub Checks payload renderer with no posting or merge authority.
10. Require an environment-backed local HMAC before a capsule can affect reconciliation.

Exit criteria:

1. Weak grounding runners cannot execute proof overlays.
2. Revision, authorship, overlay, and mutation violations fail closed.
3. Journal tampering and conflicting idempotency keys are detected.
4. Precedent cannot enter blind first-pass context or become automatic authority.
5. Packs cannot weaken global policy.
6. GitHub reporting cannot grant itself execution or merge authority.
7. Unsigned, modified, wrong-key, and inactive-key proof capsules fail closed.

## 1.0: production hardening

1. Add a calibrated, correlation-aware adaptive router.
2. Add marginal-value stopping after a successful shadow evaluation.
3. Harden disposable executors with pinned images, externally verifiable runner attestations, managed signing keys, rotation, and platform-specific isolation.
4. Add capability-brokered credentials.
5. Add signed policy bundles and release provenance.
6. Add SBOM and dependency review.
7. Add an authenticated least-privilege GitHub Checks integration around the existing renderer.
8. Add drift monitoring for models, prompts, judges, and provider capabilities.
9. Add architect-editor only after a separate mutation-safety proof.
10. Add external replication for the main product claim.
11. Add durable provider call attempts and crash-safe dispatch resume without duplicate paid calls.
12. Add independently evaluated model-driven proof-test authoring.

Exit criteria: the engine, plugin, schemas, security controls, and evaluation evidence support a stable release without relying on hidden or unverifiable behavior.

## Release gates

Every release requires:

1. JSON and plugin structure validation.
2. Ruff and strict mypy for Python.
3. At least 85 percent coverage for business logic.
4. Fake-provider failure tests.
5. Prompt injection, path escape, stale input, timeout, output bomb, and fallback tests.
6. Real smoke tests for every advertised built-in profile.
7. Documentation claim review.
8. No release tag when advertised models cannot be exercised in the supported environment.
