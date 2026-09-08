# autofusion

autofusion is a Claude Code first escalation system for serious engineering work. It combines an active Claude Code session with independent model reviewers, evidence grounding, explicit disagreement, and auditable receipts.

The repository is currently an alpha runtime. It includes a typed Python package, deterministic routing, immutable snapshots, packet compilation, provider adapters, grounding, proof capsules, durable run journals, reconciliation, metadata-only receipts, replay and evaluation helpers, and a Claude Code plugin skeleton.

The plugin remains skill-first. Installing the plugin does not magically grant model credentials or a live callable route. Successful fusion requires the helper CLI and the configured provider transports to pass runtime checks. If a model, sandbox, DLP gate, budget, or receipt write fails, autofusion records degraded state instead of claiming fused success.

## Why autofusion

A larger panel is not automatically a better panel. Models can share blind spots, judges can be biased by answer order, and a polished synthesis can hide unresolved contradictions. autofusion therefore optimizes for four things:

1. Independent review paths.
2. Executable evidence where the artifact permits it.
3. Visible contradictions instead of forced consensus.
4. Honest run accounting, including partial and failed fusion.

Fusion remains an escalation. Routine work should stay with one model. Use fusion when the cost of a missed issue is higher than the review latency.

## Two execution models

### /autofusion:fusion

The running Claude Code session is self, the drafter and reconciler. self is not a callable transport. External helpers may call Codex, Claude CLI profiles, or configured APIs, but no Python process pretends to invoke the active Claude session.

### /autofusion:fusion-parallel

Every participant is externally callable. This mode supports batch, CI, independent proposals, and panels outside an active Claude Code session.

## Built-in model profiles

The target profile contract is:

1. gpt-sol calls gpt-5.6-sol through Codex with xhigh effort.
2. gpt-sol-ultra calls the same gpt-5.6-sol model with configured Codex reasoning effort `ultra` and declared compound execution.
3. claude-opus calls Claude CLI with the opus alias, canonically Claude Opus 5 (`claude-opus-5`), at xhigh.
4. self represents the active session and is explicitly non-callable. Current default identities are Opus 5, Sonnet 5, Haiku 4.5, and Fable 5.1.

gpt-sol-ultra is a compound execution mode, not a second model family. Its hidden subagents do not count as independent panel votes.

There is no silent fallback to GPT 5.5 or another model. Autofusion rejects canonical model identity mismatches. Fable is permitted only as the active self session; callable profiles, aliases, overlays, and panel roles cannot execute it. If a requested profile is unavailable, the run records degradation and cannot claim successful fusion.

## Fusion topologies

1. review sends one frozen artifact to an independent reviewer, grounds checkable findings, and reconciles every finding.
2. adversarial-review asks the reviewer to disprove assumptions, construct counterexamples, and demand a rewrite when a patch would preserve the architectural defect.
3. dual-review runs two blind reviewers against the same packet and preserves provenance for both.
4. panel-rank compares independent proposals with blind identities and a fixed rubric. It selects a base proposal. It does not perform a naive merge.
5. advisor asks one external model a bounded question. Advisor output is consultation, not successful fusion by itself.
6. adaptive selects a bounded scaffold from approved topologies using task risk, verifiability, reversibility, model availability, latency, and budget.

Unresolved blocker or major findings may receive one challenge and one rebuttal. Debate is not an unbounded topology.

## Proof Fusion

Proof Fusion is a post-review evidence stage, not another model vote. An independently authored test overlay declares a typed relation across immutable revisions such as base, head, fixed, and mutant. The runtime validates the intent, overlay paths, author separation, revision hashes, trusted verification ID, expected outcomes, and mutation gate before it can emit a confirmed proof capsule.

Generated test code never runs through the ordinary host or WSL grounding runner. It requires a Docker image reference pinned by a SHA-256 digest, denied network, a read-only container root, dropped capabilities, no-new-privileges, bounded processes, bounded memory, bounded CPU, and an attested image identity. The runner executes the immutable inspected image ID rather than resolving the configured name again. Without that runner, proof execution fails closed.

Every persisted proof capsule also requires a local HMAC attestation. The signing secret is read from `AUTOFUSION_PROOF_ATTESTATION_KEY`, must contain at least 32 bytes, and is never passed into Docker or written to the capsule. This authenticates the capsule to the local Autofusion process. It does not replace managed key custody or an externally attested production runner.

The current alpha executes and verifies supplied proof intents and overlays. Automatic model-driven test generation is not yet an advertised capability.

## Fusion packs

The built-in migration, security, release, incident, API contract, dependency, and research evidence packs add artifact-specific roles, minimum presets, allowed topologies, and proof policy. A pack may escalate a route, but it cannot weaken global policy. See [Proof Fusion](docs/proof-fusion.md) and [fusion packs](docs/fusion-packs.md) for the executable contracts and failure boundaries.

## Presets

1. fast uses one cross-model review round and avoids a second call unless a blocker remains.
2. balanced uses blind GPT and Claude reviews with bounded concurrency.
3. high uses the strongest configured profiles and adversarial review.
4. quality is a compatibility alias for high.
5. budget minimizes additional paid API usage but never promises universal zero cost.
6. adaptive chooses only among operator-approved presets and may escalate upward. It cannot silently downgrade a hard safety gate.

Explicit topology and reviewer settings override a preset only inside immutable global policy, privacy, model, recursion, and budget limits.

## Structured panel analysis

The panel analyzer does not simply merge prose. It reports:

1. Agreement.
2. Contradictions.
3. Partial coverage.
4. Reviewer-specific insights.
5. Blind spots.
6. Grounding candidates.
7. Executed grounding results.
8. Decision impact.

Agreement is not automatically confidence. Correlated models may agree for the same wrong reason. Evidence strength and reviewer independence remain separate fields.

