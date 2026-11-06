"""The review agent independently judges carts before checkout and declines before they end.

It re-checks everything from the records, trusting no other agent's conclusion. When a model
is available it also asks for a second opinion, and it always reports what became of that
opinion: "passed", "failed", "unavailable", or "none" when there is no model.
"""

from __future__ import annotations

import json
from typing import Any

from ..compat import fit_problem
from ..domain import Brief, Listing
from ..fit import check_need, same_kind
from ..models.base import Model, ModelError, ask_json
from ..prompts import AUDIT, CHECK
from ..runtime.agent import Agent


def _ledger(brief: Brief) -> str:
    lines = []
    for number, item in enumerate(brief.items, 1):
        needs = "; ".join(item.needs()) or "none beyond the kind of product"
        lines.append(f"item {number}: {item.name}, quantity {item.quantity}; requirements: {needs}")
    if brief.budget is not None:
        lines.append(f"budget: at most {brief.budget} ({brief.budget_scope})")
    lines.append(f"pick: {brief.pick} {brief.pick_measure}".strip())
    return "\n".join(f"- {line}" for line in lines)


class ReviewAgent(Agent):
    """Handles `cart` and `decline`."""

    name = "review"

    def __init__(self, model: Model | None = None):
        self.model = model

    def on_cart(self, payload: dict[str, Any], context) -> dict[str, Any]:
        brief = Brief.from_dict(payload["brief"])
        lines = {str(k): int(v) for k, v in payload["lines"].items()}
        records = {r["id"]: Listing.from_dict(r) for r in payload["records"]}
        problems, ruled_out = self._rule_problems(brief, lines, records)
        opinion, better = "none", []
        if self.model and not problems:
            opinion, extra, better = self._model_opinion(brief, lines, records)
            problems += extra
        context.note(f"cart review: {len(problems)} problem(s), model {opinion}")
        return {
            "ok": not problems,
            "problems": problems,
            "ruled_out": ruled_out,
            "better": better,
            "model_review": opinion,
        }

    def _rule_problems(self, brief, lines, records) -> tuple[list[str], list[str]]:
        problems: list[str] = []
        ruled_out: list[str] = []
        unverified = set(brief.lookups)
        for ident, quantity in lines.items():
            record = records.get(ident)
            if record is None:
                problems.append(f"{ident} has no record to check")
                continue
            owners = [i for i in brief.items if same_kind(i.name, i.shelf, record.category)]
            if not owners:
                problems.append(f"{ident} is not a kind the shopper asked for")
                ruled_out.append(ident)
                continue
            item = owners[0]
            if quantity != item.quantity:
                problems.append(f"{ident} is held at {quantity}, {item.name} wants {item.quantity}")
            if not record.in_stock:
                problems.append(f"{ident} is sold out")
                ruled_out.append(ident)
            for need in item.needs():
                if need in unverified:
                    continue
                verdict = check_need(need, record)
                if verdict.met is not True:
                    problems.append(f"{ident} does not meet {need}: {verdict.reason}")
                    ruled_out.append(ident)
        for item in brief.items:
            if not any(
                i in records and same_kind(item.name, item.shelf, records[i].category)
                for i in lines
            ):
                problems.append(f"{item.name} has no line in the cart")
        problems += self._budget_problems(brief, lines, records)
        problems += self._fit_problems(brief, lines, records, ruled_out)
        problems += self._pick_problems(brief, lines, records, ruled_out)
        return problems, list(dict.fromkeys(ruled_out))

    @staticmethod
    def _budget_problems(brief, lines, records) -> list[str]:
        if brief.budget is None:
            return []
        priced = {i: records[i].price for i in lines if i in records}
        if any(p is None for p in priced.values()):
            return ["a cart line has no known price, so the budget cannot be checked"]
        if brief.budget_scope == "each":
            return [
                f"{i} costs {p:g}, over the limit of {brief.budget:g} for each item"
                for i, p in priced.items()
                if p > brief.budget + 1e-9
            ]
        total = sum(p * lines[i] for i, p in priced.items())
        if total > brief.budget + 1e-9:
            return [f"the total is {total:g}, over the budget of {brief.budget:g}"]
        return []

    @staticmethod
    def _fit_problems(brief, lines, records, ruled_out) -> list[str]:
        problems = []
        for pair in brief.fits:
            part_item = brief.items[pair["item"] - 1]
            device_item = brief.items[pair["with"] - 1]
            of_kind = lambda item: [  # noqa: E731
                records[i]
                for i in lines
                if i in records and same_kind(item.name, item.shelf, records[i].category)
            ]
            for part in of_kind(part_item):
                for device in of_kind(device_item):
                    problem = fit_problem(part, device)
                    if problem:
                        problems.append(problem)
                        ruled_out.append(part.id)
        return problems

    @staticmethod
    def _pick_problems(brief, lines, records, ruled_out) -> list[str]:
        """A cheaper listing that clearly fits every requirement means the cart is wrong."""
        if brief.pick != "cheapest":
            return []
        problems = []
        unverified = set(brief.lookups)
        for ident in lines:
            record = records.get(ident)
            if record is None or record.price is None:
                continue
            owners = [i for i in brief.items if same_kind(i.name, i.shelf, record.category)]
            for item in owners[:1]:
                needs = [n for n in item.needs() if n not in unverified]
                for other in records.values():
                    if (
                        other.id != ident
                        and other.in_stock
                        and other.price is not None
                        and other.price < record.price
                        and same_kind(item.name, item.shelf, other.category)
                        and all(check_need(n, other).met is True for n in needs)
                    ):
                        problems.append(f"{other.id} also fits and is cheaper than {ident}")
                        ruled_out.append(ident)
                        break
        return problems

    def _model_opinion(self, brief, lines, records) -> tuple[str, list[str], list[str]]:
        cart = {i: records[i].to_dict() for i in lines if i in records}
        others = [r.to_dict() for i, r in records.items() if i not in lines][:15]
        prompt = (
            f"{CHECK}\n\nREQUEST:\n{brief.request}\n\nREQUIREMENTS IN FORCE:\n{_ledger(brief)}"
            f"\n\nCART:\n{json.dumps(lines)}\n\nRECORDS OF THE CART'S LISTINGS:\n{json.dumps(cart)}"
            f"\n\nOTHER LISTINGS SEEN:\n{json.dumps(others)}"
        )
        try:
            got = ask_json(self.model, prompt, 3000)
        except ModelError:
            return "unavailable", [], []
        if got is None:
            return "unavailable", [], []
        if got.get("ok") is not False:
            return "passed", [], []
        problems = [str(p) for p in got.get("problems") or []] or ["the model review failed"]
        better = [str(b) for b in got.get("better") or [] if str(b) in records]
        return "failed", problems, better

    def on_decline(self, payload: dict[str, Any], context) -> dict[str, Any]:
        """Before ending without buying: do listings seen fit after all, or remain unread?"""
        brief = Brief.from_dict(payload["brief"])
        records = [Listing.from_dict(r) for r in payload["records"]]
        unverified = set(brief.lookups)
        fits: dict[str, list[str]] = {}
        unread: list[str] = []
        missing: list[str] = []
        for number, item in enumerate(brief.items, 1):
            needs = [n for n in item.needs() if n not in unverified]
            mine = [
                r for r in records if r.in_stock and same_kind(item.name, item.shelf, r.category)
            ]
            mine = [r for r in mine if self._within_budget(brief, item, r)]
            verdicts = {r.id: [check_need(n, r).met for n in needs] for r in mine}
            good = [r.id for r in mine if all(v is True for v in verdicts[r.id])]
            if good:
                fits[str(number)] = good[:4]
            else:
                missing.append(item.name)
            unread += [r.id for r in mine if not r.opened and None in verdicts[r.id]]
        opinion = "none"
        if self.model and not fits:
            opinion, more = self._audit_with_model(brief, records)
            for key, ids in more.items():
                fits.setdefault(str(key), ids)
        context.note(f"decline audit: {len(fits)} item(s) with fits, {len(unread)} unread")
        return {
            "fits": fits,
            "unread": unread[:6],
            "missing": missing,
            "model_review": opinion,
        }

    @staticmethod
    def _within_budget(brief, item, record) -> bool:
        if brief.budget is None or record.price is None:
            return True
        cost = record.price if brief.budget_scope == "each" else record.price * item.quantity
        return cost <= brief.budget + 1e-9

    def _audit_with_model(self, brief, records) -> tuple[str, dict[str, list[str]]]:
        shown = [
            ("OPENED " if r.opened else "ROW ") + json.dumps(r.to_dict())
            for r in records[-30:]
            if r.in_stock
        ]
        prompt = (
            f"{AUDIT}\n\nREQUEST:\n{brief.request}\n\nREQUIREMENTS IN FORCE:\n{_ledger(brief)}"
            "\n\nLISTING RECORDS SEEN (newest first):\n" + "\n".join(reversed(shown))
        )
        try:
            got = ask_json(self.model, prompt, 4000)
        except ModelError:
            return "unavailable", {}
        if got is None or not isinstance(got.get("fits"), dict):
            return "unavailable", {}
        known = {r.id for r in records}
        named = {
            str(k): [str(i) for i in v if str(i) in known][:4]
            for k, v in got["fits"].items()
            if isinstance(v, list)
        }
        return "passed", {k: v for k, v in named.items() if v}
