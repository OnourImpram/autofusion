# Session delegate review

The main Claude Code session can carry frozen review requests to registered
`gemini-delege`, `grok-delege`, `sol-delege`, `astra-delege`, or `terra-delege`
agents. Python prepares and validates artifacts. It never invokes the session,
launches a delegate, or selects a replacement for a missing delegate.

```sh
autofusion session-export --repo . --kind plan --path plan.md \
  --task "Review this plan" --preset fast --self-model claude-fable-5-1 \
  --delegate gpt-sol=sol-delege --output .fusion/requests.json
```

Use the actual active session identity for `--self-model`. The example permits
Fable as self. Both Fable identities are forbidden as callable reviewers and
delegate targets, including aliases and returned effective identities.

Export uses the configured panel, admission policy, DLP and context bounds.
Delegate mappings are `configured-handle=registered-delegate`. Registration
depends on the current router snapshot. Check the available agents in the main
session; a name in a file does not establish availability. Omitted mappings
remain missing calls. Additional models or panels require normal permitted
configuration. A dual panel needs two distinct admitted reviewer models.

For each dispatchable request, the main session supplies that request's prompt
and reviewer schema to its named agent. First passes are blind: no peer response
is included in a prompt. Use only the prepared packet. Do not edit the artifact,
execute reviewer-supplied commands, or give the agent authority to reconcile.
The main session controls delegate tool access. The bridge does not establish a
sandbox around a registered agent.

At [claude-oauth d8404b0](https://github.com/OnourImpram/claude-oauth/blob/d8404b0/src/supervisor/launcher.ts),
Google and xAI delegates inherit parent tools without a turn cap; OpenAI
delegates carry built-in tools plus `Skill`. Read-only behavior cannot be inferred
from a delegate name. Native gateway IDs and patched shadow aliases are routing
names, distinct from the effective model identity for the individual call.

Save each response as a `session-result.schema.json` envelope. Copy these
correlation fields verbatim from its own request: `schema_version`, `run_id`,
`call_id`, `packet_hash`, `artifact_manifest_hash`, `reviewer_schema_id`, and
`reviewer_schema_hash`. Set `status`, `actual_identity`, `output`, and `error`
from that call's outcome. `actual_identity` contains `model`, `vendor`, `family`,
and `delegate`. Obtain it from the invocation's identity evidence; never copy the
requested identity into an observed identity or read whichever router receipt
was written last. If identity is unavailable, preserve an incomplete outcome.

```sh
autofusion session-import RUN_ID --repo . --result .fusion/result.json
autofusion session-complete RUN_ID --repo .
autofusion finalize RUN_ID --repo . --dispositions .fusion/dispositions.json
```

Store request, response and disposition files outside the reviewed tree or under
the snapshot-excluded `.fusion/` directory. Use the same configuration and
`--work-root` throughout. Import rejects wrong
run or call IDs, different packets, manifests or schemas, already consumed
calls, replayed results, and identity overrides. A correlated invalid reviewer
payload retains a malformed status, error and hash of the rejected envelope.
Missing, failed, cancelled and abstaining
outcomes remain visible in the bridge records; existing analysis and receipt
statuses summarize incomplete calls without claiming a successful review.

Complete closes outstanding calls as missing and feeds the results into the
same attestation, analysis, grounding and pending-reconciliation stages used by
direct transports. Inspect the returned analysis, reconcile every finding, and
use ordinary `finalize`. Grounding and proof execution remain Python policy
decisions. A successful import alone is not a fused verdict.

Local hash bindings prevent accidental correlation errors and detect edits to
bound state. They do not independently authenticate model telemetry supplied by
the main session. The session operator is the ingress trust boundary. Portable
tests measure Python validation and evidence behavior; live delegate execution
needs separate verification in a registered Claude Code session.
