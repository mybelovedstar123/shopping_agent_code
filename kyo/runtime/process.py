"""Run an agent in its own process behind the normal agent interface.

The agent is named by an import path, such as "kyo.agents.request:RequestAgent", and its
settings must be JSON values. Messages cross the process boundary as JSON text.
"""

from __future__ import annotations

import importlib
import json
import multiprocessing
from typing import Any

from .agent import Agent, AgentError
from .messages import Message

_SPAWN = multiprocessing.get_context("spawn")


class AgentCrashed(AgentError):
    """The process running an agent has died."""


class _ChildContext:
    """Lets the child agent ask other agents through its parent."""

    def __init__(self, pipe):
        self._pipe = pipe

    def request(self, recipient, kind, payload=None):
        self._pipe.send(json.dumps(["request", recipient, kind, payload or {}]))
        answer = json.loads(self._pipe.recv())
        if answer.get("error"):
            raise AgentError(answer["error"])
        return answer["payload"]

    def note(self, text, **data):
        self._pipe.send(json.dumps(["note", text, data]))


def _child_main(pipe, path: str, settings: dict[str, Any]) -> None:
    module, _, name = path.partition(":")
    agent = getattr(importlib.import_module(module), name)(**settings)
    context = _ChildContext(pipe)
    while True:
        try:
            text = pipe.recv()
        except EOFError:
            return
        if text is None:
            return
        message = Message.from_json(text)
        try:
            reply = message.reply(agent.handle(message, context))
            text = reply.to_json()
        except Exception as error:  # the parent decides what a failure means
            text = message.failure(f"{type(error).__name__}: {error}").to_json()
        pipe.send(json.dumps(["reply", text]))


class ProcessAgent(Agent):
    """Forwards each message to a child process and relays what the child asks for."""

    def __init__(self, name: str, path: str, settings: dict[str, Any] | None = None):
        self.name = name
        self._pipe, child_pipe = _SPAWN.Pipe()
        self.process = _SPAWN.Process(
            target=_child_main, args=(child_pipe, path, settings or {}), name=name, daemon=True
        )
        self.process.start()
        child_pipe.close()

    def handle(self, message: Message, context) -> dict[str, Any]:
        try:
            self._pipe.send(message.to_json())
            while True:
                event = json.loads(self._recv())
                if event[0] == "reply":
                    reply = Message.from_json(event[1])
                    if reply.failed:
                        raise AgentError(reply.error)
                    return reply.payload
                if event[0] == "note":
                    context.note(event[1], **event[2])
                else:
                    self._pipe.send(json.dumps(self._relay(event, context)))
        except (EOFError, BrokenPipeError, ConnectionError, OSError):
            raise AgentCrashed(f"the {self.name} process has stopped") from None

    def _recv(self) -> str:
        while not self._pipe.poll(0.1):
            if not self.process.is_alive() and not self._pipe.poll(0):
                raise EOFError
        return self._pipe.recv()

    @staticmethod
    def _relay(event: list[Any], context) -> dict[str, Any]:
        _, recipient, kind, payload = event
        try:
            return {"payload": context.request(recipient, kind, payload)}
        except AgentError as error:
            return {"error": str(error)}

    def stop(self) -> None:
        try:
            self._pipe.send(None)
        except (BrokenPipeError, OSError):
            pass
        self.process.join(timeout=5)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=5)
        self._pipe.close()
