# Autofusion runtime completion checklist

Date: 2026-07-12

## Remote-first constraints

- [x] Keep work out of `C:\Users\onuri\autofusion`.
- [x] Use the temporary GitHub archive tree only for staging and validation.
- [ ] Publish changes through GitHub Git Data API, not local git push.

## Implementation phases

- [x] Lock the current remote baseline and staging path.
- [x] Finish runtime engine, schema parity, and safety hardening.
- [x] Finish CLI, plugin, and documentation surfaces.
- [x] Finish evaluation, replay, drift, and reporting surfaces.
- [ ] Run ruff, strict mypy, full pytest with coverage threshold, package build, and CLI smoke checks.
- [ ] Run adversarial verification against the remote branch artifact.
- [ ] Open PR, pass CI, merge, delete branch, and clean temporary state.

## Review criteria

- [ ] No secrets or private data committed.
- [x] No false claim that `self` is callable from Python.
- [x] GPT model defaults expose GPT 5.6 SOL and GPT 5.6 SOL Ultra.
- [x] Claude options expose Opus and Fable without hard-coding one vendor path.
- [x] Receipts degrade honestly on DLP, budget, provider, or grounding failure.
