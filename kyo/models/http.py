"""A client for OpenAI-compatible chat completion endpoints, using only the standard library."""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from .base import ModelError, ModelReply

RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}
BACKOFF_SECONDS = (2.0, 5.0, 10.0)


class HttpModel:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self._api_key = api_key
        self.timeout = timeout
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"HttpModel(url={self.url!r}, model={self.model!r})"

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
    ) -> ModelReply:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        return self._parse(self._post(body))

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        data = json.dumps(body).encode("utf-8")
        for delay in (*BACKOFF_SECONDS, None):
            request = urllib.request.Request(self.url, data, headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                problem = f"the model endpoint returned HTTP {error.code}"
                retry = error.code in RETRY_STATUSES
            except (OSError, ValueError) as error:
                problem = f"the model endpoint could not be used: {type(error).__name__}"
                retry = True
            if not retry or delay is None:
                raise ModelError(problem)
            self._sleep(delay * random.uniform(0.8, 1.2))
        raise AssertionError("unreachable")

    @staticmethod
    def _parse(data: dict[str, Any]) -> ModelReply:
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise ModelError("the model endpoint returned no choices") from None
        if not isinstance(message, dict):
            raise ModelError("the model endpoint returned no message")
        content = message.get("content") or ""
        calls = message.get("tool_calls") or []
        if not content and not calls:
            raise ModelError("the model returned an empty reply")
        cost = (data.get("usage") or {}).get("cost")
        return ModelReply(content, calls, float(cost) if isinstance(cost, int | float) else 0.0)


def model_from_env(env: Mapping[str, str] | None = None) -> HttpModel | None:
    """Build a model from KYO_MODEL_* settings, or return None to run offline."""
    env = os.environ if env is None else env
    base_url, name = env.get("KYO_MODEL_BASE_URL"), env.get("KYO_MODEL_NAME")
    if not base_url or not name:
        return None
    return HttpModel(base_url, name, env.get("KYO_MODEL_API_KEY", ""))
