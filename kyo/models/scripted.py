"""A model that replays prepared replies, so tests run without a network."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .base import ModelError, ModelReply

Rule = Callable[[str], Any]


class ScriptedModel:
    """Answers from a list of replies, or by calling a function with the prompt text.

    A reply can be text, a dict (sent as JSON), a `ModelReply`, or an exception to raise.
    """

    def __init__(self, replies: list[Any] | Rule, cost: float = 0.0):
        self.replies = replies
        self.cost = cost
        self.prompts: list[str] = []

    def complete(self, messages, tools=None, max_tokens=1024) -> ModelReply:
        prompt = str(messages[-1].get("content", ""))
        self.prompts.append(prompt)
        if callable(self.replies):
            reply = self.replies(prompt)
        elif self.replies:
            reply = self.replies.pop(0)
        else:
            raise ModelError("the scripted model has no replies left")
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, ModelReply):
            return reply
        text = json.dumps(reply) if isinstance(reply, dict) else str(reply)
        return ModelReply(content=text, cost=self.cost)
