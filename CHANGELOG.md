# Changelog

All notable changes to autofusion are recorded here.

## Unreleased

The stage label "alpha" is removed from the description of the current runtime: the README Limits
section, the project page, the plugin and marketplace descriptions, the two skill notes and four
documents. The `Development Status :: 3 - Alpha` classifier is dropped from `pyproject.toml`. The
limits themselves are unchanged, and the repository validator now requires the assurance sentence
itself where it used to require the label. Schema identifiers (`0.2-alpha`) and the names of past
releases keep their spelling.

The plugin ships a square listing icon at `plugins/autofusion/.claude-plugin/icon.png` (1024 by 1024
pixels), drawn from the banner: three independent reviewers meeting in one node.

The README gains a "Data and privacy" section that states what leaves the machine and where, from
`SECURITY.md`, the safety model and the telemetry module. `plugin.json` names the project page as
homepage and documentation, the issue tracker as support and that section as the privacy statement.

## 1.0.0

Released 15 November 2026. The work was prepared as 0.7.0 and is released as 1.0.0, the first major
version. This release moves the
built-in profiles from the previous model generation to the current one, adds five
profiles, and makes the rule that a handle names a model family and never a version a tested
invariant. The configuration was moved without contacting a provider; the live smokes were
run afterwards, on 15 November 2026, one bounded call per profile
(`docs/provider-verification.md`, section "2026-11-15 model generation"). Every enabled
advertised profile has a dated smoke. Every repair below carries a mutation
note: the repair was reverted once with its tests kept, and the failures named are the
reproduction tests.

### Changed

1. Moved profiles. `gpt-sol` and `gpt-sol-ultra` pin `gpt-6.1-sol` (family `gpt-6.1`),
   `claude-opus` keeps the `opus` alias with canonical model `claude-opus-5-5`, and `grok`
   pins `grok-4.7`. Handles, panels, presets and packs are unchanged and follow the moved ids.
   The July and September measurements in `docs/provider-verification.md` keep their dates and
   the model names they measured; they are not evidence for the new ids.
   Mutation: 57 failed with the shipped configuration and the example reverted to the previous
   generation (the new profile tests, the fixture-bound contract tests, the Astra quota test and
   the identity tests among them).
2. Self identities. `self` admits Opus 5.5, Sonnet 5.5, Haiku 5.5, Fable 5.2 and Mythos 5.2 in
   addition to the four identities of 0.6.0, each with explicit vendor and family metadata.
3. Mythos is self-only, exactly like Fable. One helper, `names_self_only` in `identity.py`
   (it replaces `names_fable`), now covers both families at configuration load, admission, the
   HTTP boundary and the `agy` and ACP adapters, and the migration message names both. The two
   adapters used to match Fable by prefix and now match by substring like the rest of the policy.
   Mutation: 41 failed with Mythos removed from the helper; 7 failed (3 ACP, 4 agy) with only
   the two adapters reverted to their 0.6.0 checks.
4. Shared quota. The `openai-chatgpt` group lists `gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-luna`
   and `gpt-6-astra`. Its recorded source is an operator statement that four ids answered on one
   account on 2026-11-15, not a smoke record of this repository.
5. Context windows. The windows of `gpt-sol`, `gpt-sol-ultra`, `claude-opus` and the new OpenAI
   profiles are carried from the profile each one derives from, and `context_window_source`
   says `carried from <previous id> on 2026-11-15; NOT_VERIFIED for <new id>` instead of citing
   a page that was not opened.
6. Fixtures. The synthetic receipt, analysis and trusted-attestation fixtures moved to the new
   ids. The self-identity hash and the analysis hash were recomputed by the documented method,
   after that method had reproduced both old hashes.
7. Version agreement. `scripts/validate_repository.py` now also requires `pyproject.toml` and
   `src/autofusion/__init__.py` to carry the same version as the marketplace and plugin
   manifests. Mutation: 2 failed with the check reverted (the pyproject and package arms; the
   marketplace and plugin arms were already covered by the earlier check).
