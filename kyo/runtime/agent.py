"""The contract every agent implements."""

from __future__ import annotations

from typing import Any, Protocol

from .messages import Message


class AgentError(Exception):
    """Another agent could not do what was asked."""


class UnknownKind(AgentError):
    """An agent received a kind of message it does not handle."""


class Context(Protocol):
    """What an agent may do besides answering: ask other agents and leave notes."""

    def request(
        self, recipient: str, kind: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...

    def note(self, text: str, **data: Any) -> None: ...


class Agent:
    """Handles one message at a time. A message of kind `x` is handled by `on_x`.

    An agent's attributes are private to it. Handlers receive a payload and a context
    and return the reply payload, so other agents can only see what is sent to them.
    """

    name = "agent"

    def handle(self, message: Message, context: Context) -> dict[str, Any]:
        handler = getattr(self, f"on_{message.kind}", None)
        if handler is None:
            raise UnknownKind(f"{self.name} does not handle {message.kind!r}")
        return handler(message.payload, context) or {}
