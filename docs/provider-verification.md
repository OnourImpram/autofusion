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

## ChatGPT Web smoke through Agent Web Bridge: 13 September 2026

One autofusion `call` used the `chatgpt-web-high` profile over the OpenAI-compatible transport
with `structured_output: prompt` against a loopback Agent Web Bridge (127.0.0.1:8765), which
drove the operator's signed-in ChatGPT Pro tab in the operator's own Chrome. The request was a
public one-line toy prompt with a two-field schema and no tools; the packet repository held one
toy function.

The call completed in 14,216 ms with schema-valid output (`{"answer": "PONG"}`,
output hash `348d1bd5fa0d11929cad2f72f910fea304d579215856dee744d63701e35ec95e`). The bridge's
reply reports the requested UI route as `model`, so `identity_evidence` is `configured-route`,
not an observed backend identity. Usage is not reported by the bridge (`usage_source: none`).
The bridge answers a plain `response_format` request with 400 `unsupported_output_contract`;
that is why the schema rides in the prompt. A first attempt on the same day stopped at the
bridge's `prompt_integrity` check before any send: the bridge typed the prompt through CDP and
ChatGPT's editor rewrote it; the bridge now inserts through the browser's plain-text editing
command and the smoke above followed.

This is operational telemetry for one bounded call, not a claim about ChatGPT's terms, quota
or model identity. The ChatGPT web session and the Codex CLI share no quota: on the same day the
Codex CLI route reported its usage limit reached until 19 September 2026 while this route answered.

## 2026-11-15 model generation

Version 1.0.0 moves the built-in profiles to the model generation of 15 November 2026. This section records that move and the live smokes that followed it. No provider was contacted while the configuration was changed. The smokes were run afterwards, on 15 November 2026: one bounded `autofusion call` per profile, with a public toy addition function inline in the prompt, no tools requested, and the packaged reviewer output schema. The [bounded smoke record](provider-smoke-20261115.json) stores metadata and hashes only; it contains neither the prompt nor provider output. `call` prints the effective model after the adapter's identity check; it does not print the evidence class, so these rows do not say whether an identity was observed in the provider stream or is the configured route. The July and September measurements above keep their dates and the model names they measured; they are not evidence for the model ids below.

| Handle | Model id | Transport | Shipped state | Smoke state |
| --- | --- | --- | --- | --- |
| `gpt-sol` | `gpt-6.1-sol` at xhigh | codex-exec | enabled | completed in 19,057 ms, schema-valid, effective `gpt-6.1-sol`, Codex CLI 0.169.0 |
| `gpt-sol-ultra` | `gpt-6.1-sol` at ultra, compound | codex-exec | enabled | completed in 16,279 ms, schema-valid, effective `gpt-6.1-sol` |
| `gpt-terra` | `gpt-6.1-terra` at xhigh | codex-exec | enabled after this smoke | completed in 13,564 ms, schema-valid, effective `gpt-6.1-terra` |
| `gpt-luna` | `gpt-6-luna` at xhigh | codex-exec | enabled after this smoke | completed in 11,338 ms, schema-valid, effective `gpt-6-luna` |
| `claude-opus` | alias `opus`, canonical `claude-opus-5-5` at xhigh | claude-exec | enabled | completed in 24,264 ms, schema-valid, effective `claude-opus-5-5`, Claude Code 2.1.325 |
| `gemini-flash` | `gemini-3.8-flash-high` | agy-headless | enabled after this smoke | completed in 8,710 ms, schema-valid, effective `gemini-3.8-flash-high`, `agy` 1.5.9 (earlier smoke rejected: no terminal model identity) |
| `gemini-argon` | `gemini-4-argon-high` | agy-headless | enabled after this smoke | completed in 12,775 ms, schema-valid, effective `gemini-4-argon-high`, `agy` 1.5.9 (first smoke rejected: no terminal model identity) |
| `gemini-carbon` | `gemini-4-carbon-high` | agy-headless | enabled after this smoke | completed in 35,534 ms, schema-valid, effective `gemini-4-carbon-high`, `agy` 1.5.9 (first smoke rejected: no terminal model identity) |
| `gemini-hydrogen` | `gemini-4-hydrogen-xhigh` | agy-headless | enabled after this smoke | completed in 16,816 ms, schema-valid, effective `gemini-4-hydrogen-xhigh`, `agy` 1.5.9 (first smoke rejected: no terminal model identity) |
| `grok` | `grok-4.7` | acp | disabled, gate `main-session-verified-acp-permissions-and-identity-smoke` | NOT_RUN: the ACP preflight refused to start, no model call was made (grok CLI 1.0.40) |