8. `agy` session identity and Gemini profiles. `agy` 1.5.9 in print mode with `--output-format
   stream-json` emits `init`, `step_update` and one `result` event with a matching
   `conversation_id` and no model field on `result`. `src/autofusion/providers/agy.py` now
   accepts `init.model` as observed identity (`provider-response` evidence class) only when one
   `init` event and the terminal `result` share a non-empty string `conversation_id` and
   `init.model` is a non-empty string, comparing it with the profile's canonical model as
   before. It refuses missing `init`, `init` without a model, `init` or `result` without
   `conversation_id`, different `conversation_id` values, multiple `init` events, non-string or
   empty `init.model` or `conversation_id`, or a terminal `result` naming a model different from
   the session model; a terminal `result` naming the model itself still identifies the call.
   `gemini-flash`, `gemini-argon`, `gemini-carbon` and `gemini-hydrogen` are enabled with no
   activation gate, in `guardrails.model_allowlist`, and state
   `identity_source: session-init-model`; `.fusion.example.json` follows for `gemini-flash`.
   Mutation: in `tests/test_agy_session_identity.py` (14 tests), 8 failed and 6 passed before
   the adapter change; 17 failed in the profile tables of `tests/test_model_generation.py` and
   `tests/test_agy.py` before the configuration change.

### Added

1. Five profiles. `gpt-terra` (`gpt-6.1-terra`) and `gpt-luna` (`gpt-6-luna`) over the Codex
   CLI route are enabled and admitted to the model allowlist after their live route smoke.
   `gemini-argon` (`gemini-4-argon-high`), `gemini-carbon` (`gemini-4-carbon-high`) and
   `gemini-hydrogen` (`gemini-4-hydrogen-xhigh`) over the `agy` headless transport were
   rejected in their first smoke because the terminal result carried no model identity, and now
   ship enabled in `guardrails.model_allowlist` with no activation gate and
   `identity_source: session-init-model` after the `agy` session-init identity update and their
   dated live smokes.
2. The naming rule. `autofusion.model_naming` holds two pure functions, and
   `tests/test_model_generation.py` pins the shipped profiles to their ids and holds the rule on
   both the built-in defaults and `.fusion.example.json`: a digit in a handle is a violation,
   and so is a second handle for the same family name, effort and compound flag, with negative
   arms built from fixture configurations.
   Mutation: 7 failed with the rule reverted; 5 failed with only the version arm reverted;
   3 failed with only the duplicate arm reverted.
3. OpenAI-compatible transport option `params.structured_output: "prompt"`: the response
   schema travels inside the prompt instead of `response_format`, for endpoints that refuse
   API-level output contracts. Local schema validation is unchanged, so an ignored
   instruction fails output validation rather than being trusted. `params.session_header`
   sends a fresh per-call identity in the named header. Written for Agent Web Bridge, a
   loopback daemon that drives the operator's signed-in ChatGPT tab; two disabled profiles
   `chatgpt-web-high` and `chatgpt-web-xhigh` ship behind the activation gate
   `main-session-verified-web-bridge-identity-smoke`, in their own `openai-chatgpt-web`
   quota group because the ChatGPT web session is not the Codex CLI quota. Tests: the prompt
   carries the schema and no `response_format`; the session header differs per call; output
   that violates the schema is rejected; an unknown mode is a configuration error.
   Live: one `call` through the bridge returned schema-valid output in 14,216 ms
   (`docs/provider-verification.md`). This item was listed as unreleased on the main branch
   before 1.0.0.

### Verification

1. Live route smokes, 15 November 2026, one bounded `autofusion call` per profile with a public
   toy function, no tools and the packaged reviewer schema. Completed with schema-valid output:
   `gpt-sol` (19,057 ms), `gpt-sol-ultra` (16,279 ms), `gpt-terra` (13,564 ms), `gpt-luna`
   (11,338 ms), `astra-ultra` (17,314 ms) on Codex CLI 0.169.0, and `claude-opus` (24,264 ms,
   effective model `claude-opus-5-5`) on Claude Code 2.1.325. Metadata only, in
   `docs/provider-smoke-20261115.json`.
