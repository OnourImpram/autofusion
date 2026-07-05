# Roadmap

## Phase 0: provider and registry foundation

1. Add Python package skeleton.
2. Implement config loading and model registry merge.
3. Implement `self` as a non callable sentinel.
4. Implement `codex-exec` transport.
5. Add `doctor` and `validate-config` commands.
6. Add schema validation tests.

Exit criteria: a reviewer call can be made through Codex, malformed responses are rejected, and `self` cannot be called from Python.

## Phase 1: review vertical slice

1. Implement structured reviewer findings.
2. Implement approved command grounding.
3. Implement disposition and signoff contracts.
4. Implement deadlock escalation.
5. Implement receipts.
6. Run a real diff fusion with Claude Code as drafter and Codex as reviewer.

Exit criteria: a real diff fusion run records grounding evidence, decision change state, and honest degradation behavior.

## Phase 2: model independent panels

1. Add Anthropic API transport.
2. Add OpenAI compatible transport.
3. Add panel topology.
4. Add model handle level timeouts and retry policy.
5. Add richer receipt comparison across models.

Exit criteria: adding a new model handle requires config only, not code changes.

## Phase 3: distribution polish and evaluation

1. Add CI.
2. Add eval harness comparing fusion against solo and same model self review.
3. Add release packaging.
4. Add plugin validation workflow.
5. Add documentation site if needed.

Exit criteria: the repo can prove when cross model diversity adds value beyond ordinary self review.
