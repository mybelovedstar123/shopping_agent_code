"""The request agent turns a shopper's words into a structured brief."""

from __future__ import annotations

import json
import re
from typing import Any

from ..domain import PICK_RULES, ROLES, Brief, Item
from ..fit import words
from ..models.base import Model, ModelError, ask_json, listed
from ..prompts import BRIEF, UNMIX
from ..runtime.agent import Agent
from ..schema import kinds

COLOURS = (
    "red orange yellow green blue purple pink brown black white grey gray silver gold beige "
    "navy teal ivory cream maroon olive turquoise charcoal"
).split()
MATERIALS = (
    "wood wooden oak pine walnut bamboo ceramic glass metal steel aluminium aluminum brass copper "
    "plastic leather cotton linen wool silk denim wicker rattan stone marble rubber silicone"
).split()
SIZES = {"tall": "height", "high": "height", "long": "length", "wide": "width", "deep": "depth"}
MEASURED = r"capacity|height|length|width|depth|weight|volume|power|storage|size|diameter"
NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "pair": 1,
}  # fmt: skip
ROLE_PATTERNS = {
    "cart_view": (r"(view|show|get|read|see).*cart|cart.*(view|show|content|get)",),
    "cart": (r"cart|basket",),
    "buy": (r"check.?out|purchase|place.*order|\bbuy\b|\border\b",),
    "refuse": (r"decline|refuse|no.?purchase|without.*(buy|purchas)|give.?up|\bend\b",),
    "ask": (r"\bask\b|message|question|shopper|customer|contact",),
    "compare": (r"compare|side.by.side",),
    "search": (r"search|find|browse|list.*(product|item)|query",),
    "open": (r"detail|open|view|get.*(product|item|listing)|product.*(page|info)|inspect|show",),
}
POINTER = re.compile(
    r"\b(?:the same|same|matching|unlike|different from|similar to|just like)\b[^.;]*?"
    r"\b(?:as|to|from|like|the)\b[^.;]*",
    re.I,
)
APPROVAL = r"\b(approv\w*|confirm\w* with me|check with me|ask me (first|before)|my (ok|okay))\b"
CONTEXT = r"\b(for my|for our|for his|for her|for the|to use|to wear|because|since|as a gift)\b.*"
LEAD_IN = (
    r"^.*?\b(?:buy|get|find|purchase|order|need|want|looking for|would like|shop for|after)\b"
    r"(?: to (?:buy|get|find|purchase|order))?(?: me| us)?\s*"
)


def tool_name(tool: dict[str, Any]) -> str:
    return tool["function"]["name"]


def guess_roles(tools: list[dict[str, Any]]) -> dict[str, str]:
    """Work out which tool does which job from names first, then descriptions."""
    roles: dict[str, str] = {}
    taken: set[str] = set()
    for field in ("name", "description"):
        for role, patterns in ROLE_PATTERNS.items():
            if role in roles:
                continue
            for tool in tools:
                name = tool_name(tool)
                if name in taken:
                    continue
                text = str(tool["function"].get(field) or "").replace("_", " ")
                if any(re.search(p, text, re.I) for p in patterns):
                    roles[role] = name
                    taken.add(name)
                    break
    return roles


def category_labels(tools: list[dict[str, Any]]) -> list[str]:
    """Labels the store lists in its tool schemas."""
    found: list[str] = []
    for tool in tools:
        props = (tool["function"].get("parameters") or {}).get("properties") or {}
        for name, spec in props.items():
            if not isinstance(spec, dict) or "string" not in kinds(spec):
                continue
            if re.search(r"categor|shelf|department|section", name, re.I):
                found += [o for o in spec.get("enum") or [] if isinstance(o, str)]
    return found


TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}  # fmt: skip
UNITS_AND_TEENS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}  # fmt: skip
_SPELLED = "|".join([*TENS, *UNITS_AND_TEENS, "hundred", "a"])


def _spelled_money_to_digits(text: str) -> str:
    """Rewrite amounts such as 'twenty five dollars' as '25 dollars'."""

    def convert(match: re.Match) -> str:
        total, current = 0, 0
        for word in re.split(r"[\s-]+", match.group(1).lower()):
            if word == "hundred":
                current = max(current, 1) * 100
            elif word in TENS:
                current += TENS[word]
            elif word in UNITS_AND_TEENS:
                current += UNITS_AND_TEENS[word]
        total += current
        return f"{total} {match.group(2)}"

    pattern = rf"\b((?:(?:{_SPELLED})[\s-]+)+)(dollars?|bucks?|usd)\b"
    return re.sub(pattern, convert, text, flags=re.I)


