"""The selection agent chooses the listing for each item, and can say why others failed."""

from __future__ import annotations

import re
from typing import Any

from ..domain import Item, Listing
from ..fit import check_need, key_names_property, same_kind
from ..runtime.agent import Agent


def stated_measure(listing: Listing, measure: str) -> float | None:
    """The number a listing states for a measure, or None when it states none."""
    for key, value in listing.attributes.items():
        if key_names_property(key, measure):
            match = re.search(r"-?\d+(?:\.\d+)?", str(value))
            if match:
                return float(match.group())
    return None


class SelectionAgent(Agent):
    """Handles `choose`. It keeps no memory: everything it needs is in the message."""

    name = "selection"

    def on_choose(self, payload: dict[str, Any], context) -> dict[str, Any]:
        item = Item.from_dict(payload["item"])
        records = [Listing.from_dict(r) for r in payload["records"]]
        pick = payload.get("pick", "any")
        measure = payload.get("pick_measure", "")
        budget = payload.get("budget")
        scope = payload.get("budget_scope", "total")
        excluded = set(payload.get("excluded") or [])
        unverified = set(payload.get("unverified") or [])
        needs = [n for n in item.needs() if n not in unverified]

        fits: list[Listing] = []
        unread: list[Listing] = []
        rejected: dict[str, list[str]] = {}
        for record in records:
            problems: list[str] = []
            if record.id in excluded:
                problems.append("ruled out earlier")
            if not same_kind(item.name, item.shelf, record.category):
                problems.append(f"{record.category} is not the requested kind")
            if not record.in_stock and payload.get("in_stock_only", True):
                problems.append("sold out")
            if budget is not None and record.price is not None:
                cost = record.price if scope == "each" else record.price * item.quantity
                if cost > budget + 1e-9:
                    problems.append(f"costs {cost:g}, over the budget of {budget:g}")
            undecided = False
            for need in needs:
                verdict = check_need(need, record)
                if verdict.met is False:
                    problems.append(verdict.reason)
                elif verdict.met is None:
                    undecided = True
            if problems:
                rejected[record.id] = problems
            elif undecided or (pick in ("most", "least") and not record.opened):
                unread.append(record)
            else:
                fits.append(record)

        choice, need_open = self._decide(fits, unread, pick, measure, payload.get("wishes") or [])
        context.note(
            f"{item.name}: {len(fits)} fit, {len(unread)} unread, {len(rejected)} rejected",
            choice=choice,
        )
        return {
            "choice": choice,
            "fits": [r.id for r in fits],
            "need_open": need_open,
            "rejected": rejected,
        }

    @staticmethod
    def _decide(fits, unread, pick, measure, wishes) -> tuple[str | None, list[str]]:
        """Return the choice, or the unread listings that must be read before choosing."""
        by_price = lambda r: r.price if r.price is not None else float("inf")  # noqa: E731
        if pick == "cheapest":
            best = min(fits, key=by_price, default=None)
            cheaper = [r for r in unread if best is None or by_price(r) < by_price(best)]
            if cheaper:
                return None, [r.id for r in sorted(cheaper, key=by_price)]
            return (best.id if best else None), []
        if pick in ("most", "least"):
            if unread:
                return None, [r.id for r in unread]
            stated = [(stated_measure(r, measure), r) for r in fits]
            stated = [(value, r) for value, r in stated if value is not None]
            if not stated:
                return None, []
            pair = (max if pick == "most" else min)(stated, key=lambda p: p[0])
            return pair[1].id, []
        if fits:
            return _best_wish(fits, wishes).id, []
        return None, [r.id for r in unread]


def _best_wish(fits: list[Listing], wishes: list[str]) -> Listing:
    """Wishes only break ties between listings that fit."""
    if not wishes:
        return fits[0]

    def wish_score(record: Listing) -> int:
        return sum(1 for wish in wishes if check_need(wish, record).met)

    return max(fits, key=wish_score)
