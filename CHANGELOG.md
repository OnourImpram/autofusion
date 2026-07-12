# Changelog

All notable changes to autofusion are recorded here.

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
