# autofusion

autofusion is a Claude Code first escalation system for serious engineering work. It combines an active Claude Code session with independent model reviewers, evidence grounding, explicit disagreement, and auditable receipts.

The repository is currently a pre-engine alpha. The Claude Code skills and design contracts are usable as a manual workflow. The Python engine, transports, grounding runner, and receipt writer are still roadmap items. The project does not claim those components exist before they are implemented and verified.

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
2. gpt-sol-ultra calls the same gpt-5.6-sol model with Codex ultra mode.
3. claude-opus calls Claude CLI with the opus alias, canonically Claude Opus 4.8, at xhigh.
4. claude-fable calls Claude CLI with the fable alias, canonically Claude Fable 5, at xhigh.
5. self represents the active Claude Code session and is explicitly non-callable.

gpt-sol-ultra is a compound execution mode, not a second model family. Its hidden subagents do not count as independent panel votes.

There is no silent fallback to GPT 5.5 or another model. If the requested profile is unavailable, the run records degradation and cannot claim successful fusion.

## Fusion topologies

1. review sends one frozen artifact to an independent reviewer, grounds checkable findings, and reconciles every finding.
2. adversarial-review asks the reviewer to disprove assumptions, construct counterexamples, and demand a rewrite when a patch would preserve the architectural defect.
3. dual-review runs two blind reviewers against the same packet and preserves provenance for both.
4. panel-rank compares independent proposals with blind identities and a fixed rubric. It selects a base proposal. It does not perform a naive merge.
5. advisor asks one external model a bounded question. Advisor output is consultation, not successful fusion by itself.
6. adaptive selects a bounded scaffold from approved topologies using task risk, verifiability, reversibility, model availability, latency, and budget.

Unresolved blocker or major findings may receive one challenge and one rebuttal. Debate is not an unbounded topology.

## Presets

1. fast uses one cross-model review round and avoids a second call unless a blocker remains.
2. balanced uses blind GPT and Claude reviews with bounded concurrency.
3. quality uses the strongest configured profiles and adversarial review.
4. budget minimizes additional paid API usage but never promises universal zero cost.
5. adaptive chooses only among operator-approved presets and may escalate upward. It cannot silently downgrade a hard safety gate.

Explicit topology, reviewer, and policy settings always override a preset.

## Structured panel analysis

The panel analyzer does not simply merge prose. It reports:

1. Agreement.
2. Contradictions.
3. Partial coverage.
4. Reviewer-specific insights.
5. Blind spots.
6. Grounding candidates.
7. Decision impact.

Agreement is not automatically confidence. Correlated models may agree for the same wrong reason. Evidence strength and reviewer independence remain separate fields.

See [the fusion analysis schema](schemas/fusion-analysis.schema.json) and [fusion topologies](docs/fusion-topologies.md).

## Inspiration and differentiation

The adaptive scaffold design is informed by Sakana Fugu, Trinity, and Conductor. The structured panel analysis, presets, selective invocation, cost visibility, and recursion limits are informed by OpenRouter Fusion.

autofusion differs in its operating target. It is repository-aware, Claude Code first, verification-oriented, and explicit about the non-callable self boundary. It also refuses to count opaque hidden workers as independent evidence and refuses to present a degraded single-model answer as fused.

See [competitive research](docs/competitive-research.md) for the source analysis and adoption boundaries.

## Install from Claude Code

Register the marketplace and install the plugin:

~~~text
/plugin marketplace add OnourImpram/autofusion
/plugin install autofusion@autofusion
~~~

Example invocations:

~~~text
/autofusion:fusion diff --preset balanced
/autofusion:fusion plan --topology adversarial-review --reviewers gpt-sol-ultra,claude-fable
/autofusion:fusion answer --preset adaptive --focus research-evidence
/autofusion:fusion-parallel --panel external-council
~~~

If the helper CLI is unavailable, the skills must say that the run is manual and must not claim unavailable transports, grounding, or receipts were executed.

## Configuration

Start from [.fusion.example.json](.fusion.example.json). Reviewer-supplied command text is never executed. Grounding may invoke only trusted verification IDs whose argv arrays were approved before review.

Compound orchestrators such as OpenRouter Fusion or Sakana Fugu can be configured later as optional comparison providers. They are disabled in the example, cannot nest by default, and do not expose enough worker provenance to satisfy a cross-model quorum on their own.

## Current status

Implemented now:

1. Claude Code marketplace and plugin skeleton.
2. Manual fusion and fully external workflow skills.
3. Model, preset, topology, analysis, safety, and receipt contracts.
4. JSON schemas for panel analysis and receipts.
5. Repository validation workflow.
6. Research-backed competitive design notes.

Not yet implemented:

1. Python engine and CLI.
2. Codex and Claude CLI transports.
3. Immutable repository snapshot builder.
4. Grounding sandbox.
5. Receipt persistence.
6. Live adaptive routing.
7. A/B evaluation harness.

The first engine milestone remains a real diff fusion run with Claude Code as drafter, Codex as reviewer, approved verification commands as grounding, and an honest receipt that proves what changed.