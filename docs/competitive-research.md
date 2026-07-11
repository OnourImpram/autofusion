# Competitive research

This document records product and research ideas that inform autofusion. It is not a source-code port. All implementation contracts and text in this repository are independently written for autofusion.

## Sakana Fugu

Sakana Fugu presents a multi-agent system as one model interface. The orchestrator may solve a request directly, route it to one worker, or build a multi-agent scaffold. Its worker pool is swappable and may respect provider, privacy, or compliance exclusions.

The June 2026 technical report describes two operating points:

1. Fugu emphasizes lower latency and usually selects one worker.
2. Fugu Ultra emphasizes quality and composes workflows from multiple workers.
3. The orchestrator chooses workers, writes targeted role instructions, coordinates communication, requests verification, and decides when to synthesize.
4. The system is query-adaptive rather than tied to one fixed topology.

Sources:

1. [Sakana Fugu release](https://sakana.ai/fugu-release/)
2. [Sakana Fugu technical report](https://arxiv.org/abs/2606.21228)
3. [TRINITY: An Evolved LLM Coordinator](https://arxiv.org/abs/2512.04695)
4. [Learning to Orchestrate Agents in Natural Language with the Conductor](https://arxiv.org/abs/2512.04388)
5. [SakanaAI/fugu public repository](https://github.com/SakanaAI/fugu)

At the 11 July 2026 audit, GitHub metadata for the public SakanaAI/fugu repository reported no declared license. No repository code, prompts, or distinctive text are copied. Only public architectural concepts are independently reimplemented. The technical paper has its own publication license and is used as a cited research source.

### Ideas adopted

1. Treat orchestration as a first-class capability rather than a hard-coded panel.
2. Separate Thinker, Worker, Reviewer, Verifier, Adversary, and Judge responsibilities.
3. Select effort and topology from task properties, not from one global default.
4. Allow provider and model exclusions as policy inputs.
5. Record the scaffold plan before dispatch so adaptive behavior remains inspectable.
6. Use receipts as training data for a future calibrated router.

### Ideas deliberately constrained

1. autofusion does not train a learned orchestrator in the first engine release. It starts with deterministic risk rules and runs any learned router in shadow mode until calibrated.
2. Recursive orchestration is capped at depth one. More recursion is not accepted merely because a compound provider supports it.
3. Opaque compound systems do not satisfy cross-model quorum by themselves because autofusion cannot verify which hidden workers ran.
4. Reviewer roles remain non-mutating. A worker may edit only in an explicitly separate future topology.

## OpenRouter Fusion

OpenRouter Fusion runs a panel in parallel, gives responses to a judge, returns structured analysis, and lets a calling model write the final answer. The official analysis surface distinguishes consensus, contradictions, partial coverage, unique insights, and blind spots.

The official product also exposes:

1. Curated high and budget tiers. autofusion defines fast as its own latency-oriented preset.
2. Explicit panel and judge overrides.
3. A bounded panel size and bounded tool-call budget.
4. Selective invocation when the base model decides a task warrants fusion.
5. Per-model activity and additive cost visibility.
6. Recursion protection for nested Fusion calls.
7. Workspace guardrails for budgets, provider restrictions, prompt-injection handling, and sensitive-data handling.

Sources:

1. [OpenRouter Fusion documentation](https://openrouter.ai/docs/guides/features/plugins/fusion)
2. [OpenRouter Fusion evaluation](https://openrouter.ai/blog/announcements/fusion-beats-frontier/)
3. [OpenRouter Advisor](https://openrouter.ai/blog/announcements/advisor-server-tool/)
4. [OpenRouter Subagent](https://openrouter.ai/blog/announcements/subagent-server-tool/)
5. [OpenRouter Guardrails](https://openrouter.ai/blog/announcements/guardrails/)

### Ideas adopted

1. Structured analysis is separate from final synthesis.
2. Explicit model settings override presets only inside enclosing policy.
3. Presets represent quality, cost, and latency tradeoffs.
4. A bounded advisor consultation is distinct from a full fusion run.
5. Every receipt records requested models, effective models, latency, and cost when available.
6. Context capacity is a quorum property. A packet must fit every required participant.
7. Fusion nesting is bounded and visible.
8. Prompt injection and sensitive-data controls support flag, redact, and block actions.

### Ideas deliberately constrained

1. Agreement is not treated as higher confidence without independent evidence. Model errors can be correlated.
2. Web search is not enabled by default for repository review. Research profiles may enable it with explicit source and domain policy.
3. A judge analyzes responses but does not perform an unconditional prose merge.
4. A failed panel may return partial findings, but it cannot silently become a successful single-model answer.
5. Same-model repeated sampling is measured as replication, not cross-model diversity.

## Second-pass coverage audit

A second review of the public Fugu technical report and the broader OpenRouter routing surface found several mechanisms beyond the first product summary. Every item below is classified so upstream inspiration does not become an untracked dependency.

### Additional Fugu mechanisms

1. **Per-step routing.** The latency-oriented Fugu can choose a different worker at critical turns in a longer trajectory. autofusion now reserves per-step routing in the orchestration contract. It remains deterministic or shadow-only until outcome calibration exists.
2. **Explicit communication graphs.** Fugu Ultra workflows assign each subtask a worker and an access list describing which earlier outputs it may see. autofusion adopts an explicit access-list graph instead of exposing the entire transcript to every participant.
3. **Intra-workflow isolation.** Fugu Ultra isolates current tool trajectories so an early worker does not steer every later worker onto the same path. autofusion adopts isolated first-pass tool traces and records every function-call owner.
4. **Controlled shared memory.** Fugu Ultra distinguishes isolated current-workflow traces from shared prior-workflow memory. autofusion permits only hash-addressed, policy-approved inter-workflow memory. It does not expose an ambient conversation transcript.
5. **Outcome-trained routing.** Fugu learns worker selection from replicated task rewards and end-to-end terminal outcomes. autofusion does not copy the training method. Receipts will support a capability ledger and a shadow router before any learned policy can affect execution.
6. **Pool substitution.** Fugu can exclude providers without retraining. autofusion already treats provider, region, retention, and model exclusions as policy inputs.

### Additional OpenRouter mechanisms

1. **Provider-aware fallback.** OpenRouter can retry models or providers after availability, rate-limit, moderation, or context errors and reports the concrete model that served the request. autofusion permits same-model endpoint fallback by default. A different-model fallback requires explicit policy, attested effective identity, and a fresh independence-quorum check. Policy or moderation blocks never become fallback triggers.
2. **Parameter and privacy routing.** Provider selection can require parameter support, deny data collection, enforce Zero Data Retention, and apply provider allowlists. These are now explicit provider-routing contract fields.
3. **Performance thresholds.** OpenRouter can rank providers using price, latency, throughput, and rolling percentiles. autofusion records p90 latency and throughput requirements as routing evidence rather than treating a transient fastest response as a stable capability claim.
4. **Structured-output healing.** OpenRouter can repair malformed JSON syntax. autofusion permits one syntax-only repair, hashes both forms, and fails when protected semantic fields change.
5. **Session stickiness.** OpenRouter can pin a model and provider during a conversation. autofusion permits stickiness inside one run for consistency and caching, but disables it across evaluation runs so provider pinning does not create hidden correlation.
6. **Isolated delegation.** OpenRouter Subagent workers see only a self-contained task description. autofusion maps this to packet completeness and explicit access lists.
7. **Named advisor memory.** OpenRouter Advisor can retain separate memory per advisor identity. autofusion reserves isolated role-memory channels, but shared memory remains opt-in and hash-addressed.
8. **Lossy context compression.** OpenRouter can trim the middle of oversized conversations. autofusion does not use lossy compression for mandatory code or policy context. Optional material may be summarized only when the context manifest records what was omitted.
9. **Quality-tier routing.** OpenRouter exposes high and budget Fusion tiers and a Pareto coding router that selects within a quality band. autofusion adds a canonical high preset, retains quality as a compatibility alias, and keeps fast as its own latency-oriented preset.

Additional sources:

1. [Fugu full technical report](https://ar5iv.labs.arxiv.org/html/2606.21228v2)
2. [OpenRouter provider routing](https://openrouter.ai/docs/guides/routing/provider-selection)
3. [OpenRouter model fallbacks](https://openrouter.ai/docs/guides/routing/model-fallbacks)
4. [OpenRouter Zero Data Retention](https://openrouter.ai/docs/guides/features/zdr)
5. [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
6. [OpenRouter response healing](https://openrouter.ai/docs/guides/features/plugins/response-healing)
7. [OpenRouter Subagent](https://openrouter.ai/docs/guides/features/server-tools/subagent)
8. [OpenRouter Advisor](https://openrouter.ai/docs/guides/features/server-tools/advisor)
9. [OpenRouter Pareto Router](https://openrouter.ai/docs/guides/routing/routers/pareto-router)
10. [OpenRouter message transforms](https://openrouter.ai/docs/guides/features/message-transforms)

### Coverage conclusion

As of 11 July 2026, no additional high-value public Fugu or OpenRouter orchestration mechanism remains unclassified. Some are adopted in the current contract, some are explicitly constrained, and the learned or stateful mechanisms are placed behind future engine and evaluation gates. This is a point-in-time audit, not a claim that either upstream product will stop evolving.
## What autofusion adds

autofusion is designed around an active engineering agent and repository truth.

1. self is the running Claude Code session and cannot be called by Python.
2. Review packets bind task, artifact, repository snapshot, constraints, and verification registry by hash.
3. Reviewer findings point only to trusted verification IDs. Reviewer text never becomes a shell command.
4. Execution evidence can override model opinion.
5. Unresolved blocker and major disagreements terminate in explicit operator escalation.
6. Receipts distinguish consultation, replication, compound execution, cross-model review, and successful fusion.
7. A degraded run cannot report fused true.

## Claim boundary

OpenRouter reported improvements on 100 DRACO deep-research tasks. The same report notes English-only, text-only, judge-dependent, and static-benchmark limitations. Sakana reports results across multiple coding, reasoning, and agent benchmarks, but those results evaluate Sakana Fugu rather than autofusion.

autofusion therefore makes no borrowed performance claim. Its value must be demonstrated on its own frozen artifacts, verification environments, solo baselines, same-model review baselines, cross-model review baselines, and human-adjudicated outcomes.