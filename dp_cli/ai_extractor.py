from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class AiExtractionError(Exception):
    """Raised when an AI extraction request cannot produce valid JSON."""


class AiDetailExtractor:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
        self.base_url = (base_url if base_url is not None else os.getenv("OPENAI_BASE_URL", "")).rstrip("/")
        self.model = model if model is not None else os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.timeout = timeout

    def extract(self, page_package: dict[str, Any], schema: list[str] | None = None) -> dict[str, Any]:
        if not self.api_key:
            raise AiExtractionError("OPENAI_API_KEY is required for AI detail extraction.")
        if not self.base_url:
            self.base_url = "https://api.openai.com/v1"

        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You extract structured data from rendered web pages. "
                        "Return only a JSON object with keys detail_info, fields, confidence, warnings. "
                        "detail_info must be an object. fields must be a list of strings. "
                        "confidence must be a number from 0 to 1. warnings must be a list of strings."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "schema": schema or [],
                            "page": page_package,
                            "instructions": (
                                "If schema is provided, extract those fields only when supported by page evidence. "
                                "If schema is empty, infer useful general fields from the page. "
                                "Use null-free concise values and do not invent values."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        response = self._post_json(f"{self.base_url}/chat/completions", payload)
        content = self._message_content(response)
        parsed = self._parse_json_object(content)
        detail_info = parsed.get("detail_info")
        if not isinstance(detail_info, dict) or not detail_info:
            raise AiExtractionError("AI extractor returned empty or invalid detail_info.")
        fields = parsed.get("fields")
        if not isinstance(fields, list):
            fields = list(detail_info.keys())
        warnings = parsed.get("warnings")
        if not isinstance(warnings, list):
            warnings = []
        confidence = parsed.get("confidence", 0)
        if not isinstance(confidence, int | float):
            confidence = 0
        return {
            "source_url": page_package.get("url"),
            "page_title": page_package.get("title"),
            "fields": [str(item) for item in fields],
            "detail_info": detail_info,
            "confidence": max(0.0, min(float(confidence), 1.0)),
            "warnings": [str(item) for item in warnings],
            "template": {
                "extract_strategy": "dp_cli_ai_detail_v1",
                "fields": [str(item) for item in fields],
                "model": self.model,
            },
        }

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            raise AiExtractionError(f"AI extraction request failed: {exc}") from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AiExtractionError("AI extraction response was not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise AiExtractionError("AI extraction response must be a JSON object.")
        return parsed

    def _message_content(self, response: dict[str, Any]) -> str:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AiExtractionError("AI extraction response did not include choices.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise AiExtractionError("AI extraction response did not include text content.")
        return content.strip()

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.lower().startswith("json"):
                stripped = stripped[4:].strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end < start:
            raise AiExtractionError("AI extraction content did not contain a JSON object.")
        try:
            parsed = json.loads(stripped[start:end + 1])
        except json.JSONDecodeError as exc:
            raise AiExtractionError("AI extraction content was not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise AiExtractionError("AI extraction content must be a JSON object.")
        return parsed
