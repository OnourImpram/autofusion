from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from autofusion.config import FusionConfig, load_config
from autofusion.dlp import DlpPolicy, preflight_packet, preflight_text
from autofusion.engine import FusionEngine, FusionRunRequest
from autofusion.errors import PolicyError
from autofusion.models import ModelProfile, ProviderRequest, ProviderResult
from autofusion.providers.base import SelfProvider
from autofusion.providers.fake import DeterministicFakeProvider
from autofusion.registry import ProviderRegistry
from autofusion.util import JsonObject, sha256_json

_BODY = "SYNTHETIC_CREDENTIAL_BODY_42"
_TAIL = "SYNTHETIC_CREDENTIAL_TAIL_84"


def _pem(label: str = "PRIVATE KEY", delimiter: str = "-----") -> str:
    return (
        f"{delimiter}BEGIN {label}{delimiter}\n{_BODY}\n{_TAIL}\n"
        f"{delimiter}END {label}{delimiter}"
    )


def _nested_pem() -> str:
    return (
        f"-----BEGIN PRIVATE KEY-----\n{_BODY}\n{_pem('RSA PRIVATE KEY')}\n"
        f"{_TAIL}\n-----END PRIVATE KEY-----"
    )


@pytest.mark.parametrize(
    "value",
    [
        *(_pem(label) for label in (
            "PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY", "DSA PRIVATE KEY",
            "OPENSSH PRIVATE KEY", "ENCRYPTED PRIVATE KEY",
        )),
        _pem(delimiter="----"),
        _pem(delimiter="-"),
        _pem(delimiter=""),
        _pem().replace("END PRIVATE KEY", "END RSA PRIVATE KEY"),
        _pem().replace("\n", r"\n"),
        _pem().replace("\n", r"\r\n"),
        _pem().replace("-----END PRIVATE KEY-----", ""),
        f"-----BEGIN PRIVATE KEY\n{_BODY}\n{_TAIL}",
        f"----BEGIN PRIVATE----\n{_BODY}\n{_TAIL}",
    ],
    ids=[
        "pkcs8", "rsa", "ec", "dsa", "openssh", "encrypted", "four-dashes", "one-dash",
        "bare-markers", "mismatched-footer", "escaped-lf", "escaped-crlf", "missing-footer",
        "truncated-header", "truncated-label",
    ],
)
def test_private_key_redaction_consumes_every_body_segment(value: str) -> None:
    result = preflight_text(value)
    assert _BODY not in result.text
    assert _TAIL not in result.text
    assert result.matches
    assert preflight_text(result.text).text == result.text
    flagged = preflight_text(value, DlpPolicy(action="flag"))
    assert flagged.text == value
    assert flagged.matches
    with pytest.raises(PolicyError, match="DLP"):
        preflight_text(value, DlpPolicy(action="block"))


def test_complete_private_keys_preserve_surrounding_nonsecret_text() -> None:
    text = f"public prefix\n{_pem()}\npublic suffix\n{_pem('RSA PRIVATE KEY')}\nend"
    redacted = preflight_text(text).text
    assert redacted == "public prefix\n[REDACTED]\npublic suffix\n[REDACTED]\nend"


@pytest.mark.parametrize(
    "value",
    [
        f"API_KEY={_BODY}",
        f'"api_key": "{_BODY}"',
        f"'token': '{_BODY}'",
        f'password="short {_BODY} {_TAIL}"',
        f'password="start\\"{_BODY} {_TAIL}"',
        f'password="start\n{_BODY}\n{_TAIL}"',
        f'password="start {_BODY} {_TAIL}',
        f"password: short {_BODY} {_TAIL}",
        f'client_secret={{"first":"{_BODY}","nested":{{"last":"{_TAIL}"}}}}',
        f'secret=["{_BODY}",{{"last":"{_TAIL}"}}]',
        f'secret={{"first":"{_BODY}","last":"{_TAIL}"',
        f'secret={{"first":"{_BODY}","last":"{_TAIL}"]',
        f'access_token="{_BODY}"',
        f'refresh-token="{_BODY}"',
    ],
    ids=[
        "environment", "json-key", "single-quoted-key", "quoted-spaces", "escaped-quote",
        "multiline-quoted", "unclosed-quote", "unquoted-spaces", "nested-object", "array",
        "truncated-object", "mismatched-close", "access-token", "refresh-token",
    ],
)
def test_credential_assignment_redacts_the_complete_value(value: str) -> None:
    result = preflight_text(value)
    assert _BODY not in result.text
    assert _TAIL not in result.text
    assert result.matches
    assert preflight_text(result.text).text == result.text
    with pytest.raises(PolicyError, match="DLP"):
        preflight_text(value, DlpPolicy(action="block"))


