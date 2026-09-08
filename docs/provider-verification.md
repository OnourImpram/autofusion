# Provider verification

This record contains bounded provider metadata only. It does not persist prompts, model output, credentials, private source, or subscription identifiers.

## Historical measurement: 17 July 2026

The verification environment downloaded v0.5.0-alpha.1 wheel and source distribution assets from GitHub and matched them against the published SHA256 checksums before execution. The source commit was c1c06fc0d9ff9d2bfc7458c7876998a687e8e2c3.

| Handle | Claude Code | Requested route | Effective model | Duration | Result |
| --- | --- | --- | --- | ---: | --- |
| claude-opus | 2.1.212 | opus, xhigh | claude-opus-4-8 | 136133 ms | completed, ship, zero findings |
| claude-fable | 2.1.212 | fable, xhigh | claude-fable-5 | 64865 ms | completed, ship, zero findings |

The Opus and Fable calls used the published autofusion call path, published reviewer schema, read-only Claude tools, no session persistence, and the adapter's canonical identity checks. The Fable pass satisfied the activation gate that applied to this authenticated operator environment in July. That callable policy is now superseded.

## Current policy: 8 September 2026

Fable 5.1 and legacy Fable 5 are permitted only as the active self session. No callable Fable profile, alias, overlay, or external role is admitted, including disabled profiles. Replace `claude-fable` with `claude-opus`, `dual-fable` with `dual-opus`, and `external-council-fable` with `external-council` in legacy configuration. Current defaults expect `claude-opus-5` for the Claude CLI `opus` route and allow Opus 5, Sonnet 5, Haiku 4.5, and Fable 5.1 as validated self identities. The July measurements above are not September model verification.

## Historical review boundary: July 2026

A broad private-source re-review was not sent to Anthropic without separate source-transfer approval. The provider identity and policy closure set passed 35 targeted tests, Ruff, and strict mypy locally on the exact source distribution. Earlier independent review findings remain linked to the alpha.1 release notes and were closed by the tested identity invariants.

This evidence is operational telemetry, not cryptographic provider attestation and not a claim of universal model availability.

## Astra adapter smoke: 8 September 2026

One autofusion `ProviderRegistry` invocation through `CodexExecAdapter` used installed Codex CLI 0.153.4 and the existing ChatGPT session. The request contained only a public toy addition function, requested no tools, and used the packaged reviewer output schema. The profile retained read-only sandbox, ephemeral execution, ignored user config and ignored rules. No login was performed.

The configured `gpt-6-astra` route at `ultra` completed in 15,692 ms with schema-valid reviewer output. Usage reported 21,941 input tokens and 240 output tokens. The JSONL stream did not expose model identity: `observed_model` is null and `identity_evidence` is `configured-route`. This verifies the bounded configured adapter route, not an independently observed model identity, provider bill, or visibility into compound workers.

The [bounded smoke record](provider-astra-smoke-20260908.json) stores metadata and hashes only. It contains neither the prompt nor provider output. Sol, Astra, and Terra share the declared `openai-chatgpt` quota group; moving between them does not avoid shared exhaustion.

The [official non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode) describes the JSONL and structured output interface. The [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) does not list ultra among its effort values; support here is scoped to the installed CLI and this dated smoke, with capability rejection retained for unproven adapters.
