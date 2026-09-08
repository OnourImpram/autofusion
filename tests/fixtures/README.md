# Contract fixtures

The positive analysis, receipt, and trusted-attestation fixtures are synthetic
current-contract examples migrated together on 2026-09-08. Their timestamps and
outcomes are test data, not live provider measurements. Current self identity is
`claude-opus-5`.

The self-identity hash is SHA-256 of its attestation object serialized with sorted
keys and compact JSON separators. It is referenced by both positive artifacts.
The receipt analysis hash is SHA-256 of the exact `analysis-valid.json` bytes.
Other repeated-character hashes are deliberately synthetic identifiers.

The validators retain `claude-fable-5` as a deliberately disallowed external
identity. The named historical Claude envelope test preserves `claude-opus-4-8`
as July-format parsing coverage, without admitting it to current default self
identities.
