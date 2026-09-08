# Security

autofusion sends artifacts you select to model providers you configure, runs verification commands
you approve, and writes metadata-only receipts. Its security posture rests on four measured
invariants, each with a test that fails when the invariant is removed (see the mutation notes in
`CHANGELOG.md`):

1. **The active session is never callable.** `self` is a sentinel; no Python path can invoke it, and
   `claude-fable-5-1` is permitted only as `self`, never as a reviewer, judge, delegate target or
   alias.
2. **Reviewer text cannot execute commands.** Grounding runs only the verification commands the
   repository declares and you approve; generated tests run only in the Docker proof runner with
   network denied, a read-only root and dropped capabilities.
3. **Admission is uniform.** Every dispatched participant passes the same role, model, provider,
   callable, compound and redaction checks; guardrails can be tightened by local configuration and
   never relaxed.
4. **Credentials do not travel.** Packets are scanned before dispatch; a complete PEM block, an API
   key or a token shape blocks the call under the default policy. Receipts carry hashes and metadata,
   not prompts or model output.

## Reporting a vulnerability

Open a private security advisory on GitHub, or write to the maintainer address in `pyproject.toml`.
Please include the autofusion version, the provider transport involved, and a reproduction that
does not contain live credentials. You will get an acknowledgement, a severity assessment and, for
a confirmed defect, a fix with a regression test and a changelog entry that names the class of the
defect.

## What this file does not claim

No benchmark of decision quality, no guarantee about a provider's handling of the data it receives,
and no assurance about the models themselves. Transport smokes are dated in
`docs/provider-verification.md`; a smoke is evidence that a route answered on that date, not that
it is safe for your data. Read your providers' terms.
