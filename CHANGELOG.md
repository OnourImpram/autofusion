# Changelog

All notable changes to autofusion are recorded here.

## 0.5.0-alpha.1

### Added

1. Mutation-gated proof intents and proof capsules for independently authored tests.
2. Disposable Docker proof runner with denied network, dropped capabilities, read-only
   container root, and explicit resource ceilings.
3. Hash-chained, idempotent run journal plus durable run status inspection.
4. Metadata-only precedent and downstream outcome ledger restricted to post-blind review.
5. Declarative migration, security, release, incident, API contract, dependency, and
   research evidence fusion packs.
6. Human-gated GitHub Checks report renderer.
7. Environment-backed local HMAC attestations for persisted proof capsules.
8. Python 3.11 through 3.14 compatibility coverage in GitHub Actions.

### Changed

1. High-effort adversarial panels now dispatch every declared blind reviewer.
2. Findings confirmed by a valid proof capsule receive the same rejection protection as
   trusted execution-grounded findings.
3. Artifact kinds now include incident, release, API contract, dependency, research
   synthesis, and architecture decision records.
4. Claude CLI calls remove the unsupported meta-schema declaration, disable auxiliary
   prompt suggestions, and retain fail-closed identity checks when provider usage reports
   additional internal models.
5. Codex CLI calls accept successful explicit model routing when current JSONL omits a
   redundant model field, while still rejecting any reported model mismatch.
6. Built-in provider output schemas now use explicit enum types compatible with the
   OpenAI structured-output subset.
7. Claude Fable remains available as an opt-in profile but ships disabled until usage
   credits and a canonical identity smoke prove that Claude CLI did not route to Opus.
8. Default high, adaptive, and external council routes use only live-smoke-verified
   GPT SOL, SOL Ultra, and Claude Opus profiles.
9. Codex identity fallback now requires a successful `turn.completed` event, ambiguous
   Claude multi-model usage fails closed, and Fable-specific panels are disabled with
   the profile until canonical identity is proven.

### Security

1. The weaker host and WSL grounding runners cannot execute generated proof overlays.
2. Proof confirmation requires independent test and patch authors, hidden candidate fixes,
   immutable revision hashes, and a killed mutant.
3. Precedent stores no raw claim or rationale and cannot enter the blind first pass or
   become automatic authority.
4. Proof reconciliation rejects unsigned, modified, wrong-key, and inactive-key capsules.
5. The proof signing secret never enters Docker, model context, or persisted artifacts.
6. Proof runners reject mutable image tags and execute the immutable image ID inspected before use.
7. Failed CLI JSONL error events are promoted to bounded diagnostics without treating
   model text as trusted output.

## 0.4.0-alpha.1

### Added

1. Typed Python package and `autofusion` CLI entry point.
2. Non-callable `self` provider boundary.
3. Codex CLI, Claude CLI, OpenAI-compatible HTTP, Anthropic HTTP, and deterministic fake provider adapters.
4. Immutable snapshot builder, context packet compiler, DLP scan, and context quorum checks.
5. Review, adversarial review, dual review, advisor, and panel-rank orchestration.
6. Pairwise order reversal for panel ranking.
7. Budget ledger, active wall-clock accounting, and failed-provider degradation.
8. Trusted verification IDs, argv-only grounding, and network-denied sandbox detection.
9. Reconciliation, deadlock handling, policy signing, replay, drift snapshots, telemetry stubs, and GitHub annotations.
10. Coverage-gated runtime tests and packaged schema parity checks.

### Changed

1. Updated the repository status from pre-engine alpha to alpha runtime.
2. Kept Claude Code plugin claims skill-first and bounded by helper CLI availability.
3. Kept public value claims gated on live provider smoke tests and external replication.

### Security

1. Repo-access dispatch now blocks when snapshot DLP finds credential patterns.
2. Reviewer-supplied command strings remain advisory only and are never executed.
3. Receipts degrade honestly on DLP, provider, budget, context, and grounding failures.
4. GitHub annotation paths are validated as repository-relative before formatting.

## 0.2.0-alpha.1

### Added

1. GPT 5.6 Sol xhigh and Sol Ultra profile contracts.
2. Claude Opus 4.8 and Claude Fable 5 profile contracts.
3. Fast, balanced, high, quality compatibility, budget, and adaptive presets.
4. Review, adversarial review, dual review, panel rank, and advisor topology contracts.
5. Thinker, Worker, Reviewer, Verifier, Adversary, and Judge role cards.
6. Structured fusion analysis for agreement, contradictions, partial coverage, unique insights, blind spots, grounding candidates, and decision impact.
7. Receipt schema with canonical self identity, effective external models, execution mode, compound status, cost, latency, budgets, and degradation.
8. Context quorum and maximum orchestration depth.
9. Model, provider, prompt-injection, sensitive-data, budget, and web-access guardrails.
10. Competitive research notes for Sakana Fugu, Trinity, Conductor, and OpenRouter Fusion.
11. Repository contract validator and GitHub Actions validation.
12. Positive and adversarial receipt fixtures for state, identity, participant quorum, and budget invariants.
13. Analysis provenance and executed-grounding fixtures.
14. Per-step routing, access-list communication, safe provider fallback, and semantic-safe output repair contracts.
15. Product differentiation roadmap for evidence graphs, independence budgets, replay, calibration, and fault testing.

### Changed

1. Replaced obsolete GPT 5.5 and Claude Opus 4.7 example handles.
2. Clarified that Sol Ultra is a compound mode over GPT 5.6 Sol, not an independent model family.
3. Clarified that opaque hidden workers do not count as cross-model quorum.
4. Expanded the roadmap from a fixed review loop toward bounded adaptive orchestration.
5. Strengthened the capability truth boundary for the pre-engine alpha.

### Security

1. Replaced trusted shell strings with verification IDs and argv arrays in the target configuration.
2. Added fail-closed recursion, context, model, budget, and receipt requirements.
3. Added metadata-only receipt defaults.
4. Added fail-closed canonical model identity and required-participant call checks.
4. Added credential-pattern and repository-hygiene validation.
