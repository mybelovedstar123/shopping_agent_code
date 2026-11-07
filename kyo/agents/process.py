"""The process agent carries out steps the request demands before an ending, such as approval.

It remembers proof per cart: an approval of one cart says nothing about a different cart.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..models.base import Model, ModelError, ask_json
from ..prompts import GATE
from ..runtime.agent import Agent
from ..schema import text_params
from .interview import spoken

YES = re.compile(
    r"\b(yes|yep|yeah|sure|ok|okay|approved?|approve|go ahead|sounds good|please do|fine|confirm(ed)?)\b",
    re.I,
)
NO = re.compile(r"\b(no|nope|don'?t|do not|not|stop|cancel|wait|decline|rather not)\b", re.I)
ROUNDS = 3


def read_approval(answer: str) -> bool | None:
    """True for approval, False for refusal, None when the reply says neither."""
    if NO.search(answer):
        return False
    return True if YES.search(answer) else None


class ProcessAgent(Agent):
    """Handles `perform`: {stage, steps, cart, ask_tool, ask_schema, tools}."""

    name = "process"

    def __init__(self, model: Model | None = None):
        self.model = model
        self.proven: set[str] = set()
        self.record: list[str] = []

    def on_perform(self, payload: dict[str, Any], context) -> dict[str, Any]:
        stage = payload["stage"]
        steps = [s for s in payload.get("steps") or [] if s.get("when") == stage]
        mark = f"{stage}|{json.dumps(payload.get('cart') or {}, sort_keys=True)}"
        if not steps or mark in self.proven:
            return {"proceed": True, "done": [], "not_done": []}
        if self.model:
            result = self._with_model(stage, steps, payload, context)
            if result is not None:
                if result["proceed"]:
                    self.proven.add(mark)
                return result
            context.note("the model could not run the process steps; using rules")
        result = self._with_rules(stage, steps, payload, context)
        if result["proceed"]:
            self.proven.add(mark)
        return result

    def _with_rules(self, stage, steps, payload, context) -> dict[str, Any]:
        done, not_done = [], []
        for step in steps:
            if not step.get("messages"):
                not_done.append(step["step"])
                continue
            question = self._question(stage, payload.get("cart") or {})
            reply = self._ask(question, payload, context)
            if reply is None:
                return {
                    "proceed": False,
                    "reason": "could not reach the shopper",
                    "done": done,
                    "not_done": [step["step"]],
                    "approved": None,
                }
            approved = read_approval(reply)
            self.record.append(f"Q: {question} A: {reply}")
            if approved is not True:
                return {
                    "proceed": False,
                    "reason": reply,
                    "done": done,
                    "not_done": [step["step"]],
                    "approved": approved,
                }
            done.append(step["step"])
        if not_done:
            context.note(f"cannot do without a model: {'; '.join(not_done)}")
        return {"proceed": True, "done": done, "not_done": not_done}

    @staticmethod
    def _question(stage: str, cart: dict[str, Any]) -> str:
        if stage == "end":
            return "I could not find anything that fits. May I end without buying?"
        lines = ", ".join(f"{n} x {i}" for i, n in (cart.get("lines") or {}).items())
        total = cart.get("total")
        cost = f" for a total of {total:g}" if isinstance(total, int | float) else ""
        return f"May I place this order: {lines}{cost}?"

    @staticmethod
    def _ask(question: str, payload, context) -> str | None:
        tool, schema = payload.get("ask_tool"), payload.get("ask_schema") or {}
        param = next(iter(schema.get("required") or text_params(schema)), None)
        if not tool or not param:
            return None
        reply = context.request("gateway", "call", {"name": tool, "args": {param: question}})
        return spoken(reply["observation"]) if reply.get("ok") else None

    def _with_model(self, stage, steps, payload, context) -> dict[str, Any] | None:
        wanted = "place the order" if stage == "order" else "end without a purchase"
        done_calls: list[str] = []
        for _ in range(ROUNDS):
            record = "\n".join(f"{n}. {line}" for n, line in enumerate(self.record, 1))
            prompt = (
                GATE.replace("STAGE", wanted)
                + f"\n\nREQUEST:\n{payload.get('request', '')}"
                + "\n\nSTEPS DUE NOW:\n"
                + "\n".join(s["step"] for s in steps)
                + f"\n\nCART NOW:\n{json.dumps(payload.get('cart') or {})}"
                + f"\n\nRECORD:\n{record or '(nothing yet)'}"
                + f"\n\nTOOLS:\n{json.dumps(payload.get('tools') or [])}"
            )
            try:
                got = ask_json(self.model, prompt, 3500)
            except ModelError:
                return None
            if got is None:
                return None
            hold = str(got.get("hold") or "").strip()
            needs = [n for n in got.get("needs") or [] if isinstance(n, dict) and n.get("need")]
            if hold or needs:
                return {
                    "proceed": False,
                    "reason": hold or "the reply changes the requirements",
                    "done": done_calls,
                    "not_done": [],
                    "needs": needs,
                    "approved": False,
                }
            call = got.get("call") if isinstance(got.get("call"), dict) else None
            if not call:
                missing = [
                    str(s.get("step"))
                    for s in got.get("steps") or []
                    if isinstance(s, dict) and s.get("done") is not True
                ]
                return {
                    "proceed": not missing,
                    "reason": "; ".join(missing),
                    "done": done_calls,
                    "not_done": missing,
                }
            name, args = str(call.get("tool") or ""), call.get("args") or {}
            reply = context.request("gateway", "call", {"name": name, "args": args})
            if not reply.get("ok"):
                return None
            line = f"{name} {json.dumps(args)} -> {spoken(reply['observation'])[:300]}"
            self.record.append(line)
            done_calls.append(line)
        return {
            "proceed": False,
            "reason": "the required steps were not finished",
            "done": done_calls,
            "not_done": [s["step"] for s in steps],
        }