@pytest.mark.parametrize(
    "value",
    [f"password={_BODY},{_TAIL}", f"password={_BODY}]{_TAIL}", f"password={_BODY}\\\n{_TAIL}"],
    ids=["comma", "closing-bracket", "continued-line"],
)
def test_unquoted_credential_punctuation_and_continuations_leave_no_tail(value: str) -> None:
    redacted = preflight_text(value).text
    assert _BODY not in redacted
    assert _TAIL not in redacted


@pytest.mark.parametrize(
    "value",
    [
        f'password="""{_BODY}\n{_TAIL}"""',
        f"password='''{_BODY}\n{_TAIL}'''",
        f"password='{_BODY}''{_TAIL}'",
        f'password="{_BODY}"{_TAIL}',
        f'password="{_BODY}" + "{_TAIL}"',
        f"password: |\n  {_BODY}\n  {_TAIL}",
        f"password: >-\n  {_BODY}\n  {_TAIL}",
        f'password=r"""{_BODY}\n{_TAIL}"""',
    ],
    ids=["triple-double", "triple-single", "yaml-quote", "shell-concat", "python-concat",
         "yaml-literal", "yaml-folded", "raw-triple"],
)
def test_multiline_and_concatenated_credential_literals_leave_no_tail(value: str) -> None:
    redacted = preflight_text(value).text
    assert _BODY not in redacted
    assert _TAIL not in redacted


def test_incomplete_private_key_footer_does_not_end_redaction_early() -> None:
    value = (
        f"-----BEGIN PRIVATE KEY-----\n{_BODY}\nEND PRIVATE INFO\n{_TAIL}\n"
        "-----END PRIVATE KEY-----\npublic suffix"
    )
    redacted = preflight_text(value).text
    assert _BODY not in redacted
    assert _TAIL not in redacted
    assert redacted.endswith("public suffix")


def test_nested_private_key_markers_cannot_leave_an_outer_body_tail() -> None:
    redacted = preflight_text(_nested_pem()).text
    assert _BODY not in redacted
    assert _TAIL not in redacted


@pytest.mark.parametrize(
    "value",
    [f"-----BEGIN PRIVATE\nKEY-----\n{_BODY}\n{_TAIL}",
     f'token=[REDACTED]{_BODY}', f'password="[REDACTED]"{_BODY}',
     r'{\"api_key\":\"' + _BODY + r'\"}'],
    ids=["split-header", "sentinel-unquoted-tail", "sentinel-quoted-tail", "escaped-json"],
)
def test_fragmented_credential_syntax_does_not_exempt_the_body(value: str) -> None:
    assert _BODY not in preflight_text(value).text


def test_ordinary_private_review_prose_is_not_a_key_block() -> None:
    text = "Begin privately reviewing the public API. Preserve ordinary prose."
    assert preflight_text(text).text == text


@pytest.mark.parametrize("case", ["header", "nested"])
def test_large_malformed_credential_documents_are_bounded(case: str) -> None:
    code = (
        "from autofusion.dlp import preflight_text\n"
        "import sys\n"
        "value = ('BEGIN ' + ' ' * 100000 + 'PUBLIC') if sys.argv[1] == 'header' "
        "else ('secret={' * 10000 + 'x')\n"
        "preflight_text(value)\n"
        "print('processed')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code, case], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=4, check=True,
    )
    assert completed.stdout.strip() == "processed"


