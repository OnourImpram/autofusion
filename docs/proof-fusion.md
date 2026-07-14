# Proof Fusion

Proof Fusion turns a review claim into a bounded executable experiment. It is a post-review evidence stage. It is not another vote and it does not let model text become a command.

## Contract

A verification intent binds:

1. One fusion run and finding.
2. One typed relation: regression, repair, feature, or counterexample.
3. One trusted verification ID.
4. An independent test author and patch author.
5. Candidate-fix blindness.
6. Immutable revision tree hashes.
7. A required mutant revision.

The test overlay is limited to configured proof paths. The runtime rejects absolute paths, parent traversal, symlinks, non-UTF-8 files, oversized overlays, duplicate destinations, and files outside the approved patterns.

## Relations

| Relation | Expected observations |
| --- | --- |
| regression | base passes, head fails, mutant fails |
| repair | head fails, fixed passes, mutant fails |
| feature | base fails, head passes, mutant fails |
| counterexample | control passes, head fails, mutant fails |

The mutation gate prevents a test that passes regardless of the behavior under dispute from becoming confirmed proof. A confirmed capsule requires every declared observation and a passing mutation gate.

## Execution boundary

Generated or externally authored proof code may run only through a configured proof runner that reports all of the following:

1. Network denied.
2. Strong disposable isolation.
3. A stable runner attestation hash.

The built-in strong runner uses Docker with no network, a read-only root filesystem, dropped Linux capabilities, no-new-privileges, bounded PIDs, memory and CPU, and a disposable workspace copy. The Docker image is intentionally unset by default. Operators must configure an image reference pinned by a full SHA-256 digest. Detection resolves and records its immutable local image ID. Execution uses that inspected ID, which closes the tag or reference change window between detection and execution.

The ordinary Linux or WSL unshare grounding runners are not accepted for proof overlays. They remain suitable only for trusted project verification definitions. If Docker is unavailable or the image is not configured, proof execution fails closed.

## Capsule semantics

A proof capsule records only bounded metadata and hashes:

1. Intent and overlay hashes.
2. Revision identities and expected outcomes.
3. Actual outcomes and exit codes.
4. Output, invocation, and runner attestation hashes.
5. Mutation-gate status.
6. Final proof verdict and capsule hash.
7. A local HMAC attestation over a domain-separated capsule bundle.

A cryptographically valid confirmed capsule may mark its linked finding as grounded. The reconciler then prevents that finding from being rejected or waived. `not-reproduced` and `inconclusive` capsules do not clear a finding.

Capsule reconstruction verifies the recorded fields, relation, revision hashes, mutation gate, verdict, and content hash. Reconciliation additionally verifies a local HMAC signature using `AUTOFUSION_PROOF_ATTESTATION_KEY` and the configured active key ID. An unsigned capsule, a wrong key, an inactive key ID, or a modified signed bundle fails closed.

The local key must contain at least 32 bytes. It remains in the Autofusion process environment and is never sent to the proof container or persisted in an artifact. This gate prevents an arbitrary self-hashed JSON document from affecting reconciliation. It does not prove external runner identity, protect against a fully compromised local process, provide managed key custody, or replace durable production execution logs.

## CLI workflow

Hash each immutable revision tree:

~~~text
autofusion proof-hash --root ./base-snapshot
autofusion proof-hash --root ./head-snapshot
autofusion proof-hash --root ./mutant-snapshot
~~~

Create an intent that conforms to [the verification intent schema](../schemas/proof-intent.schema.json), place the independent test overlay under an approved path, and run:

Before execution, load `AUTOFUSION_PROOF_ATTESTATION_KEY` from operator-controlled secure storage. The CLI checks that the key exists before it detects or starts a proof runner.

~~~text
autofusion prove \
  --repo . \
  --intent intent.json \
  --overlay proof-overlay \
  --revision base=base-snapshot \
  --revision head=head-snapshot \
  --revision mutant=mutant-snapshot \
  --output proof-capsule.json
~~~

Attach one or more verified capsules during reconciliation:

~~~text
autofusion finalize RUN_ID \
  --dispositions dispositions.json \
  --proof-capsule proof-capsule.json
~~~

## Current boundary

The alpha runtime validates and executes supplied intents and overlays. It does not yet ask a model to author the proof test automatically. It also does not prove that two human-readable author labels correspond to cryptographically attested people or agents. Those are separate provenance milestones.
