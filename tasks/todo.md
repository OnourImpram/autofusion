# Autofusion proof runtime checklist

Date: 2026-07-16
Branch: `fix/v0.5.0-alpha.1-release-gates`

## Remote first constraints

- [x] Keep work out of `C:\Users\onuri\autofusion`.
- [x] Create the implementation branch directly on GitHub.
- [x] Use only an ephemeral archive tree for staging and validation.
- [x] Publish through the GitHub Git Data API without a local git push.

## Phase 1: proof execution

- [x] Add typed verification intent and proof capsule contracts.
- [x] Require network denial and disposable strong isolation for generated tests.
- [x] Validate test overlays against approved paths, file limits, and symlink rules.
- [x] Execute declared base, head, fixed, and mutant relations without shell strings.
- [x] Link confirmed proof capsules to findings without allowing rejection or waiver.
- [x] Require a local HMAC attestation before proof can affect reconciliation.
- [x] Add proof schemas and a `prove` CLI command.

## Phase 2: durable execution state

- [x] Add a hash chained, idempotent run journal.
- [x] Persist every state transition and terminal decision.
- [x] Add a `run-status` command and pending reconciliation resume surface.
- [x] Document that interrupted provider dispatch is not yet automatically resumed.

## Phase 3: compounding product surfaces

- [x] Add metadata-only precedent and downstream outcome records.
- [x] Enforce expiry, repository scope, DLP-safe hashes, and post-blind-review use.
- [x] Add declarative migration, security, release, incident, API contract,
  dependency, and research evidence fusion packs.
- [x] Add a GitHub check report renderer without automatic merge authority.

## Verification

- [x] Run ruff across source, tests, and scripts.
- [x] Run strict mypy across source, tests, and scripts.
- [x] Run full pytest with at least 85 percent branch coverage.
- [x] Validate root and packaged schema parity.
- [x] Build wheel and source distribution, then inspect packaged files.
- [x] Run CLI smoke tests for proof, status, packs, and precedent commands.
- [x] Run adversarial probes for path escape, symlink, weak isolation, mutation
  bypass, journal tampering, replayed idempotency keys, and expired precedent.
- [x] Open a PR, pass CI, merge, and delete the feature branch.
- [x] Remove temporary build, comparison, smoke, and archive state after remote verification.

## Release gates

- [x] A generated test cannot run through the weaker grounding runner.
- [x] Reviewer text never becomes an executable command.
- [x] A proof cannot be confirmed without its declared relation, mutation gate, and local signature.
- [x] A journal cannot accept conflicting payloads for one idempotency key.
- [x] Precedent cannot enter the blind first pass or become authority by itself.
- [x] A degraded run cannot report `fused: true`.
- [x] GPT 5.6 SOL xhigh and SOL Ultra live profile smokes complete with canonical identity.
- [x] Claude Opus 4.8 xhigh live profile smoke completes with canonical usage dominance.
- [x] Claude Fable remains opt-in and disabled because current subscription routing reports Opus 4.8 rather than canonical Fable 5.
- [x] Fable-specific panels remain disabled until usage credits and canonical identity smoke pass.

## Review notes

Release candidate adversarial verification verdict: PASS with documented external re-review latency.

- Ruff passed across source, tests, and scripts.
- Strict mypy passed across 65 source files.
- Pytest passed with 192 tests, one platform-limited skip, and 87.45 percent branch coverage.
- Root, packaged, instance, linked-run, marketplace, and plugin contracts passed.
- Clean wheel and source distribution contents passed inspection. Installed-wheel config, packs, and doctor smoke passed.
- Dependency audit passed with zero known vulnerabilities after excluding the stale venv bootstrap state by upgrading the ephemeral audit environment to pip 26.1.2. A CycloneDX SBOM was generated.
- Adversarial source review found and fixed a Docker image tag time-of-check to time-of-use gap. Proof now requires a full digest and executes the inspected image ID.
- PR #3 merged after all four required CI jobs passed. The post-merge main workflow also passed.
- All 52 changed blobs and all 103 non-task main-tree blobs matched the verified staging bytes.
- Claude Opus independent review found two major identity-evidence weaknesses. Codex fallback now requires a clean `turn.completed` event. Claude multi-model identity now requires canonical output-token dominance.
- Targeted tests and final live smokes closed both major findings. Follow-up Opus disposition reviews timed out or returned a failed provider envelope and were not counted as successful review.
- Codex CLI was updated from 0.128.0 to 0.144.5. GPT SOL xhigh and Ultra completed through the packaged adapter.
- Claude Fable remained fail closed. The CLI returned Opus 4.8 telemetry for both the alias and full Fable model ID, consistent with an unavailable entitlement or provider fallback.
