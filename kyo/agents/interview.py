"""The interview agent asks the shopper about preferences the request left unstated.

Messages are scarce, and a question reveals a preference only if it names that exact property,
so it asks about one property at a time and favours items with nothing found yet.
"""

from __future__ import annotations

import re
from typing import Any

from ..domain import Item
from ..fit import words
from ..models.base import Model, ModelError, ask_json, item_index, listed
from ..prompts import HEAR, PLAN
from ..runtime.agent import Agent
from ..schema import text_params
from .request import read_money

NO_PREFERENCE = re.compile(
    r"\b(don'?t mind|do not mind|no preference|doesn'?t matter|does not matter|don'?t care"
    r"|any(thing)? (is|will|would) (be )?(fine|ok|okay|do)|whatever|either is fine|up to you)\b",
    re.I,
)
PUZZLED = re.compile(r"\b(don'?t (know|understand|follow)|not sure what|what do you mean)\b", re.I)
POLITE = re.compile(
    r"^(?:well,?\s*|oh,?\s*|hmm,?\s*|yes,?\s*)*"
    r"(?:i(?:'d| would)? (?:like|prefer|want|need|love)|i'm after|i am after|it (?:should|must|has to) be"
    r"|it needs to be|make it|please|preferably|ideally|definitely|let'?s go with|go with|something)\s*",
    re.I,
)


def spoken(observation: Any) -> str:
    """The words of a reply, whatever structure holds them."""
    if isinstance(observation, str):
        return observation
    found: list[str] = []

    def walk(node: Any, depth: int) -> None:
        if depth > 4:
            return
        if isinstance(node, str) and node.strip():
            found.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)

    walk(observation, 0)
    return " ".join(found)


def read_answer(
    answer: str, label: str, values: list[str], loose: bool = True
) -> dict[str, str] | None:
    """Turn a reply about one property into a requirement, or None when it gives none.

    With loose False, only a known value or "no preference" counts: the reply was about
    something else, such as the budget.
    """
    text = " ".join(answer.split()).strip(" .!")
    if not text or NO_PREFERENCE.search(text):
        return {"free": label}
    if PUZZLED.search(text):
        return None
    heard = set(words(text))
    known = [v for v in values if v and set(words(v)) and set(words(v)) <= heard]
    if known:
        value = max(known, key=len)
        negative = re.search(
            rf"\b(not|no|anything but|except|avoid)\b[^.]*{re.escape(value)}", text, re.I
        )
        return {"need": f"{label}: {'not ' if negative else ''}{value}"}
    if not loose:
        return None
    return {"need": f"{label}: {POLITE.sub('', text).strip(' .!,')}"}


