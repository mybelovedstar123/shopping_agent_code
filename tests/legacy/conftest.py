"""Run the single-agent baseline (agent.py) offline against a scripted model and store."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests" / "stubs")]

import agent as legacy  # noqa: E402

ROWS = {"id": "SKU-1001", "title": "Red lamp", "category": "lamps", "price": 20.0}


def tool(name, **properties):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }


TOOLS = [
    tool("find", q={"type": "string"}),
    tool("view", id={"type": "string"}),
    tool("add", id={"type": "string"}, n={"type": "integer", "minimum": 1}),
    tool("buy"),
    tool("decline"),
]

BRIEF = {
    "items": [{"name": "lamp", "shelf": "lamps", "quantity": 1, "musts": []}],
    "roles": {"search": "find", "open": "view", "cart": "add", "buy": "buy", "refuse": "decline"},
    "pick": "any",
}


class Harness:
    """Replays model turns and answers store calls, recording what the agent sends."""

    def __init__(self, turns, store=None):
        self.turns = list(turns)
        self.store = store or self.default_store
        self.store_calls = []
        self.tool_results = []

    @staticmethod
    def default_store(body):
        name = body["action"]["name"]
        observation = {"rows": [ROWS], "total": 1} if name == "find" else {"ok": True}
        return {
            "step": body["step"],
            "finished": name in ("buy", "decline"),
            "observation": observation,
        }

    def model(self, path, body):
        cost = {"cost": 0.001}
        if "tools" not in body:
            first = body["messages"][0]["content"]
            text = first if isinstance(first, str) else first[0]["text"]
            reply = BRIEF if "prepare a shopping task" in text else {"ok": True}
            message = {"content": json.dumps(reply)}
        else:
            self.tool_results += [m for m in body["messages"] if m.get("role") == "tool"]
            name, args = self.turns.pop(0) if self.turns else ("decline", {})
            call = {
                "id": f"call{len(self.tool_results)}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
            message = {"content": None, "tool_calls": [call]}
        return SimpleNamespace(data={"choices": [{"message": message}], "usage": cost}, error=None)

    def env(self, path, body):
        self.store_calls.append(body["action"]["name"])
        reply = self.store(body)
        if isinstance(reply, SimpleNamespace):
            return reply
        return SimpleNamespace(data=reply, error=None)


@pytest.fixture
def agent():
    return legacy


@pytest.fixture
def play(monkeypatch):
    """Run agent_main with scripted model turns; returns (trace, harness)."""

    def _play(turns, store=None, max_steps=30):
        harness = Harness(turns, store)
        monkeypatch.setattr(legacy._llm, "post_verbose", harness.model)
        monkeypatch.setattr(legacy._env, "post_verbose", harness.env)
        problem = {
            "environment": {
                "binding": {"episode": "test"},
                "policy_view": {"query": "Buy a lamp.", "tools": TOOLS, "max_steps": max_steps},
            }
        }
        return legacy.agent_main(problem), harness

    return _play