See [the fusion analysis schema](schemas/fusion-analysis.schema.json) and [fusion topologies](docs/fusion-topologies.md).

## Inspiration and differentiation

The adaptive scaffold design is informed by Sakana Fugu, Trinity, and Conductor. The structured panel analysis, presets, selective invocation, cost visibility, and recursion limits are informed by OpenRouter Fusion.

autofusion differs in its operating target. It is repository-aware, Claude Code first, verification-oriented, and explicit about the non-callable self boundary. It also refuses to count opaque hidden workers as independent evidence and refuses to present a degraded single-model answer as fused.

See [competitive research](docs/competitive-research.md) for the source analysis and adoption boundaries. See [product differentiators](docs/product-differentiators.md) for the independently designed proof, routing, evaluation, and fault-testing roadmap.

## Install from Claude Code

Register the marketplace and install the plugin:

~~~text
/plugin marketplace add OnourImpram/autofusion
/plugin install autofusion@autofusion
~~~

Example invocations:

~~~text
/autofusion:fusion diff --preset balanced
/autofusion:fusion plan --topology adversarial-review --reviewers gpt-sol-ultra,claude-opus
/autofusion:fusion answer --preset adaptive --focus research-evidence
/autofusion:fusion release --pack release --preset adaptive
/autofusion:fusion-parallel --panel external-council
~~~

If the helper CLI is unavailable, the skills must say that the run is manual and must not claim unavailable transports, grounding, or receipts were executed.

Install the helper package from a checkout or release artifact:

~~~text
python -m pip install autofusion
autofusion doctor
autofusion config-validate --config .fusion.example.json
autofusion packs
autofusion proof-hash --root ./snapshot
autofusion prove --repo . --intent intent.json --overlay proof-overlay --revision base=base-snapshot --revision head=head-snapshot --revision mutant=mutant-snapshot --output proof-capsule.json
autofusion run-status RUN_ID
~~~

Load `AUTOFUSION_PROOF_ATTESTATION_KEY` from operator-controlled secure storage before `autofusion prove`. Do not place the key in `.fusion.json`, shell history, proof overlays, receipts, or repository files.

## Configuration

Start from [.fusion.example.json](.fusion.example.json). Reviewer-supplied command text is never executed. Grounding may invoke only trusted verification IDs whose argv arrays were approved before review.

Fable 5.1 (`claude-fable-5-1`) is self-only. Legacy `claude-fable-5` may be explicitly registered as self with identity metadata, but neither identity may be called externally. Remove legacy `claude-fable` profiles from overlays and use `claude-opus`; replace `dual-fable` with `dual-opus`, and `external-council-fable` with `external-council`. Legacy overlays fail with a migration error. The July Fable call record is retained as historical evidence of the former policy in [provider verification](docs/provider-verification.md).

Third-party compound orchestrators such as OpenRouter Fusion or Sakana Fugu can be configured later as optional comparison providers. They are disabled in the example, cannot nest by default, and do not expose enough worker provenance to satisfy a cross-model quorum on their own.

## Current status

Implemented in the alpha runtime:

1. Typed Python package and CLI entry point.
2. Non-callable `self` boundary.
3. Enabled built-in profiles for GPT 5.6 SOL, GPT 5.6 SOL Ultra, and Claude Opus 5, with Fable restricted to self.
4. Deterministic adaptive routing, hard gates, context quorum, and budget accounting.
5. Review, adversarial review, dual review, advisor, and panel-rank topologies.
6. Codex CLI, Claude CLI, OpenAI-compatible HTTP, Anthropic HTTP, and deterministic fake providers.
7. Immutable repository snapshots and DLP preflight.
8. Trusted verification IDs, argv-only grounding, and network-denied runner detection.
9. Finding reconciliation, deadlock handling, metadata-only receipts, replay, policy signing, drift snapshots, telemetry stubs, and GitHub annotations.
10. Mutation-gated, locally HMAC-attested proof capsules behind disposable Docker isolation.
11. Hash-chained idempotent run journals and pending reconciliation status.
12. Metadata-only precedent and downstream outcome records restricted to post-blind review.
13. Declarative fusion packs and a human-gated GitHub Checks report renderer.
14. Contract validators, strict mypy, ruff, and coverage-gated tests.

Still gated before public 1.0 claims:

1. Real smoke evidence for every enabled advertised live profile in each supported operator environment. Optional profiles remain disabled in portable defaults and require a local canonical identity smoke before activation.
2. External replication showing cross-model gain beyond same-model self-review.
3. Managed production signing for provider routing, cost, external runner identity, and key rotation.
4. Stronger disposable isolation beyond WSL or Linux `unshare`.
5. Repeated installation and provider verification from immutable release artifacts across additional operator environments.
6. Automatic proof-test authoring and interrupted provider-dispatch resume.

## Alpha Provenance Boundary

Receipts reconcile against the linked analysis artifact. A `ship` receipt is valid only when the linked analysis has no blocker or major findings. The runtime writes structural hashes for packets, snapshots, calls, grounding, and receipts, and the validators reject mismatched summaries.

This is still an alpha provenance system. Validators check linked artifact hashes, participant identity consistency, and receipt summaries. Claude CLI identity comes from provider usage telemetry. A multi-model Claude envelope is accepted only when the canonical model is the dominant output-token contributor. Historical Codex CLI 0.144 compatibility permits omitted model identity in JSONL: the adapter-pinned `--model` argument and a successful `turn.completed` event provide configured routing evidence, without an observed provider identity. See the dated records in [provider verification](docs/provider-verification.md). The project does not yet claim cryptographic authenticity of external model usage, subscription entitlements, or provider bills. Those require captured trusted runtime evidence and release signing.
