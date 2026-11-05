"""Delivers messages between agents. Each agent has its own mailbox and worker thread."""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from .agent import Agent, AgentError
from .messages import Message


class AgentTimeout(AgentError):
    """An agent did not answer in time."""


class UnknownAgent(AgentError):
    """A message was addressed to an agent that is not on the bus."""


class _Context:
    """The bus as seen by one agent."""

    def __init__(self, bus: Bus, name: str):
        self._bus = bus
        self._name = name

    def request(self, recipient, kind, payload=None, timeout=None):
        return self._bus.request(recipient, kind, payload, sender=self._name, timeout=timeout)

    def note(self, text, **data):
        pass


class Bus:
    """Runs a set of agents. Payloads are copied through JSON, so agents share no memory."""

    def __init__(self, timeout: float = 60.0):
        self.timeout = timeout
        self._agents: dict[str, Agent] = {}
        self._mailboxes: dict[str, queue.Queue[Message | None]] = {}
        self._workers: list[threading.Thread] = []
        self._pending: dict[str, Future[Message]] = {}
        self._lock = threading.Lock()
        self._running = False

    def register(self, agent: Agent) -> None:
        if self._running:
            raise RuntimeError("register agents before starting the bus")
        if agent.name in self._agents:
            raise ValueError(f"an agent named {agent.name!r} is already registered")
        self._agents[agent.name] = agent
        self._mailboxes[agent.name] = queue.Queue()

    def start(self) -> None:
        self._running = True
        for name in self._agents:
            worker = threading.Thread(target=self._work, args=(name,), name=name, daemon=True)
            worker.start()
            self._workers.append(worker)

    def stop(self) -> None:
        self._running = False
        for mailbox in self._mailboxes.values():
            mailbox.put(None)
        for worker in self._workers:
            worker.join(timeout=5)
        self._workers.clear()

    def __enter__(self) -> Bus:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def request(
        self,
        recipient: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        sender: str = "client",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a message and wait for the reply payload."""
        if recipient not in self._mailboxes:
            raise UnknownAgent(f"no agent named {recipient!r}")
        message = Message.from_json(Message(sender, recipient, kind, payload or {}).to_json())
        answer: Future[Message] = Future()
        with self._lock:
            self._pending[message.id] = answer
        self._mailboxes[recipient].put(message)
        try:
            reply = answer.result(timeout if timeout is not None else self.timeout)
        except FutureTimeout:
            raise AgentTimeout(f"{recipient} did not answer {kind!r} in time") from None
        finally:
            with self._lock:
                self._pending.pop(message.id, None)
        if reply.failed:
            raise AgentError(reply.error)
        return reply.payload

    def _work(self, name: str) -> None:
        agent = self._agents[name]
        mailbox = self._mailboxes[name]
        context = _Context(self, name)
        while True:
            message = mailbox.get()
            if message is None:
                return
            try:
                reply = message.reply(agent.handle(message, context))
                reply = Message.from_json(reply.to_json())
            except Exception as error:  # a failing handler must not stop this agent
                reply = message.failure(f"{type(error).__name__}: {error}")
            self._deliver(reply)

    def _deliver(self, reply: Message) -> None:
        with self._lock:
            answer = self._pending.get(reply.reply_to or "")
        if answer is not None and not answer.done():
            answer.set_result(reply)
