"""What agents need from a language model."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol


class ModelError(Exception):
    """A model could not produce a reply."""


@dataclass(frozen=True)
class ModelReply:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    cost: float = 0.0


class Model(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
    ) -> ModelReply: ...


def parse_json_object(text: Any) -> dict[str, Any] | None:
    """Find the one JSON object in a model's reply, tolerating fences and stray words."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    fence = re.match(r"^```[A-Za-z]*\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    for candidate in (text, text[text.find("{") : text.rfind("}") + 1]):
        try:
            found = json.loads(candidate)
        except ValueError:
            continue
        return found if isinstance(found, dict) else None
    return None


def ask_json(model: Model, prompt: str, max_tokens: int = 1800) -> dict[str, Any] | None:
    """Ask for one JSON object. Returns None when the reply does not contain one."""
    reply = model.complete([{"role": "user", "content": prompt}], max_tokens=max_tokens)
    return parse_json_object(reply.content)
