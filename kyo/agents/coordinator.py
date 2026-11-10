"""The coordinator runs the shopping workflow by asking the other agents for results.

It is the only agent that knows the overall plan, and it can see only what they reply.
"""

from __future__ import annotations

from typing import Any

from ..domain import Brief
from ..runtime.agent import Agent, AgentError

READ_PER_ITEM = 4
READ_BATCH = 4
MAX_ROUNDS = 6
FIRST_PAGES = 2


class Coordinator(Agent):
    """Handles `run`: {request} becomes {outcome, lines, total, reason, ...}."""

    name = "coordinator"

    def on_run(self, payload: dict[str, Any], context) -> dict[str, Any]:
        episode = _Episode(context)
        return episode.run(payload["request"])


class _Episode:
    """The state of one shopping run."""

    def __init__(self, context):
        self.ctx = context
        self.schemas: dict[str, dict[str, Any]] = {}
        self.tools: list[dict[str, Any]] = []
        self.brief = Brief()
        self.excluded: set[str] = set()
        self.cheapest_all = False
        self.searched: set[str] = set()
        self.model_reviews: list[str] = []
        self.buys_refused = 0
        self.prices_stale = False

    # ------------------------------------------------------------ helpers

    def ask(self, agent: str, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.ctx.request(agent, kind, payload or {})

    def tool(self, role: str) -> str | None:
        return self.brief.roles.get(role)

    def left(self) -> int:
        return int(self.ask("gateway", "status")["left"])

    def finished(self) -> bool:
        return bool(self.ask("gateway", "status")["finished"])

    def reserve(self) -> int:
        """Steps to keep for setting the cart and ending."""
        messages = sum(int(s.get("messages") or 0) for s in self.brief.process)
        return len(self.brief.items) + messages + 2

    def known_ids(self) -> list[str]:
        return [r["id"] for r in self.ask("catalog", "records")["records"]]

    # ------------------------------------------------------------ workflow

    def run(self, request: str) -> dict[str, Any]:
        self.tools = self.ask("gateway", "tools")["tools"]
        self.schemas = {
            t["function"]["name"]: t["function"].get("parameters") or {} for t in self.tools
        }
        read = self.ask("request", "read", {"request": request, "tools": self.tools})
        self.brief = Brief.from_dict(read["brief"])
        if self.brief.steps and self.brief.steps >= 10:
            self.ask("gateway", "limit_steps", {"steps": self.brief.steps})
        if not self.tool("buy"):
            return self.result("unfinished", "this store has no way to check out")
        if not self.tool("search"):
            return self.result("unfinished", "this store has no way to search")
        if self.brief.lookups:
            needs = "; ".join(self.brief.lookups)
            return self.decline(
                f"this depends on another listing, which cannot be checked: {needs}"
            )
        for number in range(len(self.brief.items)):
            self.search(number, FIRST_PAGES)
            self.read_candidates(number)
        self.apply_notes()
        self.interview()
        return self.shop()

    def search(self, number: int, pages: int, terms: str | None = None, cap: float | None = None):
        item = self.brief.items[number]
        mark = f"{number}|{terms}|{cap}|{pages}"
        if mark in self.searched or self.left() <= self.reserve():
            return
        self.searched.add(mark)
        payload = {
            "tool": self.tool("search"),
            "schema": self.schemas[self.tool("search")],
            "item": item.to_dict(),
            "max_pages": pages,
            "keep": self.reserve(),
        }
        if terms:
            payload["terms"] = terms
        if cap is not None:
            payload["max_price"] = cap
        self.ask("catalog", "find", payload)

    def read_tool(self) -> str | None:
        return self.tool("open")

    def open_listings(self, ids: list[str]) -> list[str]:
        tool = self.read_tool()
        if not tool or not ids or self.left() <= self.reserve():
            return []
        payload = {
            "tool": tool,
            "schema": self.schemas[tool],
            "ids": ids,
            "keep": self.reserve(),
        }
        return self.ask("catalog", "open", payload)["opened"]

    def read_candidates(self, number: int) -> None:
        """Read a few listings of an item, so the interviewer knows what they carry."""
        item = self.brief.items[number]
        records = self.ask("catalog", "records", {"item": item.to_dict()})["records"]
        unread = [r for r in records if not r["opened"] and r["in_stock"]]
        if self.brief.pick == "cheapest":
            unread.sort(key=lambda r: r["price"] if r["price"] is not None else float("inf"))
        self.open_listings([r["id"] for r in unread[:READ_PER_ITEM]])

    def interview(self) -> None:
        tool = self.tool("ask")
        reserved = sum(int(s.get("messages") or 0) for s in self.brief.process)
        allowance = self.brief.messages - reserved
        if not tool or allowance <= 0 or self.left() <= self.reserve() + 1:
            return
        reply = self.ask(
            "interview",
            "run",
            {
                "items": [i.to_dict() for i in self.brief.items],
                "tool": tool,
                "schema": self.schemas[tool],
                "messages": allowance,
                "keep": self.reserve(),
                "request": self.brief.request,
            },
        )
        for need in reply.get("needs") or []:
            self.brief.items[need["item"]].add_need(need["need"])
        for free in reply.get("free") or []:
            self.brief.items[free["item"]].free.append(free["property"])
        for drop in reply.get("drops") or []:
            self.brief.items[drop["item"]].drop_property(drop["property"])
        if reply.get("budget"):
            self.brief.budget = reply["budget"]
        self.apply_notes()

    def apply_notes(self) -> dict[str, Any]:
        skip = [self.tool("ask")] if self.tool("ask") else []
        notes = self.ask(
            "notes",
            "scan",
            {
                "items": [i.to_dict() for i in self.brief.items],
                "known_ids": self.known_ids(),
                "skip_tools": skip,
            },
        )
        if notes.get("budget"):
            self.brief.budget = notes["budget"]
        if notes.get("price_moved"):
            self.prices_stale = True
        for need in notes.get("adds") or []:
            for item in self.brief.items:
                item.drop_property(need)
                item.add_need(need)
        for prop in notes.get("drops") or []:
            for item in self.brief.items:
                item.drop_property(prop)
        if notes.get("gone"):
            self.ask("catalog", "mark_sold", {"ids": notes["gone"]})
        return notes

    def shop(self) -> dict[str, Any]:
        reason = "nothing in the store meets every requirement"
        for _ in range(MAX_ROUNDS):
            if self.finished():
                return self.result("unfinished", "the store ended the episode")
            picks, missing = self.choose_all()
            if missing:
                return self.decline(f"no listing fits: {', '.join(missing)}")
            lines = self.lines_for(picks)
            if self.over_total_budget(lines) and not self.cheapest_all:
                self.cheapest_all = True
                continue
            if self.over_total_budget(lines):
                return self.decline("the cheapest fitting listings exceed the budget")
            setting = self.set_cart(lines)
            notes = self.apply_notes()
            if not setting.get("ok") or notes.get("sold_out") or notes.get("gone"):
                self.handle_loss(lines, notes, setting)
                continue
            if self.changed_requirements(notes):
                continue
            verdict = self.review(lines, picks)
            if not verdict["ok"]:
                reason = "; ".join(verdict["problems"]) or reason
                if not self.handle_problems(lines, verdict):
                    return self.decline(reason)
                continue
            outcome = self.finish_order(lines)
            if outcome is not None:
                return outcome
        return self.decline(f"could not settle on a cart: {reason}")

    @staticmethod
    def changed_requirements(notes: dict[str, Any]) -> bool:
        return bool(notes.get("adds") or notes.get("drops") or notes.get("budget"))

    def over_total_budget(self, lines: dict[str, int]) -> bool:
        budget = self.brief.budget
        if budget is None or self.brief.budget_scope == "each":
            return False
        records = self.ask("catalog", "records", {"ids": list(lines)})["records"]
        prices = {r["id"]: r["price"] for r in records}
        if any(prices.get(i) is None for i in lines):
            return False
        return sum(prices[i] * n for i, n in lines.items()) > budget + 1e-9

    def lines_for(self, picks: dict[int, str]) -> dict[str, int]:
        """Cart lines for the picks. Items sharing a listing add up their quantities."""
        lines: dict[str, int] = {}
        for number, pick in picks.items():
            lines[pick] = lines.get(pick, 0) + self.brief.items[number].quantity
        return lines

    def choose_all(self) -> tuple[dict[int, str], list[str]]:
        """Choose for every item, giving each its own listing when one fits."""
        picks: dict[int, str] = {}
        missing: list[str] = []
        for number, item in enumerate(self.brief.items):
            taken = set(picks.values())
            choice = self.choose(number, taken)
            if choice is None and taken:
                choice = self.choose(number)
            if choice is None:
                missing.append(item.name)
            else:
                picks[number] = choice
        return picks, missing

    def choose(self, number: int, avoid: set[str] | None = None) -> str | None:
        """Choose for one item, reading listings and widening the search as needed."""
        widened = 0
        while True:
            reply = self.select(number, avoid or set())
            if reply["need_open"]:
                if self.open_listings(reply["need_open"][:READ_BATCH]):
                    continue
            if reply["choice"]:
                return reply["choice"]
            widened += 1
            if widened > 3 or self.left() <= self.reserve():
                return None
            self.widen(number, widened)

    def widen(self, number: int, attempt: int) -> None:
        """Look further: all pages of the category, then the product's name, then its last word."""
        item = self.brief.items[number]
        if attempt == 1:
            self.search(number, 6)
        elif attempt == 2:
            self.search(number, 3, terms=item.name)
        else:
            self.search(number, 3, terms=item.name.split()[-1])

    def select(self, number: int, avoid: set[str]) -> dict[str, Any]:
        item = self.brief.items[number]
        records = self.ask("catalog", "records", {"item": item.to_dict()})["records"]
        pick = "cheapest" if self.cheapest_all else self.brief.pick
        budget = self.brief.budget
        if budget is not None and self.brief.budget_scope == "total" and len(self.brief.items) > 1:
            budget = None  # a whole-order limit cannot be judged for one line of several
        return self.ask(
            "selection",
            "choose",
            {
                "item": item.to_dict(),
                "records": records,
                "pick": pick,
                "pick_measure": self.brief.pick_measure,
                "budget": budget,
                "budget_scope": self.brief.budget_scope,
                "excluded": sorted(self.excluded | avoid),
                "in_stock_only": self.brief.in_stock_only,
                "wishes": self.brief.wishes,
                "unverified": self.brief.lookups,
            },
        )

    def set_cart(self, lines: dict[str, int]) -> dict[str, Any]:
        tool = self.tool("cart")
        if not tool:
            return {"ok": False, "errors": ["this store has no cart tool"], "lines": {}}
        return self.ask("cart", "set", {"tool": tool, "schema": self.schemas[tool], "lines": lines})

    def handle_loss(self, lines, notes, setting) -> None:
        """A cart line could not be held: rule it out and choose again."""
        lost = set(notes.get("gone") or [])
        for error in setting.get("errors") or []:
            lost.add(str(error).split(":", 1)[0])
        if not lost and notes.get("sold_out"):
            lost = self.find_lost(lines)
        self.excluded |= lost
        if lost:
            self.ask("catalog", "mark_sold", {"ids": sorted(lost)})

    def find_lost(self, lines) -> set[str]:
        """The store said a line sold out without naming it: re-read the lines to find it."""
        tool = self.read_tool()
        if tool and self.left() > len(lines) + self.reserve():
            self.ask(
                "catalog",
                "refresh",
                {"tool": tool, "schema": self.schemas[tool], "ids": list(lines)},
            )
        records = self.ask("catalog", "records", {"ids": list(lines)})["records"]
        gone = {r["id"] for r in records if not r["in_stock"]}
        return gone or (set(lines) if len(lines) == 1 else set())

    def review(self, lines: dict[str, int], picks: dict[int, str]) -> dict[str, Any]:
        """Re-read the lines for their current price and stock, then have the cart judged."""
        tool = self.read_tool()
        due = sum(
            int(s.get("messages") or 0) for s in self.brief.process if s.get("when") == "order"
        )
        if tool and self.left() > len(lines) + due:
            self.ask(
                "catalog",
                "refresh",
                {"tool": tool, "schema": self.schemas[tool], "ids": list(lines)},
            )
            self.prices_stale = False
        records = self.ask("catalog", "records")["records"]
        verdict = self.ask(
            "review",
            "cart",
            {"brief": self.brief.to_dict(), "lines": lines, "records": records, "assign": picks},
        )
        self.model_reviews.append(verdict["model_review"])
        if self.prices_stale and self.brief.budget is not None:
            verdict["ok"] = False
            verdict["problems"].append("a price changed and there are no steps to read it again")
        return verdict

    def handle_problems(self, lines: dict[str, int], verdict: dict[str, Any]) -> bool:
        """Rule out what the review faulted. False when nothing can be changed."""
        out = set(verdict.get("ruled_out") or [])
        better = verdict.get("better") or []
        if better:
            out |= set(lines)
        if not out and verdict["model_review"] == "failed":
            out = set(lines)
        new = out - self.excluded
        self.excluded |= out
        return bool(new)

    def cart_summary(self, lines: dict[str, int]) -> dict[str, Any]:
        records = self.ask("catalog", "records", {"ids": list(lines)})["records"]
        prices = {r["id"]: r["price"] for r in records}
        known = all(prices.get(i) is not None for i in lines)
        total = round(sum(prices[i] * n for i, n in lines.items()), 2) if known else None
        return {"lines": lines, "total": total}

    def perform(self, stage: str, lines: dict[str, int] | None) -> dict[str, Any]:
        """Have the process agent do the request's steps. It gets no tool that changes the
        cart or ends the episode, so the reviewed cart is the one checked out."""
        tool = self.tool("ask")
        locked = {self.tool(role) for role in ("cart", "buy", "refuse")} - {None}
        tools = [t for t in self.tools if t["function"]["name"] not in locked]
        return self.ask(
            "process",
            "perform",
            {
                "stage": stage,
                "steps": self.brief.process,
                "cart": self.cart_summary(lines) if lines else {},
                "ask_tool": tool,
                "ask_schema": self.schemas.get(tool or "", {}),
                "request": self.brief.request,
                "tools": tools,
            },
        )

    def finish_order(self, lines: dict[str, int]) -> dict[str, Any] | None:
        """Check out, or return None to choose again after a change."""
        gate = self.perform("order", lines)
        if not gate["proceed"]:
            for need in gate.get("needs") or []:
                index = min(max(1, int(need.get("item") or 1)), len(self.brief.items)) - 1
                self.brief.items[index].add_need(str(need["need"]))
            if gate.get("needs"):
                return None
            self.clear_cart()
            return self.decline(
                f"the shopper did not approve: {gate.get('reason', '')}", gated=True
            )
        reply = self.ask(
            "gateway",
            "call",
            {"name": self.tool("buy"), "args": self.brief.buy_args},
        )
        if not reply.get("ok"):
            return self.result("unfinished", str(reply.get("error")))
        observation = reply.get("observation")
        observation = observation if isinstance(observation, dict) else {}
        if reply.get("finished"):
            if observation.get("accepted") is False:
                note = observation.get("note") or "the store did not accept the order"
                return self.result("declined", str(note))
            return self.result("purchased", "the order was placed", lines, observation)
        self.buys_refused += 1
        notes = self.apply_notes()
        if notes.get("gone") or notes.get("sold_out"):
            self.handle_loss(lines, notes, {})
        if self.buys_refused >= 3 or self.left() <= 1:
            return self.decline("the store kept refusing the order")
        return None

    def clear_cart(self) -> None:
        tool = self.tool("cart")
        if tool:
            try:
                self.ask("cart", "set", {"tool": tool, "schema": self.schemas[tool], "lines": {}})
            except AgentError as error:
                self.ctx.note(f"could not empty the cart: {error}")

    def decline(self, reason: str, gated: bool = False) -> dict[str, Any]:
        """Audit the decision, then end without buying if it stands."""
        if not gated:
            rescued = self.audit()
            if rescued is not None:
                return rescued
            gate = self.perform("end", None)
            if not gate["proceed"]:
                self.ctx.note(f"ending without the process steps: {gate.get('reason', '')}")
        tool = self.tool("refuse")
        if not tool:
            return self.result("unfinished", f"{reason}; this store has no way to decline")
        args = dict(self.brief.refuse_args)
        schema = self.schemas.get(tool) or {}
        if "message" in (schema.get("properties") or {}):
            args["message"] = "Sorry, nothing in the store meets all of your requirements."
        reply = self.ask("gateway", "call", {"name": tool, "args": args})
        if not reply.get("ok"):
            return self.result("unfinished", str(reply.get("error")))
        return self.result("declined", reason)

    def audit(self) -> dict[str, Any] | None:
        """Read rows the audit says could fit, and buy if the audit then finds a fit for all."""
        records = self.ask("catalog", "records")["records"]
        verdict = self.ask("review", "decline", {"brief": self.brief.to_dict(), "records": records})
        self.model_reviews.append(verdict["model_review"])
        unread = [i for i in verdict["unread"] if i not in self.excluded]
        if unread and self.open_listings(unread):
            records = self.ask("catalog", "records")["records"]
            verdict = self.ask(
                "review", "decline", {"brief": self.brief.to_dict(), "records": records}
            )
        fits = {k: [i for i in v if i not in self.excluded] for k, v in verdict["fits"].items()}
        if len([k for k, v in fits.items() if v]) < len(self.brief.items) or self.buys_refused:
            return None
        picks = {n: fits[str(n + 1)][0] for n in range(len(self.brief.items))}
        lines = self.lines_for(picks)
        if self.over_total_budget(lines):
            return None
        if not self.set_cart(lines).get("ok"):
            return None
        verdict = self.review(lines, picks)
        if not verdict["ok"]:
            self.clear_cart()
            return None
        return self.finish_order(lines)

    def result(
        self,
        outcome: str,
        reason: str,
        lines: dict[str, int] | None = None,
        observation: dict[str, Any] | None = None,
    ):
        status = self.ask("gateway", "status")
        summary = self.cart_summary(lines) if lines else {"lines": {}, "total": None}
        reviews = [r for r in self.model_reviews if r != "none"]
        return {
            "outcome": outcome,
            "reason": reason,
            "lines": summary["lines"],
            "total": summary["total"],
            "steps": status["step"],
            "order_id": (observation or {}).get("order_id"),
            "brief": self.brief.to_dict(),
            "unverified": list(self.brief.lookups),
            "model_review": reviews[-1] if reviews else "none",
        }