class InterviewAgent(Agent):
    """Handles `run`: asks within the message limit and returns requirement changes."""

    name = "interview"

    def __init__(self, model: Model | None = None):
        self.model = model
        self.asked: list[dict[str, str]] = []

    def on_run(self, payload: dict[str, Any], context) -> dict[str, Any]:
        items = [Item.from_dict(i) for i in payload["items"]]
        tool, schema = payload["tool"], payload.get("schema") or {}
        param = next(iter(schema.get("required") or text_params(schema)), None)
        remaining = int(payload.get("messages", 0))
        keep = int(payload.get("keep", 6))
        tables = [context.request("catalog", "keys", {"item": i.to_dict()}) for i in items]
        lanes = self._lanes(items, tables, payload.get("request", ""), remaining, context)
        values = {
            (n, slot["key"]): slot["values"]
            for n, table in enumerate(tables)
            for slot in table["keys"]
        }
        found = {n: 0 for n in lanes}
        spent = {n: 0 for n in lanes}
        changes: dict[str, list] = {"needs": [], "free": [], "drops": []}
        budget = None
        while remaining > 0 and param:
            pending = [n for n in lanes if lanes[n]]
            if not pending:
                break
            number = min(pending, key=lambda n: (found[n] > 0, spent[n], n))
            label = lanes[number].pop(0)
            question = f"What {label} would you like for the {items[number].name}?"
            reply = context.request("gateway", "call", {"name": tool, "args": {param: question}})
            if not reply.get("ok") or reply.get("finished"):
                break
            remaining -= 1
            spent[number] += 1
            answer = spoken(reply["observation"])
            self.asked.append({"question": question, "answer": answer})
            heard = self._hear(number, question, answer, label, values, items, context)
            for need in heard.get("needs", []):
                changes["needs"].append(need)
                found[need["item"]] = found.get(need["item"], 0) + 1
            changes["free"] += heard.get("free", [])
            changes["drops"] += heard.get("drops", [])
            budget = heard.get("budget", budget)
            if int(reply.get("left", 99)) <= keep:
                break
        context.note(f"asked {len(self.asked)} question(s), learned {len(changes['needs'])}")
        return {**changes, "budget": budget, "asked": list(self.asked)}

    def _lanes(self, items, tables, request, count, context) -> dict[int, list[str]]:
        """Rank what to ask per item: the model's picks if any, then attribute keys by coverage."""
        picks = self._plan(items, tables, request, count, context) if self.model else {}
        lanes: dict[int, list[str]] = {}
        for number, (item, table) in enumerate(zip(items, tables, strict=True)):
            settled = {" ".join(words(n.split(":", 1)[0])) for n in item.needs()}
            settled |= {" ".join(words(f)) for f in item.free}
            lane: list[str] = []
            for label in picks.get(number, []) + [slot["key"] for slot in table["keys"]]:
                name = " ".join(words(label))
                if name not in settled and name not in lane:
                    lane.append(name)
            lanes[number] = lane
        return lanes

    def _plan(self, items, tables, request, count, context) -> dict[int, list[str]]:
        settled = [
            f"item {n}: {item.name} - settled by the request: {'; '.join(item.needs()) or 'nothing'}"
            for n, item in enumerate(items, 1)
        ]
        listing = []
        for n, table in enumerate(tables, 1):
            listing.append(f"item {n} keys:")
            listing += [
                f"  {s['key']} (on {s['carried']}): {' | '.join(s['values'][:4])}"
                for s in table["keys"]
            ]
        prompt = (
            PLAN.replace("COUNT", str(max(1, count)))
            + f"\n\nREQUEST:\n{request}\n\nITEMS:\n"
            + "\n".join(settled)
            + "\n\nATTRIBUTE KEYS SEEN:\n"
            + "\n".join(listing)
        )
        try:
            plan = ask_json(self.model, prompt, 3500) or {}
        except ModelError as error:
            context.note(f"the model could not plan questions: {error}")
            return {}
        picks: dict[int, list[str]] = {}
        for pick in listed(plan, "ask"):
            if isinstance(pick, dict) and str(pick.get("label") or "").strip():
                number = item_index(pick.get("item") or 1, len(items))
                picks.setdefault(number, []).append(str(pick["label"]))
        return picks

    def _hear(self, number, question, answer, label, values, items, context) -> dict[str, Any]:
        if self.model:
            heard = self._hear_with_model(number, question, answer, items)
            if heard is not None:
                return heard
            context.note("the model could not read an answer; using rules")
        budget, rest, _ = read_money(answer)
        read = read_answer(rest, label, values.get((number, label), []), loose=budget is None)
        heard: dict[str, Any] = {"budget": budget} if budget else {}
        if read is None:
            return heard
        if "free" in read:
            return {**heard, "free": [{"item": number, "property": read["free"]}]}
        return {**heard, "needs": [{"item": number, "need": read["need"]}]}

    def _hear_with_model(self, number, question, answer, items) -> dict[str, Any] | None:
        prompt = (
            f"{HEAR}\n\nITEMS:\n"
            + "\n".join(f"item {n}: {i.name}" for n, i in enumerate(items, 1))
            + f"\n\nQUESTIONS AND ANSWERS:\n1. (about item {number + 1}) Q: {question}\nA: {answer}"
        )
        try:
            got = ask_json(self.model, prompt, 2500)
        except ModelError:
            return None
        if got is None:
            return None

        def index(entry: dict[str, Any]) -> int:
            return item_index(entry.get("item") or number + 1, len(items), number)

        rows = lambda key: [e for e in listed(got, key) if isinstance(e, dict)]  # noqa: E731
        budget = got.get("budget")
        return {
            "needs": [
                {"item": index(e), "need": str(e["need"]).strip()}
                for e in rows("needs")
                if str(e.get("need") or "").strip()
            ],
            "free": [
                {"item": index(e), "property": str(e["property"])}
                for e in rows("free")
                if e.get("property")
            ],
            "drops": [
                {"item": index(e), "property": str(e["property"])}
                for e in rows("drop")
                if e.get("property")
            ],
            "budget": budget if isinstance(budget, int | float) and budget > 0 else None,
        }
