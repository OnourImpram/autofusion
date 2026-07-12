"""HMAC signatures for portable policy bundles without secret persistence."""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass

from autofusion.errors import PolicyError
from autofusion.util import JsonObject, canonical_json_bytes, sha256_json

_ALGORITHM = "hmac-sha256"
_VERSION = 1
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class PolicyBundleSignature:
    """Public verification material. The signing key is deliberately absent."""

    key_id: str
    bundle_hash: str
    signature: str
    algorithm: str = _ALGORITHM
    version: int = _VERSION

    def as_json(self) -> JsonObject:
        return {
            "version": self.version,
            "algorithm": self.algorithm,
            "key_id": self.key_id,
            "bundle_hash": self.bundle_hash,
            "signature": self.signature,
        }


def _signature_payload(key_id: str, bundle_hash: str) -> bytes:
    return canonical_json_bytes(
        {
            "version": _VERSION,
            "algorithm": _ALGORITHM,
            "key_id": key_id,
            "bundle_hash": bundle_hash,
        }
    )


def _secret_bytes(secret: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(secret, (bytes, bytearray, memoryview)):
        raise PolicyError("policy signing key must be bytes-like")
    material = bytes(secret)
    if len(material) < 32:
        raise PolicyError("policy signing key must contain at least 32 bytes")
    return material


def sign_policy_bundle(
    bundle: Mapping[str, object], *, key_id: str, signing_key: bytes | bytearray | memoryview
) -> PolicyBundleSignature:
    """Sign a canonical policy bundle and return only public signature metadata."""

    if not _KEY_ID.fullmatch(key_id):
        raise PolicyError("policy signing key ID is invalid")
    bundle_hash = sha256_json(dict(bundle))
    signature = hmac.new(
        _secret_bytes(signing_key), _signature_payload(key_id, bundle_hash), hashlib.sha256
    ).hexdigest()
    return PolicyBundleSignature(key_id=key_id, bundle_hash=bundle_hash, signature=signature)


def verify_policy_bundle(
    bundle: Mapping[str, object],
    signature: PolicyBundleSignature,
    verification_keys: Mapping[str, bytes | bytearray | memoryview],
    *,
    revoked_key_ids: frozenset[str] | set[str] = frozenset(),
    allowed_key_ids: frozenset[str] | set[str] | None = None,
) -> PolicyBundleSignature:
    """Fail closed for wrong, inactive, revoked, or mismatched signing material."""

    if signature.version != _VERSION or signature.algorithm != _ALGORITHM:
        raise PolicyError("unsupported policy signature algorithm or version")
    if signature.key_id in revoked_key_ids:
        raise PolicyError("policy signature key is revoked")
    if allowed_key_ids is not None and signature.key_id not in allowed_key_ids:
        raise PolicyError("policy signature key is not active")
    signing_key = verification_keys.get(signature.key_id)
    if signing_key is None:
        raise PolicyError("policy signature key is unavailable")
    bundle_hash = sha256_json(dict(bundle))
    if not hmac.compare_digest(bundle_hash, signature.bundle_hash):
        raise PolicyError("policy bundle hash does not match its signature")
    expected = hmac.new(
        _secret_bytes(signing_key),
        _signature_payload(signature.key_id, bundle_hash),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature.signature):
        raise PolicyError("policy signature verification failed")
    return signature
