"""An ordered record of what a team of agents did."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

Event = dict[str, Any]
Subscriber = Callable[[Event], None]


class Trace:
    """Keeps every event in order and tells subscribers as events happen.

    A subscriber that raises is ignored, so watching a team can never affect it.
    """

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()

    def record(self, type: str, agent: str, **data: Any) -> Event:
        with self._lock:
            event = {
                "seq": len(self._events) + 1,
                "time": time.time(),
                "type": type,
                "agent": agent,
                **data,
            }
            self._events.append(event)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:  # noqa: S110 - observers must not disturb the agents
                pass
        return event

    def subscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            self._subscribers.append(subscriber)

    def events(self) -> list[Event]:
        with self._lock:
            return list(self._events)
