"""The notes agent reads free text in store replies for changes to the task."""

from __future__ import annotations

import re
from typing import Any

from ..domain import Item
from ..models.base import Model, ModelError, ask_json
from ..prompts import NOTE
from ..runtime.agent import Agent

SKIP_KEYS = re.compile(r"^(title|name|description|reply|message|summary|details?|id|url)$", re.I)
BUDGET = re.compile(
    r"\bbudget\b[^.]*?\b(?:now|to|is|of|changed to|raised to|lowered to|reduced to)\b\D*?(\d+(?:\.\d+)?)",
    re.I,
)
SOLD_OUT = re.compile(
    r"\b(sold out|no longer available|out of stock|cannot be bought|can no longer be bought|discontinued|unavailable)\b",
    re.I,
)
PRICE_MOVED = re.compile(
    r"\bprice\b.*\b(changed|increased|decreased|risen|gone up|gone down|is now)\b", re.I
)
ADD_NEED = re.compile(
    r"\b(?:the )?([a-z][a-z ]{1,24}?) (?:must|should|has to|needs to) (?:now )?be ([^.;,]+)", re.I
)
DROP_NEED = re.compile(
    r"\b(?:no longer|doesn'?t|does not|don'?t|do not) (?:need|want|require|care about|mind)s? (?:the |a |an )?([a-z][a-z ]{1,24}?)(?: requirement)?(?:[.;,]|$)",
    re.I,
)


def loose_lines(observation: Any) -> list[str]:
    """Sentences in a reply that are not part of a listing record."""
    lines: list[str] = []
    if isinstance(observation, str):
        return [observation] if len(observation.split()) >= 4 else []
    if not isinstance(observation, dict):
        return []
    for key, value in observation.items():
        if isinstance(value, str) and len(value.split()) >= 4 and not SKIP_KEYS.match(str(key)):
            lines.append(value)
        elif isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            lines += [v for v in value if len(v.split()) >= 4]
    return lines


def read_note(line: str, known_ids: list[str]) -> dict[str, Any]:
    """Read one note with rules."""
    found: dict[str, Any] = {"adds": [], "drops": [], "gone": []}
    match = BUDGET.search(line)
    if match:
        found["budget"] = float(match.group(1))
    if SOLD_OUT.search(line):
        found["sold_out"] = True
        found["gone"] = [
            i for i in known_ids if re.search(rf"(?<![\w-]){re.escape(i)}(?![\w-])", line)
        ]
    if PRICE_MOVED.search(line):
        found["price_moved"] = True
    for match in ADD_NEED.finditer(line):
        prop = re.sub(r"^(?:the|it|its)\s+", "", match.group(1).strip(), flags=re.I)
        if prop and prop.lower() not in ("it", "this", "item", "order", "budget"):
            found["adds"].append(f"{prop.lower()}: {match.group(2).strip()}")
    for match in DROP_NEED.finditer(line):
        found["drops"].append(match.group(1).strip().lower())
    return found


def is_change(found: dict[str, Any]) -> bool:
    return any(found.get(k) for k in ("budget", "sold_out", "price_moved", "adds", "drops", "gone"))


class NotesAgent(Agent):
    """Handles `scan`: reads what the gateway logged since the last scan, once per note."""

    name = "notes"

    def __init__(self, model: Model | None = None):
        self.model = model
        self.seen: set[str] = set()
        self.scanned_to = 0

    def on_scan(self, payload: dict[str, Any], context) -> dict[str, Any]:
        log = context.request("gateway", "observations", {"since": self.scanned_to})
        self.scanned_to = log["step"]
        items = [Item.from_dict(i) for i in payload.get("items") or []]
        known = payload.get("known_ids") or []
        result: dict[str, Any] = {
            "budget": None,
            "adds": [],
            "drops": [],
            "gone": [],
            "sold_out": False,
            "price_moved": False,
            "notes": [],
        }
        for entry in log["entries"]:
            if entry["name"] in (payload.get("skip_tools") or []):
                continue
            for line in loose_lines(entry["observation"]):
                if line in self.seen:
                    continue
                self.seen.add(line)
                found = self._read(line, items, known)
                if is_change(found):
                    self._merge(result, found, line)
        if result["notes"]:
            context.note(f"store notes: {' | '.join(result['notes'])}")
        return result

    @staticmethod
    def _merge(result: dict[str, Any], found: dict[str, Any], line: str) -> None:
        if found.get("budget"):
            result["budget"] = found["budget"]
        result["adds"] += found.get("adds", [])
        result["drops"] += found.get("drops", [])
        result["gone"] += found.get("gone", [])
        result["sold_out"] = result["sold_out"] or bool(found.get("sold_out"))
        result["price_moved"] = result["price_moved"] or bool(found.get("price_moved"))
        result["notes"].append(line)

    def _read(self, line: str, items: list[Item], known: list[str]) -> dict[str, Any]:
        if not self.model:
            return read_note(line, known)
        ledger = "\n".join(
            f"item {n}: {i.name}: {'; '.join(i.needs())}" for n, i in enumerate(items, 1)
        )
        prompt = f"{NOTE}\n\nITEMS AND REQUIREMENTS NOW:\n{ledger}\n\nTHE NOTE:\n{line}"
        try:
            got = ask_json(self.model, prompt, 2500)
        except ModelError:
            return read_note(line, known)
        if got is None:
            return read_note(line, known)
        if got.get("kind") not in (None, "change"):
            return {}
        budget = got.get("budget")
        rows = lambda key: [e for e in got.get(key) or [] if isinstance(e, dict) and e.get("need")]  # noqa: E731
        return {
            "budget": budget if isinstance(budget, int | float) and budget > 0 else None,
            "adds": [str(e["need"]).strip() for e in rows("add")],
            "drops": [str(e["need"]).strip() for e in rows("drop")],
            "gone": [str(g) for g in got.get("gone") or [] if str(g) in known],
            "sold_out": got.get("sold_out") is True,
            "price_moved": got.get("price_moved") is True,
        }
