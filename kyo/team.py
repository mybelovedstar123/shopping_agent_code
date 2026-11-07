"""Assemble a team of agents and run one shopping request."""

from __future__ import annotations

from typing import Any

from .agents.cart import CartAgent
from .agents.catalog import CatalogAgent
from .agents.coordinator import Coordinator
from .agents.gateway import GatewayAgent, StoreBackend
from .agents.interview import InterviewAgent
from .agents.notes import NotesAgent
from .agents.process import ProcessAgent
from .agents.request import RequestAgent
from .agents.review import ReviewAgent
from .agents.selection import SelectionAgent
from .models.base import Model
from .models.budget import Budget, BudgetedModel
from .runtime.bus import Bus
from .runtime.trace import Trace

# Model calls each agent may make in one run.
ALLOWANCES = {"request": 3, "catalog": 3, "interview": 8, "review": 6, "process": 6, "notes": 10}
TEAM_CALLS = 30


def _allowed(model: Model | None, budget: Budget, name: str) -> Model | None:
    if model is None:
        return None
    return BudgetedModel(model, budget.child(ALLOWANCES[name]))


def build_team(
    store: StoreBackend,
    model: Model | None = None,
    max_steps: int = 30,
    trace: Trace | None = None,
    budget: Budget | None = None,
    timeout: float = 300.0,
) -> Bus:
    """Create a bus with every agent registered. The caller starts and stops it."""
    budget = budget or Budget(TEAM_CALLS)
    bus = Bus(timeout=timeout, trace=trace)
    bus.register(GatewayAgent(store, max_steps))
    bus.register(RequestAgent(_allowed(model, budget, "request")))
    bus.register(CatalogAgent(_allowed(model, budget, "catalog")))
    bus.register(InterviewAgent(_allowed(model, budget, "interview")))
    bus.register(SelectionAgent())
    bus.register(CartAgent())
    bus.register(ReviewAgent(_allowed(model, budget, "review")))
    bus.register(ProcessAgent(_allowed(model, budget, "process")))
    bus.register(NotesAgent(_allowed(model, budget, "notes")))
    bus.register(Coordinator())
    return bus


def run_team(
    request: str,
    store: StoreBackend,
    model: Model | None = None,
    max_steps: int = 30,
    trace: Trace | None = None,
    budget: Budget | None = None,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Run one request to its end and return the coordinator's result."""
    with build_team(store, model, max_steps, trace, budget, timeout) as bus:
        return bus.request("coordinator", "run", {"request": request}, timeout=timeout)