PER_ITEM = r"(?:each|apiece|per item|per unit|a piece)"


def read_money(text: str) -> tuple[float | None, str, bool]:
    """Find the budget. Returns it, the text without that phrase, and whether it is per item."""
    text = _spelled_money_to_digits(text)
    limit = (
        r"(?:,?\s*(?:for |of )?(?:nothing over|not over|under|below|less than|at most"
        r"|no more than|up to|within|maximum of"
        r"|max|budget of|budget is|spend(?:ing)?(?: at most)?|cost(?:ing)? (?:at most|under))\s*)"
    )
    patterns = (
        rf"{limit}?\$\s*(\d+(?:\.\d+)?)",
        rf"{limit}?(\d+(?:\.\d+)?)\s*(?:dollars?|usd|bucks?)\b",
        r"budget(?: of| is)?\s*(\d+(?:\.\d+)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            # Only words next to the amount make it a limit per item ("$5 each").
            each = bool(
                re.match(rf"\s*{PER_ITEM}\b", text[match.end() :], re.I)
                or re.search(
                    rf"\b{PER_ITEM}\s*(?:costing|at|for)?\s*$", text[: match.start()], re.I
                )
            )
            return float(match.group(1)), text[: match.start()] + " " + text[match.end() :], each
    return None, text, False


def _pick(text: str) -> tuple[str, str]:
    if re.search(r"cheapest|lowest price|least expensive|best price|lowest cost", text, re.I):
        return "cheapest", ""
    match = re.search(r"\b(most|highest|largest|biggest)\s+([a-z]+(?: [a-z]+)?)", text, re.I)
    if match and match.group(2).split()[0].lower() != "expensive":
        return "most", match.group(2)
    match = re.search(r"\b(least|lowest|smallest|lightest)\s+([a-z]+)", text, re.I)
    if match:
        return "least", "weight" if match.group(1).lower() == "lightest" else match.group(2)
    return "any", ""


def _split_items(text: str) -> list[str]:
    """Split on 'and' or commas only where the next part starts a new counted product."""
    count = "|".join(NUMBER_WORDS) + r"|\d+"
    return [
        p for p in re.split(rf"(?:,|\band\b|\bplus\b)\s+(?=(?:{count})\b)", text, flags=re.I) if p
    ]


def _parse_clause(clause: str, labels: list[str]) -> Item | None:
    musts: list[str] = []
    text = re.sub(CONTEXT, " ", clause, flags=re.I)

    def take(pattern: str, build) -> None:
        nonlocal text
        while match := re.search(pattern, text, re.I):
            need = build(match)
            if need and need not in musts:
                musts.append(need)
            text = text[: match.start()] + " " + text[match.end() :]

    limit = r"at least|at most|under|over|below|above|up to|more than|less than|minimum|maximum"
    quantity = r"\d+(?:\.\d+)?\s*-?\s*[a-zA-Z]*"
    take(
        rf"\b(?:with |that holds? |holding |of )?({limit}) ({quantity})\s+({MEASURED})\b",
        lambda m: f"{m.group(3).lower()}: {m.group(1).lower()} {m.group(2)}",
    )
    take(
        rf"\b({limit}) ({quantity})\s+(tall|high|long|wide|deep)\b",
        lambda m: f"{SIZES[m.group(3).lower()]}: {m.group(1).lower()} {m.group(2)}",
    )
    take(
        rf"\b(?:with |of )?(?:a |an )?({MEASURED})\s+(?:of\s+)?(?:({limit}) )?({quantity})",
        lambda m: f"{m.group(1).lower()}: {(m.group(2) + ' ') if m.group(2) else ''}{m.group(3)}",
    )
    take(r"\bsize\s+(xs|s|m|l|xl|xxl)\b", lambda m: f"size: {m.group(1).upper()}")
    material = "|".join(MATERIALS)
    colour = "|".join(COLOURS)
    take(
        rf"\b(?:not|no|without|non)[ -]({material})\b",
        lambda m: f"material: not {m.group(1).lower()}",
    )
    take(rf"\b(?:not|no|without)[ -]({colour})\b", lambda m: f"colour: not {m.group(1).lower()}")
    take(r"\b(?:without|no)\s+([a-z]+)\b", lambda m: f"not {m.group(1).lower()}")
    take(rf"\b({colour})\b", lambda m: f"colour: {m.group(1).lower()}")
    take(rf"\b({material})\b", lambda m: f"material: {m.group(1).lower()}")
    text = re.sub(
        r"\b(in|with|made of|made from|that is|that are|which is|please)\b", " ", text, flags=re.I
    )

    quantity_found = 1
    match = re.match(r"\s*(\d+|[a-z]+)\b(?: of)?", text, re.I)
    if match and (match.group(1).isdigit() or match.group(1).lower() in NUMBER_WORDS):
        word = match.group(1).lower()
        quantity_found = int(word) if word.isdigit() else NUMBER_WORDS[word]
        text = text[match.end() :]
    name = " ".join(re.sub(r"[^\w\s-]", " ", text).split()).strip().lower()
    name = re.sub(r"^(?:(?:the|some|new|nice|good|another)\s+)+", "", name)
    if not name:
        return None
    shelf = _best_label(name, labels)
    # With the kind of product settled by its label, any other word in the name says something
    # about the product. It must count as a requirement, or the team would ignore it and could
    # buy the wrong thing.
    extra = [w for w in name.split() if words(w) and set(words(w)) - set(words(shelf))]
    if shelf and extra and not set(words(name)) <= set(words(shelf)):
        musts.append(" ".join(extra))
    return Item(name, shelf, max(1, quantity_found), musts)


def _best_label(name: str, labels: list[str]) -> str:
    wanted = set(words(name))
    best, score = "", 0
    for label in labels:
        overlap = len(wanted & set(words(label)))
        if overlap > score:
            best, score = label, overlap
    return best


def parse_request(request: str, tools: list[dict[str, Any]]) -> Brief:
    """Read a request with rules alone."""
    roles = guess_roles(tools)
    brief = Brief(request=request, roles=roles)
    text = " ".join(request.split())

    match = re.search(
        r"\b(?:at most|up to|only|ask me)?\s*(\d+) (?:questions?|messages?)\b", text, re.I
    )
    if match:
        brief.messages = int(match.group(1))
        text = text[: match.start()] + text[match.end() :]
    elif re.search(r"\b(don't|do not|no need to) (ask|message|contact)\b", text, re.I):
        brief.messages = 0
    elif roles.get("ask"):
        brief.messages = 3
    match = re.search(r"\b(?:within|in|at most) (\d+) (?:steps|moves|tool calls)\b", text, re.I)
    if match:
        brief.steps = int(match.group(1))
        text = text[: match.start()] + text[match.end() :]

    if re.search(APPROVAL, text, re.I):
        step = {"step": "get the shopper's approval", "when": "order", "messages": 1}
        brief.process.append(step)
    brief.pick, brief.pick_measure = _pick(text)
    brief.budget, text, each = read_money(text)
    if each:
        brief.budget_scope = "each"

    sentences = [s for s in re.split(r"(?<=[.!?;])\s+", text) if s.strip()]
    wanted = next(
        (s for s in sentences if re.search(LEAD_IN, s, re.I)), sentences[0] if sentences else ""
    )
    body = re.sub(LEAD_IN, "", wanted, flags=re.I).rstrip(".!?; ")
    body = re.sub(
        r"\b(the )?(cheapest|lowest price|least expensive|best price)( one)?\b",
        " ",
        body,
        flags=re.I,
    )
    body = re.sub(r"\b(each|apiece|per item)\b", " ", body, flags=re.I)
    if brief.pick in ("most", "least"):
        body = re.sub(
            r"\b(?:with |having |that has )?(?:the )?"
            r"(?:most|highest|largest|biggest|least|lowest|smallest|lightest)"
            rf"(?: {re.escape(brief.pick_measure)})?\b",
            " ",
            body,
            flags=re.I,
        )
    labels = category_labels(tools)
    pointer = POINTER.search(body)
    if pointer:
        brief.lookups.append(" ".join(pointer.group().split()))
        body = body[: pointer.start()] + body[pointer.end() :]
    for clause in _split_items(body):
        item = _parse_clause(clause, labels)
        if item:
            brief.items.append(item)
    if not brief.items:
        brief.items = [Item("the requested product")]
    return brief


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and re.fullmatch(r"\s*\d+(\.\d+)?\s*", value):
        return float(value)
    return None


def _count(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number == int(number) else None


def brief_from_model(
    data: dict[str, Any], request: str, tools: list[dict[str, Any]]
) -> Brief | None:
    """Convert a model's brief, rejecting one that lacks items or a way to buy."""
    names = {tool_name(t) for t in tools}
    raw_roles = data.get("roles") if isinstance(data.get("roles"), dict) else {}
    roles = {k: v for k, v in raw_roles.items() if k in ROLES and isinstance(v, str) and v in names}
    items = []
    for entry in listed(data, "items"):
        if isinstance(entry, dict):
            quantity = _count(entry.get("quantity"))
            items.append(
                Item(
                    str(entry.get("name") or "the product"),
                    str(entry.get("shelf") or ""),
                    quantity if quantity and quantity > 0 else 1,
                    [str(m) for m in listed(entry, "musts") if m],
                )
            )
    if not items or not roles.get("buy"):
        return None
    limit = _count(data.get("messages"))
    process = []
    for entry in listed(data, "process"):
        if isinstance(entry, dict) and str(entry.get("step") or "").strip():
            when = str(entry.get("when") or "").lower()
            process.append(
                {
                    "step": str(entry["step"]).strip(),
                    "when": when if when in ("choose", "order", "end") else "order",
                    "messages": max(0, _count(entry.get("messages")) or 0),
                }
            )
    pick = data.get("pick") if data.get("pick") in PICK_RULES else "any"
    budget = _number(data.get("budget"))
    fits = [
        {"item": _count(p.get("item")), "with": _count(p.get("with"))}
        for p in listed(data, "fits")
        if isinstance(p, dict)
        and all(1 <= (_count(p.get(k)) or 0) <= len(items) for k in ("item", "with"))
    ]
    return Brief(
        request=request,
        items=items,
        budget=budget if budget and budget > 0 else None,
        budget_scope="each" if data.get("budget_scope") == "each" else "total",
        pick=pick,
        pick_measure=str(data.get("pick_measure") or ""),
        in_stock_only=data.get("in_stock_only") is not False,
        messages=limit if limit is not None and limit >= 0 else (3 if roles.get("ask") else 0),
        steps=_count(data.get("steps")),
        process=process,
        quiet_decline=data.get("quiet_decline") is not False,
        wishes=[str(w) for w in listed(data, "wishes")],
        fits=fits,
        lookups=[str(x) for x in listed(data, "lookups")],
        roles=roles,
        buy_args=data["buy_args"] if isinstance(data.get("buy_args"), dict) else {},
        refuse_args=data["refuse_args"] if isinstance(data.get("refuse_args"), dict) else {},
    )


class RequestAgent(Agent):
    """Handles `read`: {request, tools} becomes {brief, source}."""

    name = "request"

    def __init__(self, model: Model | None = None):
        self.model = model

    def on_read(self, payload: dict[str, Any], context) -> dict[str, Any]:
        request, tools = payload["request"], payload["tools"]
        brief = self._with_model(request, tools, context) if self.model else None
        source = "model"
        if brief is None:
            brief, source = parse_request(request, tools), "rules"
        context.note(f"read {len(brief.items)} item(s) using {source}", source=source)
        return {"brief": brief.to_dict(), "source": source}

    def _with_model(self, request: str, tools: list[dict[str, Any]], context) -> Brief | None:
        listing = [
            f"- {tool_name(t)}: {t['function'].get('description', '')} "
            f"| parameters: {json.dumps(t['function'].get('parameters') or {})}"
            for t in tools
        ]
        prompt = f"{BRIEF}\nTOOLS:\n" + "\n".join(listing) + f"\n\nREQUEST:\n{request}"
        try:
            data = ask_json(self.model, prompt, 4000)
        except ModelError as error:
            context.note(f"the model could not read the request: {error}")
            return None
        brief = brief_from_model(data or {}, request, tools)
        if brief is None:
            context.note("the model's brief was unusable")
            return None
        self._unmix(brief, context)
        return brief

    def _unmix(self, brief: Brief, context) -> None:
        """Remove words that describe another listing from a product's own requirements."""
        for item in brief.items:
            pointers = [need for need in brief.lookups if need in item.musts]
            rest = [need for need in item.musts if need not in pointers]
            if not pointers or not rest:
                continue
            prompt = (
                f"{UNMIX}\n\nREQUEST:\n{brief.request}\n\nPRODUCT: {item.name}\n"
                "REQUIREMENTS POINTING AT ANOTHER LISTING:\n" + "\n".join(pointers) + "\n"
                "OTHER REQUIREMENTS:\n" + "\n".join(rest)
            )
            try:
                got = ask_json(self.model, prompt, 1500) or {}
            except ModelError:
                continue
            moved = [
                m for m in rest if m in [str(x).strip() for x in listed(got, "describe_other")]
            ]
            if moved:
                joined = f"{pointers[0]} (that listing: {'; '.join(moved)})"
                item.musts = [
                    joined if m == pointers[0] else m for m in item.musts if m not in moved
                ]
                brief.lookups = [joined if x == pointers[0] else x for x in brief.lookups]
                context.note(f"moved {len(moved)} words describing another listing", moved=moved)
