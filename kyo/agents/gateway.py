"""The gateway agent is the team's only door to the store.

Every store action passes through its mailbox, so steps are counted in one place, arguments
are validated before a step is spent, and all observations can be reviewed afterwards.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..runtime.agent import Agent
from ..schema import shape_args


class StoreBackend(Protocol):
    """A store the team can shop in."""

    def tools(self) -> list[dict[str, Any]]: ...

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Perform an action and return {observation, finished}."""
        ...


class GatewayAgent(Agent):
    """Handles `tools`, `call`, `status`, `limit_steps` and `observations`."""

    name = "gateway"

    def __init__(self, store: StoreBackend, max_steps: int = 30):
        self.store = store
        self.max_steps = max_steps
        self.step = 0
        self.finished = False
        self.log: list[dict[str, Any]] = []
        self._schemas = {
            t["function"]["name"]: t["function"].get("parameters") for t in store.tools()
        }

    def on_tools(self, payload, context) -> dict[str, Any]:
        return {"tools": self.store.tools()}

    def on_status(self, payload, context) -> dict[str, Any]:
        return {"step": self.step, "left": self.max_steps - self.step, "finished": self.finished}

    def on_limit_steps(self, payload, context) -> dict[str, Any]:
        """Lower the step limit, for a request that sets its own."""
        self.max_steps = min(self.max_steps, int(payload["steps"]))
        return self.on_status({}, context)

    def on_call(self, payload, context) -> dict[str, Any]:
        """Perform one store action. A rejected call spends no step."""
        name, args = payload["name"], payload.get("args") or {}
        if self.finished:
            return {"ok": False, "error": "the episode has finished"}
        if name not in self._schemas:
            return {"ok": False, "error": f"unknown tool {name}"}
        if self.step >= self.max_steps:
            return {"ok": False, "error": "no steps left"}
        args, faults = shape_args(args, self._schemas[name])
        if faults:
            return {"ok": False, "error": "; ".join(faults)}
        result = self.store.call(name, args)
        self.step += 1
        self.finished = bool(result.get("finished"))
        observation = result.get("observation")
        self.log.append({"step": self.step, "name": name, "args": args, "observation": observation})
        context.note(f"step {self.step}: {name}", tool=name, args=args)
        return {
            "ok": True,
            "observation": observation,
            "finished": self.finished,
            "left": self.max_steps - self.step,
        }

    def on_observations(self, payload, context) -> dict[str, Any]:
        """Everything the store has said since a given step."""
        since = int(payload.get("since", 0))
        return {"entries": [e for e in self.log if e["step"] > since], "step": self.step}
