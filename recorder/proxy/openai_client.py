"""Small dependency-free OpenAI Responses API adapter."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any
from urllib import error, request


class OpenAIConfigurationError(RuntimeError):
    """Raised when OpenAI-backed behavior is requested but not configured."""


class OpenAIResponseError(RuntimeError):
    """Raised when the OpenAI API returns an error or unusable response."""


@dataclass(slots=True)
class OpenAITextResponse:
    text: str
    response_id: str | None
    model: str
    usage: dict[str, Any]


class OpenAIResponsesClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 45.0,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise OpenAIConfigurationError("OPENAI_API_KEY is required for OpenAI-backed runs.")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.timeout = timeout

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        body.setdefault("model", self.model)
        req = request.Request(
            f"{self.base_url}/responses",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise OpenAIResponseError(f"OpenAI API returned {exc.code}: {details}") from exc
        except error.URLError as exc:
            raise OpenAIResponseError(f"OpenAI API request failed: {exc.reason}") from exc

    def complete_text(
        self,
        *,
        prompt: str,
        system_message: str = "",
        context: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        text_format: dict[str, Any] | None = None,
    ) -> OpenAITextResponse:
        context = context or {}
        input_text = prompt
        if context:
            input_text = f"{prompt}\n\nContext JSON:\n{json.dumps(context, sort_keys=True)}"

        messages: list[dict[str, str]] = []
        if system_message:
            messages.append({"role": "developer", "content": system_message})
        messages.append({"role": "user", "content": input_text})

        payload: dict[str, Any] = {
            "input": messages,
            "temperature": (
                temperature
                if temperature is not None
                else float(os.getenv("OPENAI_TEMPERATURE", "0.85"))
            ),
            "max_output_tokens": (
                max_output_tokens
                if max_output_tokens is not None
                else int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "700"))
            ),
            "store": False,
        }
        if text_format:
            payload["text"] = {"format": text_format}

        response = self.create(payload)
        return OpenAITextResponse(
            text=_extract_output_text(response),
            response_id=response.get("id"),
            model=response.get("model") or self.model,
            usage=dict(response.get("usage") or {}),
        )

    def complete_json(
        self,
        *,
        prompt: str,
        system_message: str = "",
        context: dict[str, Any] | None = None,
        schema: dict[str, Any],
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        result = self.complete_text(
            prompt=prompt,
            system_message=system_message,
            context=context,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            text_format={
                "type": "json_schema",
                "name": "gommage_fix_issue_brief",
                "schema": schema,
                "strict": True,
            },
        )
        try:
            parsed = json.loads(result.text)
        except json.JSONDecodeError as exc:
            raise OpenAIResponseError("OpenAI response did not contain valid JSON.") from exc
        parsed["_openai"] = {
            "response_id": result.response_id,
            "model": result.model,
            "usage": result.usage,
        }
        return parsed


class OpenAICompletion:
    """Callable adapter used by LLMProxy."""

    def __init__(self, client: OpenAIResponsesClient | None = None) -> None:
        self.client = client or OpenAIResponsesClient()
        self.model = self.client.model
        self.last_metadata: dict[str, Any] = {}

    def __call__(
        self,
        prompt: str,
        *,
        system_message: str = "",
        context: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        **_: Any,
    ) -> str:
        response = self.client.complete_text(
            prompt=prompt,
            system_message=system_message,
            context=context,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        self.last_metadata = {
            "provider": "openai",
            "response_id": response.response_id,
            "model": response.model,
            "temperature": temperature
            if temperature is not None
            else float(os.getenv("OPENAI_TEMPERATURE", "0.85")),
            "usage": response.usage,
        }
        self.model = response.model
        return response.text


def _extract_output_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    chunks: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)

    text = "\n".join(chunk for chunk in chunks if chunk.strip()).strip()
    if text:
        return text
    raise OpenAIResponseError("OpenAI response did not include text output.")
