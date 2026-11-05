"""Messages are the only way agents exchange information."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class Message:
    """One request or reply between agents. The payload must be plain JSON values."""

    sender: str
    recipient: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=_new_id)
    reply_to: str | None = None
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.error is not None

    def reply(self, payload: dict[str, Any] | None = None) -> Message:
        return Message(self.recipient, self.sender, self.kind, payload or {}, reply_to=self.id)

    def failure(self, error: str) -> Message:
        return Message(self.recipient, self.sender, self.kind, reply_to=self.id, error=error)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> Message:
        return cls(**json.loads(text))
