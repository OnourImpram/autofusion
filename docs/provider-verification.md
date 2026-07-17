# Provider verification

This record contains bounded provider metadata only. It does not persist prompts, model output, credentials, private source, or subscription identifiers.

## 17 July 2026

The verification environment downloaded v0.5.0-alpha.1 wheel and source distribution assets from GitHub and matched them against the published SHA256 checksums before execution. The source commit was c1c06fc0d9ff9d2bfc7458c7876998a687e8e2c3.

| Handle | Claude Code | Requested route | Effective model | Duration | Result |
| --- | --- | --- | --- | ---: | --- |
| claude-opus | 2.1.212 | opus, xhigh | claude-opus-4-8 | 136133 ms | completed, ship, zero findings |
| claude-fable | 2.1.212 | fable, xhigh | claude-fable-5 | 64865 ms | completed, ship, zero findings |

The Opus and Fable calls used the published autofusion call path, published reviewer schema, read-only Claude tools, no session persistence, and the adapter's canonical identity checks. The Fable pass satisfies the activation gate for this authenticated operator environment only.

## Portable-default decision

Fable remains disabled in .fusion.example.json and the packaged default configuration. Subscription entitlement, usage credits, and provider routing vary by operator. Each installation must enable Fable in an operator-controlled overlay only after its own canonical identity smoke reports effective_model=claude-fable-5.

## Review boundary

A broad private-source re-review was not sent to Anthropic without separate source-transfer approval. The provider identity and policy closure set passed 35 targeted tests, Ruff, and strict mypy locally on the exact source distribution. Earlier independent review findings remain linked to the alpha.1 release notes and were closed by the tested identity invariants.

This evidence is operational telemetry, not cryptographic provider attestation and not a claim of universal model availability.
