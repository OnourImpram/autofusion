# Safety model

autofusion is designed around one rule: evidence can override model opinion, but model opinion must not execute arbitrary commands.

## Reviewer isolation

In review mode, external reviewers should inspect the repository without editing it. The intended Codex path uses read only sandboxing and manifest or diff checks to prove that review did not mutate the working tree.

## Command origin

Reviewer findings may include a `check` field, but that field is advisory. It can point toward a relevant verification class, but it is never executed verbatim.

The grounding runner may execute only commands already declared in trusted `.fusion.json` configuration.

## Verdict asymmetry

A failing approved test that confirms a finding is strong evidence. A passing test is weaker. It may show that the specific command did not reproduce the problem, but it does not prove the finding false.

Grounding verdicts:

1. `confirmed`: an approved command supports the finding.
2. `not_reproduced`: approved commands ran, but did not reproduce the issue.
3. `inconclusive`: no relevant approved command exists, or the command failed because of environment setup rather than the asserted behavior.

## Honest degradation

If a reviewer transport fails, autofusion must not claim a fused result. The receipt should mark `fused: false` and name the failed transport.

## Deadlocks

A model deadlock is not solved by pretending the third model is an oracle. For unresolved blocker or major findings that cannot be grounded, the default arbiter is the operator.