2. `agy` live smoke, 15 November 2026, `agy` 1.5.9. On the first smoke against a scratch
   configuration with the three Gemini 4 profiles enabled, the adapter of that moment rejected
   each result because the terminal result carries no model identity (`agy` names the model in
   its session `init` event only); `agy` 1.1.27 does not know the Gemini 4 ids at all. Before
   changing the shipped defaults, all four Gemini profiles passed a bounded smoke from a scratch
   copy with the changed adapter (`gemini-hydrogen` 15,937 ms, `gemini-carbon` 33,541 ms,
   `gemini-argon` 10,772 ms, `gemini-flash` 21,956 ms, all schema-valid), and a live negative
   control on a scratch copy claiming `gemini-4-carbon-high` for a call served with
   `gemini-4-hydrogen-xhigh` was refused with
   `provider identity mismatch: expected gemini-4-carbon-high, received gemini-4-hydrogen-xhigh`.
   With the shipped defaults enabled, one bounded `autofusion call` per profile on `agy` 1.5.9
   completed with schema-valid output and effective model equal to the canonical model:
   `gemini-hydrogen` (16,816 ms, `gemini-4-hydrogen-xhigh`), `gemini-carbon` (35,534 ms,
   `gemini-4-carbon-high`), `gemini-argon` (12,775 ms, `gemini-4-argon-high`) and
   `gemini-flash` (8,710 ms, `gemini-3.8-flash-high`), recorded in
   `docs/provider-smoke-20261115.json`.
3. Grok ACP smoke, 15 November 2026, grok CLI 1.0.40: the ACP preflight refused to start
   because a user-level MCP configuration file existed on the smoke machine. No model call
   was made; the profile ships disabled.

### Pending

1. A Grok smoke on a machine without user-level MCP configuration.
2. Verified context windows for the moved and new profiles, replacing the carried numbers.

## 0.6.0

Released 8 September 2026. This release moves the active contracts from the July 2026 model
generation to the current one, closes twelve correctness findings from an independent
review of the 0.5.0-alpha.2 tree, and adds two sandboxed transports that ship disabled.
Every repair below carries a mutation note: the repair was reverted once with its tests
kept, the whole suite was run, and the failures named are the reproduction tests. A repair
whose reversal failed nothing was not counted.

### Changed

1. Current identities. The Claude CLI `opus` route now expects `claude-opus-5`. Validated
   `self` identities are Opus 5, Sonnet 5, Haiku 4.5 and Fable 5.1. The July measurements
   in `docs/provider-verification.md` keep their dates and model names as historical
   evidence; they are not September verification.
2. Fable is self-only. Neither `claude-fable-5-1` nor legacy `claude-fable-5` can be a
   reviewer, judge, delegate target, alias or overlay, including disabled profiles. Legacy
   `claude-fable`, `dual-fable` and `external-council-fable` configurations fail with a
   migration error naming `claude-opus`, `dual-opus` and `external-council`. Enforced at
   configuration load, at admission and at the HTTP boundary.
   Mutation: 46 failed with the source, config and validator reverted; 4 failed when only
   the HTTP self-profile guard was reinstated.
3. Astra ultra. `astra-ultra` calls `gpt-6-astra` through the existing Codex adapter with
   `model_reasoning_effort=ultra`, declared compound execution and explicit reviewer and
   judge admission. Sol, Astra and Terra carry the shared `openai-chatgpt` quota group.
   Call records now separate `configured_model`, `observed_model`, `identity_evidence` and
   `quota_group`; `effective_model` keeps its resolved-route meaning.
   Mutation: 40 failed with the step reverted; 15 failed with only the semantic identity
   checks bypassed.
4. Context fit. Admission computes a conservative UTF-8 byte bound over the rendered
   prompt, canonical schema and mandatory selected files, adds configured prompt overhead
   and output reserve, and checks it against the smallest usable participant capacity,
   including `self`, advisor and judge. Equal packet hashes no longer substitute for fit.
   A fixed judge prompt is bounded before proposers are dispatched.
   Mutation: 23 failed with fit bypassed; 5 with the mandatory-bytes gate bypassed; 6 with
   largest-instead-of-smallest capacity; 1 with the judge lower bound removed.
5. Uniform admission. Advisor calls, direct calls and runtime-loaded compound roles pass
   the same model, vendor, callable, compound and redaction checks as panel participants.
   Actual provider profiles must equal admitted profiles; scalar roles reject lists; CLI
   transports always undergo snapshot redaction; caller-supplied response schema strings
   are scanned before dispatch.
   Mutation: 8 failed with the five repair files reverted; 5 and 1 in the two follow-ups.
6. Routing minimums against the actual panel. Path and pack minimums evaluate the executed
   topology and distinct model identities, not the preset label, including after
   escalation. Built-in strength names cannot alias to a weaker meaning.
   Mutation: 3 failed on the initial repair, 2 on the alias follow-up.
