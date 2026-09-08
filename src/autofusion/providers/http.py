"""Injectable HTTPS transports for OpenAI-compatible and Anthropic APIs."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from autofusion.errors import OutputValidationError, ProviderError
from autofusion.identity import assert_executable_identity
from autofusion.models import ModelProfile, ProviderRequest, ProviderResult
from autofusion.providers.base import (
    CostResolver,
    assert_effective_identity,
    build_result,
    decode_json_object,
    no_cost_evidence,
)
from autofusion.util import JsonObject, canonical_json_bytes


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes


class HttpClient(Protocol):
    """Narrow HTTP seam used by provider tests and production transports."""

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout_s: float,
        max_bytes: int,
    ) -> HttpResponse:
        """Send one HTTPS POST request without logging sensitive headers."""


@dataclass(frozen=True, slots=True)
class UrllibHttpClient:
    """Standard-library HTTP client with explicit timeout and no retry side effects."""

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout_s: float,
        max_bytes: int,
    ) -> HttpResponse:
        if max_bytes < 1:
            raise ProviderError("provider HTTP response limit must be positive")
        _validate_endpoint(url)
        request = Request(url, data=body, headers=dict(headers), method="POST")
        opener = build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=timeout_s) as response:
                payload = response.read(max_bytes + 1)
                if len(payload) > max_bytes:
                    raise ProviderError("provider HTTP response exceeded configured limit")
                return HttpResponse(status=int(response.status), body=payload)
        except HTTPError as exc:
            payload = exc.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise ProviderError(
                    "provider HTTP error response exceeded configured limit"
                ) from exc
            return HttpResponse(status=exc.code, body=payload)
        except URLError as exc:
            raise ProviderError(f"provider HTTP transport failed: {exc.reason}") from exc


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def _validate_endpoint(url: str) -> None:
    parsed = urlsplit(url)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ProviderError("provider endpoint must use HTTPS except for loopback development")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ProviderError("provider endpoint authority is invalid")


def _object(value: object, *, name: str) -> JsonObject:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise OutputValidationError(f"provider HTTP {name} must be a JSON object")
    return dict(value)


def _api_key(key_env: str) -> str:
    key = os.environ.get(key_env)
    if not key:
        raise ProviderError(f"provider credential is missing from environment variable {key_env}")
    return key


def _decode_response(response: HttpResponse) -> JsonObject:
    try:
        text = response.body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OutputValidationError("provider HTTP response is not UTF-8") from exc
    body = decode_json_object(text, source="provider HTTP response")
    if response.status < 200 or response.status >= 300:
        message = body.get("error", body.get("message", "unknown provider error"))
        raise ProviderError(f"provider HTTP status {response.status}: {message}")
    return body


def _usage(value: object) -> JsonObject | None:
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return dict(value)
    return None


@dataclass(slots=True)
class OpenAICompatibleHttpProvider:
    profile: ModelProfile
    base_url: str
    key_env: str
    http_client: HttpClient
    cost_resolver: CostResolver = no_cost_evidence

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        assert_executable_identity(self.profile)
        started = time.monotonic()
        payload: JsonObject = {
            "model": self.profile.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "reviewer_output",
                    "strict": True,
                    "schema": request.response_schema,
                },
            },
        }
        response = self.http_client.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {_api_key(self.key_env)}",
                "Content-Type": "application/json",
            },
            body=canonical_json_bytes(payload),
            timeout_s=request.timeout_s,
            max_bytes=max(4096, request.max_output_chars * 4),
        )
        envelope = _decode_response(response)
        choices = envelope.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise OutputValidationError("OpenAI-compatible response omitted choices[0]")
        message = _object(choices[0].get("message"), name="choices[0].message")
        if message.get("refusal"):
            raise ProviderError("OpenAI-compatible provider refused the review request")
        content = message.get("content")
        if not isinstance(content, str):
            raise OutputValidationError("OpenAI-compatible response content must be a JSON string")
        structured = decode_json_object(content, source="OpenAI-compatible response content")
        effective_model = assert_effective_identity(
            expected=self.profile.canonical_model,
            actual=envelope.get("model"),
        )
        return build_result(
            profile=self.profile,
            request=request,
            effective_model=effective_model,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            output_text=content,
            structured_output=structured,
            usage=_usage(envelope.get("usage")),
            cost_resolver=self.cost_resolver,
        )


@dataclass(slots=True)
class AnthropicHttpProvider:
    profile: ModelProfile
    base_url: str
    key_env: str
    http_client: HttpClient
    cost_resolver: CostResolver = no_cost_evidence

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        assert_executable_identity(self.profile)
        started = time.monotonic()
        max_tokens = self.profile.params.get("max_tokens", 4096)
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ProviderError("Anthropic max_tokens must be a positive integer")
        payload: JsonObject = {
            "model": self.profile.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": request.prompt}],
            "output_config": {
                "format": {"type": "json_schema", "schema": request.response_schema}
            },
        }
        response = self.http_client.post(
            f"{self.base_url.rstrip('/')}/v1/messages",
            headers={
                "x-api-key": _api_key(self.key_env),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            body=canonical_json_bytes(payload),
            timeout_s=request.timeout_s,
            max_bytes=max(4096, request.max_output_chars * 4),
        )
        envelope = _decode_response(response)
        if envelope.get("stop_reason") in {"refusal", "max_tokens"}:
            raise ProviderError(f"Anthropic output stopped as {envelope.get('stop_reason')}")
        content = envelope.get("content")
        if not isinstance(content, list) or not content or not isinstance(content[0], dict):
            raise OutputValidationError("Anthropic response omitted content[0]")
        text = content[0].get("text")
        if not isinstance(text, str):
            raise OutputValidationError("Anthropic response content[0].text must be JSON text")
        structured = decode_json_object(text, source="Anthropic response content")
        effective_model = assert_effective_identity(
            expected=self.profile.canonical_model,
            actual=envelope.get("model"),
        )
        return build_result(
            profile=self.profile,
            request=request,
            effective_model=effective_model,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            output_text=text,
            structured_output=structured,
            usage=_usage(envelope.get("usage")),
            cost_resolver=self.cost_resolver,
        )