Unchanged by this move: `astra-ultra` (`gpt-6-astra`, measured 8 September 2026 as recorded above and again on 15 November 2026: completed in 17,314 ms, schema-valid), and the `chatgpt-web-high` and `chatgpt-web-xhigh` profiles.

`gpt-terra` and `gpt-luna` were first smoked in a scratch copy of the configuration with the profiles enabled (9,233 ms and 11,920 ms, both schema-valid), then enabled in the shipped defaults and smoked again; the table shows the second run. On the first 15 November 2026 run, the three Gemini 4 smokes ran against the same kind of scratch copy, because a disabled profile outside the model allowlist cannot be called. With `agy` 1.5.9 in print mode (`--output-format stream-json`), each call emitted an `init` event, `step_update` events and one `result` event, and the adapter of that moment rejected each result: the terminal `result` event carries a status, a response and usage, and no model field. The only place `agy` 1.5.9 names the model is its session `init` event (`init.model`), and an unknown model id is refused there with an explicit error instead of being replaced; `init` and `result` carry the same `conversation_id`. At the time of that first smoke, the adapter required terminal identity and did not accept the `init` event in its place, so the profiles initially stayed disabled (`gemini-flash` had also been disabled after its earlier smoke returned no terminal identity, and `agy` 1.1.27 does not list the Gemini 4 ids and fails with `model_unavailable`). `src/autofusion/providers/agy.py` was then updated to accept `init.model` as observed identity (evidence class `provider-response`) only when a single `init` event and the terminal `result` carry the same non-empty string `conversation_id` and `init.model` is a non-empty string, comparing it with the canonical model as before; it still refuses missing `init`, `init` without a model, `init` or `result` without `conversation_id`, different `conversation_id` values, multiple `init` events, non-string or empty values, or a terminal `result` naming a model different from the session model (a terminal `result` naming the model itself still identifies the call). Before the shipped defaults were changed, all four Gemini profiles passed a bounded smoke from a scratch copy with the updated adapter (`gemini-hydrogen` in 15,937 ms, `gemini-carbon` in 33,541 ms, `gemini-argon` in 10,772 ms and `gemini-flash` in 21,956 ms, all schema-valid), and a live negative control on a scratch copy claiming `gemini-4-carbon-high` for a call served with `gemini-4-hydrogen-xhigh` was refused with `provider identity mismatch: expected gemini-4-carbon-high, received gemini-4-hydrogen-xhigh`. `gemini-flash`, `gemini-argon`, `gemini-carbon` and `gemini-hydrogen` now ship enabled with no activation gate, in `guardrails.model_allowlist`, and with `identity_source: session-init-model` (`.fusion.example.json` follows for `gemini-flash`), and were smoked again against the shipped defaults on 15 November 2026 (`gemini-hydrogen` 16,816 ms, `gemini-carbon` 35,534 ms, `gemini-argon` 12,775 ms and `gemini-flash` 8,710 ms, all schema-valid with effective model equal to the canonical model; the table shows this shipped-defaults run).

The Grok smoke made no model call. The ACP preflight refuses to spawn the peer while a user-level MCP configuration file exists, and one existed on the smoke machine. That is the preflight working as designed; the route itself remains unmeasured for `grok-4.7`.

The context windows of `gpt-sol`, `gpt-sol-ultra`, `gpt-terra`, `gpt-luna` and `claude-opus` are carried unchanged from the profile each one derives from and say so in `context_window_source`: `carried from <previous id> on 2026-11-15; NOT_VERIFIED for <new id>`. No vendor page was opened for the new ids. The smoke that activates a profile must also replace its carried window with a verified one, or leave it labelled.

The `openai-chatgpt` quota group now lists `gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-luna` and `gpt-6-astra`. Its recorded source is the operator statement that four ids answered on one account on 2026-11-15; that is not a smoke record of this repository. The self identities added in 1.0.0 (Opus 5.5, Sonnet 5.5, Haiku 5.5, Fable 5.2, Mythos 5.2) are session identities attested at run time, not callable routes, so no smoke applies to them. The 8 September 2026 policy paragraph above names the identities of that date; the current expectation for the Claude CLI `opus` route is `claude-opus-5-5`.