7. Monotonic guardrails. Repository and invocation layers take the minimum finite cost cap
   and the strongest redaction action; null cannot erase an inherited finite cap;
   boolean, negative, invalid and nonfinite costs are rejected before cloning; allow and
   deny lists must be actual string lists at merge and at admission.
   Mutation: 11 failed with both repair files reverted; 4 and 8 in the follow-ups.
8. One execution deadline. The budget ledger inherits the run start and exposes one
   absolute deadline; topology waits include queue time; registry and CLI recompute the
   remaining allowance before transport execution; grounding consumes the same ledger and
   stops later commands; snapshot preparation consumes the run allowance. Built-in
   `urllib` is rejected inside an orchestrated run because it cannot bound total DNS,
   header and body time; WSL grounding under an orchestration budget is rejected because
   Linux child termination is unproven there.
   Mutation: 10 failed with the nine changed files reverted.
9. Coverage and abstention. Analysis separates a completed transport from complete
   coverage and a settled recommendation. Missing mandatory coverage and abstention
   survive analysis, reconciliation and receipts; any reported gap requires human review.
   Mutation: 23 failed with the seven production, schema and validator files reverted.
10. Pending analysis is byte-bound. The hash of the exact post-grounding serialization is
    stored in ledger-bound pending state; finalization hashes and parses the same bytes
    and rejects any change before proof attachment, reconciliation or signoff.
    Mutation: 4 failed.
11. Proof binds to the reviewed revision. The engine freezes a reviewed tree hash with the
    same contract used by proof revisions; a signed capsule whose head does not match the
    reviewed artifact is rejected for every relation. Proof staging materializes only
    manifest files, and the materialized head is hashed against the intent before the
    overlay is applied, so a source file changed during the base check never executes.
    Mutation: 12 failed on the binding; 2 on canonical staging; 1 on staged-copy integrity.
12. Proof attachment is durable. Exact signed capsule bytes are persisted before
    acceptance is recorded; every retry revalidates accepted bytes, signatures, identity
    and artifact binding and attaches the union of prior and new capsules. One advisory
    lock covers the whole per-run finalization transaction, so a competing finalization
    cannot drop an accepted capsule.
    Mutation: 5 failed on durability; 1 on the concurrent-finalization race.
13. Precise proof execution results. Proof confirmation requires the intended verification
    outcome, an intended assertion or static diagnostic, and untruncated output. Generic
    nonzero exits, collection or setup failures and no-tests results no longer satisfy a
    head or mutant failure; pytest exit semantics apply only to pytest invocations; the
    actual Python traceback takes precedence over printed assertion text; invocation hashes
    bind verification kind and expected patterns. Legacy capsules without typed match
    evidence are not promoted to confirmed proof.
    Mutation: 51 failed with the five production and schema files reverted.
14. Supervised stdin. The process runner writes stdin from a worker that is closed under
    the deadline, starts the deadline before spawn, supervises all pipes and bounds worker
    cleanup, so a child that never reads its input cannot hold the run open.
    Mutation: 2 failed.
15. `.gitattributes` keeps hash-bound JSON fixtures byte-exact on checkout, so linked-run
    provenance validates on `core.autocrlf=true` checkouts without rewriting any
    historical hash.
16. Operational notes moved from `tasks/` to `docs/history/`. Release hygiene files added:
    `.gitignore`, `SECURITY.md`, `CONTRIBUTING.md`.

### Added

1. Antigravity `agy` headless transport (`providers/agy.py`) and the disabled
   `gemini-flash` profile, canonical `gemini-3.8-flash-high`. The process runs with
   `--sandbox` inside a temporary configuration home that replaces HOME and USERPROFILE and
   contains only generated deny rules and an empty MCP configuration; native tools,
   writes, commands and URL actions are denied; the home is removed after cleanup.
   Terminal model identity is required; the requested model is never substituted for it.
   Mutation: 58 failed with the transport and its integration removed.
2. ACP transport (`providers/acp.py`) and the disabled `grok` profile, canonical
   `grok-4.6`. Stdio JSON-RPC with version checking, correlated responses, bounded
   queues, strict UTF-8 and JSON, cancellation grace and deadline enforcement. Filesystem
   and terminal capabilities are disabled; permission requests select only an unambiguous
   rejection; a bounded TOML preflight refuses unsafe local configuration before spawn.
   Mutation: 119 failed with the transport and its integration removed.
