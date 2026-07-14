# Fusion packs

Fusion packs are declarative policy overlays for recurring high-consequence artifact classes. A pack selects task-specific roles, raises the minimum preset when necessary, restricts valid topologies, and declares proof expectations.

A pack may tighten a run. It cannot weaken global model, privacy, budget, isolation, recursion, or proof policy.

## Built-in packs

| Pack | Minimum preset | Allowed topologies | Review roles | Proof policy |
| --- | --- | --- | --- | --- |
| migration | high | adversarial-review, dual-review | data integrity, rollback, compatibility | required for blocker and major |
| security | high | adversarial-review, dual-review | threat model, exploitability, mitigation | required for blocker and major |
| release | balanced | review, dual-review | regression, observability, rollback | preferred |
| incident | high | adversarial-review, dual-review | timeline, competing hypotheses, containment | preferred |
| api-contract | balanced | review, dual-review | compatibility, consumer impact, versioning | required for blocker and major |
| dependency | balanced | review, adversarial-review, dual-review | license, vulnerability, supply chain | preferred |
| research-evidence | balanced | review, dual-review, panel-rank | source quality, claim traceability, contradiction | not applicable |

`autofusion packs` prints the effective configured registry.

## Routing behavior

Pack routing is deterministic:

1. The artifact kind must be allowed by the selected pack.
2. A requested preset below the pack minimum is escalated.
3. An explicit topology outside the pack allowlist is rejected.
4. The selected roles enter the frozen packet.
5. The effective pack and route are recorded in the receipt.

Example:

~~~text
autofusion run \
  --repo . \
  --task "Review the account migration" \
  --kind migration \
  --pack migration \
  --preset adaptive
~~~

## Proof policy meaning

`required-for-blocker-major` is a decision gate, not permission to run arbitrary tests. A suitable independently authored proof must still exist and the strong proof runner must be available. When a claim cannot be proved safely, the correct terminal path is operator escalation, not fabricated confirmation.

`preferred` asks the workflow to seek proof when the artifact supports it. Absence of proof remains visible.

`not-applicable` prevents executable proof claims for evidence syntheses. Repository facts and source traceability may still be checked through other approved tools.

## Extension rule

Custom packs are configuration, not Python plugins. They must declare artifact kinds, minimum preset, allowed topologies, roles, and proof policy. Configuration merging preserves monotonic safety, so repository-local settings cannot enlarge globally approved proof paths or weaken proof and precedent controls.
