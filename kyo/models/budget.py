"""Limits on how much each agent, and the team as a whole, may spend on models."""

from __future__ import annotations

import threading
from typing import Any

from .base import Model, ModelError, ModelReply


class BudgetExceeded(ModelError):
    """A model call was refused because a limit has been reached."""


class Budget:
    """Counts calls and spend. A child budget also charges its parent, so a team has one cap."""

    def __init__(
        self, max_calls: int, max_spend: float = float("inf"), parent: Budget | None = None
    ):
        self.max_calls = max_calls
        self.max_spend = max_spend
        self.parent = parent
        self.calls = 0
        self.spent = 0.0
        self._lock = threading.Lock()

    def child(self, max_calls: int, max_spend: float = float("inf")) -> Budget:
        return Budget(max_calls, max_spend, parent=self)

    def reserve_call(self) -> None:
        """Count a call before it is made, or refuse it. Spend is only known afterwards."""
        with self._lock:
            if self.calls >= self.max_calls:
                raise BudgetExceeded(f"call limit of {self.max_calls} reached")
            if self.spent >= self.max_spend:
                raise BudgetExceeded(f"spend limit of {self.max_spend} reached")
            if self.parent is not None:
                self.parent.reserve_call()
            self.calls += 1

    def add_cost(self, cost: float) -> None:
        with self._lock:
            self.spent += cost
        if self.parent is not None:
            self.parent.add_cost(cost)


class BudgetedModel:
    """A model that spends from a budget."""

    def __init__(self, model: Model, budget: Budget):
        self.model = model
        self.budget = budget

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
    ) -> ModelReply:
        self.budget.reserve_call()
        reply = self.model.complete(messages, tools, max_tokens)
        self.budget.add_cost(reply.cost)
        return reply