3. Windows Job Object containment (`providers/_job.py`): a suspended child is assigned to
   a kill-on-close job before it resumes; cleanup waits for member termination and handle
   release. POSIX cleanup terminates the process group after its leader exits.
4. Complete credential redaction (`dlp.py`): complete, malformed, truncated and nested
   PEM blocks, quoted and structured credential values, and credential-keyed structured
   data are removed before dispatch; ordinary prose, exact placeholders and numeric
   metadata are preserved; packet-only requests keep their packet hash after sanitation.
   Mutation: 50 failed.

### Verification

1. Astra adapter smoke, 8 September 2026: one `ProviderRegistry` call through
   `CodexExecAdapter` on Codex CLI 0.153.4 completed `gpt-6-astra` at `ultra` in 15,692 ms
   with schema-valid output. The stream exposed no model identity, so the record carries
   `identity_evidence=configured-route` and `observed_model=null`. Metadata only, in
   `docs/provider-astra-smoke-20260908.json`.
2. `agy` live smoke, 8 September 2026: terminal SUCCESS and schema-valid review, but no
   terminal model identity; the adapter rejected the result and the profile ships disabled.
3. Grok ACP live smoke, 8 September 2026: protocol error before initialization completed;
   the corrected argument order was established from help output without a second model
   call, and the profile ships disabled pending a live retest.
4. Claude CLI `claude-opus-5`: unit-tested contract; no live smoke in this release.
5. The three implementation branches were merged with cross-step tests
   (`tests/test_merge_deadlines.py`, `tests/test_merge_context_identity.py`,
   `tests/test_merge_transport_policy.py`): the `agy` and ACP transports stop at the inherited
   run deadline, context preflight rejects before a rejected admission reaches a provider,
   a callable Fable profile is rejected by admission as well as by the loader, configured-route
   identity survives result handling, and redaction growth cannot exceed the admitted
   context. Mutation: 12 failed with the merge repairs reverted and the tests kept.
6. Full gate on the release revision, measured 8 September 2026 on Windows with Python 3.14:
   794 passed and 1 skipped (the POSIX-only no-op test), Ruff clean, strict mypy clean over
   96 source files, four repository validators passed. On Linux (WSL, Python 3.12) the same
   revision gave 790 passed and 5 skipped (the Windows Job Object tests). The Linux run found
   two defects the Windows gate could not: a test that imported `scripts` only because
   `python -m pytest` puts the working directory on the path, and a test that seeded a
   difference inside a copied snapshot whose directories kept their 0o555 modes. Both are
   fixed in this release; CI on the release pull request is the multi-version record.

### Known limits

1. A supervisor termination between suspended process creation and Job Object assignment
   on Windows can leave a suspended child; provider code cannot run in that interval.
   Separately, the general process runner does not own descendants of its direct child: a
   descendant that keeps the standard handles open after its parent exits can hold the
   runner's pipes until it ends. Reproduced on Windows; guaranteed ownership needs a
   containment redesign and is not claimed here.
2. Malformed credential structures can cause conservative removal of the remainder of
   that string.
3. Pending runs prepared before 0.6.0 lack byte binding and durable capsule bytes; they
   must be prepared again.
4. Whether cross-model review beats same-model self-review on real decisions is not
   measured here.

## 0.5.0-alpha.2

### Changed

1. Promoted Python and Claude Code plugin metadata to the second 0.5 alpha release.
2. Clarified that Claude Fable remains disabled in portable defaults because usage-credit
   entitlement and provider routing are operator-specific, even after one environment
   passes canonical identity verification.
3. Added a metadata-only provider verification record for the exact published adapter
   paths without persisting prompts, model output, credentials, or private source.
4. Updated Fable validator diagnostics to describe the portable-default policy rather
   than implying that one operator smoke can safely enable the profile for every user.

### Verification

1. Claude Code 2.1.212 completed the published Opus route with effective model
   claude-opus-4-8, recommendation ship, and zero findings.
2. With operator usage credits available, the published Fable route completed with
   effective model claude-fable-5, recommendation ship, and zero findings.
3. Provider identity and policy closure passed 35 targeted tests, Ruff, and strict mypy
   before the alpha.2 release preparation.

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
