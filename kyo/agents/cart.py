"""The cart agent is the only agent that changes the cart, and it notices when it drifts."""

from __future__ import annotations

import re
from typing import Any

from ..domain import Item, Listing
from ..fit import same_kind
from ..runtime.agent import Agent
from ..schema import kinds, text_params

REMOVAL = re.compile(r"remov|delet|drop|clear|take", re.I)


def _plan_calls(schema, lines, wanted, drop) -> list[dict[str, Any]] | None:
    """Tool arguments that move the cart to the wanted lines, whichever way the tool works."""
    props = schema.get("properties") or {}
    target = next((k for k in schema.get("required") or [] if k in text_params(schema)), None)
    target = target or next(iter(text_params(schema)), None)
    count = next(
        (k for k, s in props.items() if isinstance(s, dict) and "integer" in kinds(s)), None
    )
    mode = next(
        (
            k
            for k, s in props.items()
            if isinstance(s, dict) and isinstance(s.get("enum"), list) and len(s["enum"]) >= 2
        ),
        None,
    )
    if not target:
        return None
    sets = bool(count) and not mode and props[count].get("minimum") == 0
    calls: list[dict[str, Any]] = []
    if mode:
        options = [str(o) for o in props[mode]["enum"]]
        off = next((o for o in options if REMOVAL.search(o)), options[1])
        on = next(o for o in options if o != off)
    for ident in drop:
        have = lines.get(ident, 1)
        if mode:
            calls.append({target: ident, mode: off, **({count: have} if count else {})})
        elif sets:
            calls.append({target: ident, count: 0})
        elif count and isinstance(props[count].get("minimum"), int) and props[count]["minimum"] < 0:
            calls.append({target: ident, count: -have})
        else:
            return None
    for ident, want in wanted.items():
        have = lines.get(ident, 0)
        if have == want:
            continue
        if sets:
            calls.append({target: ident, count: want})
        elif mode:
            if have and want < have:
                calls.append({target: ident, mode: off, **({count: have - want} if count else {})})
            else:
                calls.append({target: ident, mode: on, **({count: want - have} if count else {})})
        elif count:
            calls.append({target: ident, count: want - have})
        elif not have:
            calls.append({target: ident})
        else:
            return None
    return calls


class CartAgent(Agent):
    """Handles `set`, `sync`, `lines` and `faults`. Its lines record what it has done."""

    name = "cart"

    def __init__(self) -> None:
        self.lines: dict[str, int] = {}

    def on_set(self, payload: dict[str, Any], context) -> dict[str, Any]:
        """Move the cart to exactly the wanted lines: {tool, schema, lines: {id: quantity}}."""
        tool, schema = payload["tool"], payload.get("schema") or {}
        wanted = {str(k): int(v) for k, v in payload["lines"].items()}
        drop = [i for i in self.lines if i not in wanted]
        calls = _plan_calls(schema, self.lines, wanted, drop)
        if calls is None:
            return {"ok": False, "error": f"cannot work out how to use {tool}", "lines": self.lines}
        target = next(
            (k for k, v in (calls[0] if calls else {}).items() if isinstance(v, str)), None
        )
        errors = []
        for args in calls:
            reply = context.request("gateway", "call", {"name": tool, "args": args})
            problem = self._problem(reply)
            ident = str(args.get(target))
            if problem:
                errors.append(f"{ident}: {problem}")
            elif ident in wanted:
                self.lines[ident] = wanted[ident]
            else:
                self.lines.pop(ident, None)
            if reply.get("finished"):
                break
        return {"ok": not errors, "errors": errors, "lines": dict(self.lines), "calls": len(calls)}

    @staticmethod
    def _problem(reply: dict[str, Any]) -> str | None:
        if not reply.get("ok"):
            return str(reply.get("error"))
        observation = reply.get("observation")
        if isinstance(observation, dict) and observation.get("error"):
            return str(observation["error"])
        return None

    def on_sync(self, payload: dict[str, Any], context) -> dict[str, Any]:
        """Read the store's cart. The store is the authority on what the cart holds."""
        reply = context.request("gateway", "call", {"name": payload["tool"], "args": {}})
        if not reply.get("ok"):
            return {"ok": False, "error": reply.get("error"), "lines": dict(self.lines)}
        found = self._read_lines(reply["observation"], payload.get("known") or [])
        if found is not None:
            self.lines = found
        return {"ok": True, "lines": dict(self.lines), "view": reply["observation"]}

    @staticmethod
    def _read_lines(observation: Any, known: list[str]) -> dict[str, int] | None:
        if not isinstance(observation, dict):
            return None
        for value in observation.values():
            if isinstance(value, list) and (not value or isinstance(value[0], dict)):
                lines: dict[str, int] = {}
                for row in value:
                    if not isinstance(row, dict):
                        continue
                    ident = next((str(v) for v in row.values() if str(v) in known), None)
                    if ident is None:
                        ident = next(
                            (str(v) for k, v in row.items() if "id" in str(k).lower()), None
                        )
                    amount = next(
                        (
                            v
                            for k, v in row.items()
                            if isinstance(v, int) and re.search(r"qty|quantity|count|unit", k, re.I)
                        ),
                        1,
                    )
                    if ident:
                        lines[ident] = amount
                return lines
        return None

    def on_lines(self, payload, context) -> dict[str, Any]:
        return {"lines": dict(self.lines)}

    def on_faults(self, payload: dict[str, Any], context) -> dict[str, Any]:
        """Compare the cart with the plan: missing, extra, wrong-quantity, sold-out, over-budget."""
        items = [Item.from_dict(i) for i in payload["items"]]
        records = {r["id"]: Listing.from_dict(r) for r in payload["records"]}
        budget, scope = payload.get("budget"), payload.get("budget_scope", "total")
        faults: list[str] = []
        claimed: set[str] = set()
        for number, item in enumerate(items, 1):
            mine = [
                i
                for i in self.lines
                if i in records
                and i not in claimed
                and same_kind(item.name, item.shelf, records[i].category)
            ]
            if not mine:
                faults.append(f"item {number} ({item.name}) has no line in the cart")
                continue
            claimed.update(mine)
            if len(mine) > 1:
                faults.append(f"item {number} ({item.name}) has {len(mine)} lines: keep one")
            for ident in mine:
                if self.lines[ident] != item.quantity:
                    faults.append(
                        f"{ident} is held at {self.lines[ident]}, "
                        f"item {number} ({item.name}) wants {item.quantity}"
                    )
        for ident in self.lines:
            if ident not in claimed:
                faults.append(f"{ident} is not something the shopper asked for")
            elif not records[ident].in_stock:
                faults.append(f"{ident} is sold out")
        priced = {i: records[i].price for i in self.lines if i in records}
        if budget is not None and all(p is not None for p in priced.values()):
            if scope == "each":
                faults += [
                    f"{i} costs {p:g}, over the limit of {budget:g} for each item"
                    for i, p in priced.items()
                    if p > budget + 1e-9
                ]
            else:
                total = sum(p * self.lines[i] for i, p in priced.items())
                if total > budget + 1e-9:
                    faults.append(f"the total is {total:g}, over the budget of {budget:g}")
        return {"faults": faults}