def test_structured_credentials_are_redacted_without_losing_other_fields() -> None:
    payload: JsonObject = {
        "api_key": _BODY,
        "items": [{"password": f"short {_TAIL}", "name": "public"}],
        "secret": {"first": _BODY, "nested": [_TAIL]},
        "max_tokens": 100,
        "public": "keep",
    }
    result = preflight_packet(payload)
    assert _BODY not in json.dumps(result.payload)
    assert _TAIL not in json.dumps(result.payload)
    assert result.payload == {
        "api_key": "[REDACTED]",
        "items": [{"password": "[REDACTED]", "name": "public"}],
        "secret": "[REDACTED]",
        "max_tokens": 100,
        "public": "keep",
    }
    assert preflight_packet(result.payload).payload == result.payload
    serialized = json.dumps(result.payload)
    assert preflight_text(serialized).text == serialized
    with pytest.raises(PolicyError, match="DLP"):
        preflight_packet(payload, DlpPolicy(action="block"))
    assert preflight_packet(payload, DlpPolicy(action="flag")).payload == payload


def test_claiming_to_be_a_schema_does_not_exempt_credential_data() -> None:
    result = preflight_packet({"$schema": "https://example.invalid/schema", "token": _BODY})
    assert _BODY not in str(result.payload)


def test_numeric_json_counters_are_not_credential_values() -> None:
    payload = {"dlp": {"match_counts": {"private-key": 1, "bearer-token": 2}}}
    assert preflight_packet(payload).payload == payload
    encoded = json.dumps(payload)
    assert preflight_text(encoded).text == encoded


@dataclass
class CapturingProvider:
    profile: ModelProfile
    requests: list[ProviderRequest] = field(default_factory=list)

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        self.requests.append(request)
        output: JsonObject = {
            "summary": "reviewed",
            "coverage": ["artifact"],
            "blind_spots": [],
            "recommendation": "ship",
            "findings": [],
        }
        return DeterministicFakeProvider(self.profile, output).invoke(request)


def _packet_config(action: str = "redact") -> FusionConfig:
    return load_config(overrides={
        "models": {"gpt-sol": {"context": "packet", "transport": "openai-compatible"}},
        "guardrails": {"sensitive_data_action": action},
    })


@pytest.mark.parametrize(
    "value", [_pem(), _pem(delimiter="---"), _pem().split("-----END")[0], _nested_pem()],
    ids=["complete", "malformed", "truncated", "nested"],
)
def test_packet_only_dispatch_cannot_receive_private_key_body(tmp_path: Path, value: str) -> None:
    config = _packet_config()
    provider = CapturingProvider(config.model("gpt-sol"))
    registry = ProviderRegistry({
        "self": SelfProvider(config.model("self")), "gpt-sol": provider,
    }, config=config)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "selected.txt").write_text(value, encoding="utf-8")
    FusionEngine(config, registry, work_root=tmp_path / "work").run(FusionRunRequest(
        task="Review selected artifact", repo_root=repo, artifact_kind="plan",
        artifact_paths=("selected.txt",), preset="fast", self_model="claude-opus-5",
        run_grounding=False,
    ))
    assert len(provider.requests) == 1
    assert _BODY not in provider.requests[0].prompt
    assert _TAIL not in provider.requests[0].prompt
    encoded = provider.requests[0].prompt.split("<autofusion_packet>\n", 1)[1].split(
        "\n</autofusion_packet>", 1
    )[0]
    packet = json.loads(encoded)
    assert sha256_json(packet) == provider.requests[0].metadata["packet_hash"]


@pytest.mark.parametrize("action", ["redact", "block"])
def test_schema_property_names_keep_grammar_but_schema_strings_obey_dlp(
    tmp_path: Path, action: str,
) -> None:
    config = _packet_config(action)
    provider = CapturingProvider(config.model("gpt-sol"))
    registry = ProviderRegistry({"gpt-sol": provider}, config=config)
    schema: JsonObject = {
        "type": "object",
        "properties": {
            "token": {"type": "string"},
            "api_key": {"type": "string", "description": "public description"},
        },
    }
    request = ProviderRequest("run", "call", "gpt-sol", "Review", schema, tmp_path, 1, 2000)
    registry.invoke(request)
    assert provider.requests[0].response_schema == schema
    schema["description"] = f'"password": "short {_BODY}"'
    if action == "block":
        with pytest.raises(PolicyError, match="DLP"):
            registry.invoke(request)
        assert len(provider.requests) == 1
    else:
        registry.invoke(request)
        captured = provider.requests[-1].response_schema
        assert captured["properties"] == schema["properties"]
        assert _BODY not in json.dumps(captured)
        Draft202012Validator.check_schema(captured)
