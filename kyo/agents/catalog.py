"""The catalog agent finds listings, opens them, and remembers everything it has seen.

No store's result format is assumed. Which keys hold the id, title, category, price and stock
is learned from what the store returns. The result layout is learned by name, and a model is
asked only when that fails.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..domain import Listing
from ..fit import same_kind, words
from ..models.base import Model, ModelError, ask_json
from ..prompts import SHAPE
from ..runtime.agent import Agent
from ..schema import param_named, text_params

MAX_PAGES_PER_SEARCH = 3
SHAPE_KEYS = ("rows", "count", "next")
NOT_PROPERTY = re.compile(
    r"\b(?:options?|variants?|models?|sku|ids?|codes?|asin|upc|ean|barcodes?|warrant\w*|video|url"
    r"|links?|images?|photos?|condition)\b"
)
CATCH_ALL = re.compile(
    r"\b(?:features?|functions?|highlights?|specs?|specifications?|descriptions?|includ\w*"
    r"|accessor\w*|extras?|misc\w*)\b"
)


def _named(row: dict[str, Any], pattern: str) -> str | None:
    return next((k for k in row if re.fullmatch(pattern, str(k), re.I)), None)


def _rows_key(observation: dict[str, Any]) -> str | None:
    lists = [
        k
        for k, v in observation.items()
        if isinstance(v, list) and v and all(isinstance(r, dict) for r in v)
    ]
    preferred = [k for k in lists if re.search(r"result|row|item|product|listing|hit", k, re.I)]
    return (preferred or lists or [None])[0]


def learn_id_key(rows: list[dict[str, Any]]) -> str | None:
    """Pick the key whose values look like ids and are unique, preferring keys named id."""
    best, best_score = None, None
    for key in rows[0]:
        values = [row.get(key) for row in rows]
        named = "id" in str(key).lower()
        looks = all(
            isinstance(v, str | int)
            and not isinstance(v, bool)
            and 1 <= len(str(v).strip()) <= 64
            and " " not in str(v).strip()
            and (named or re.search(r"\d", str(v)))
            for v in values
        )
        if not looks or (len(rows) > 1 and len(set(map(str, values))) < len(values)):
            continue
        score = (named, sum(len(str(v)) for v in values))
        if best_score is None or score > best_score:
            best, best_score = key, score
    return best


class CatalogAgent(Agent):
    """Handles `find`, `open`, `records`, `keys`, `refresh` and `mark_sold`."""

    name = "catalog"

    def __init__(self, model: Model | None = None):
        self.model = model
        self.records: dict[str, Listing] = {}
        self.order: list[str] = []
        self.sold: set[str] = set()
        self.id_key: str | None = None
        self.searches_done: set[str] = set()
        self.shapes: dict[str, dict[str, Any]] = {}

    def _call(self, context, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        return context.request("gateway", "call", {"name": tool, "args": args})

    def on_find(self, payload: dict[str, Any], context) -> dict[str, Any]:
        """Search for an item. Payload: tool, item, terms, optional max_price, keep (steps)."""
        tool, schema = payload["tool"], payload.get("schema") or {}
        item = payload["item"]
        query = param_named(schema, r"^(q|query|search|text|keywords?|terms?|phrase)$")
        query = query or next(iter(text_params(schema)), None)
        category = param_named(schema, r"categor|shelf|department|section")
        pages = param_named(schema, r"^(page|offset|cursor|token|next)")
        price_cap = param_named(schema, r"max.*price|price.*max|max.*cost")

        args: dict[str, Any] = {}
        if query:
            args[query] = payload.get("terms") or item["name"]
        options = (schema.get("properties") or {}).get(category or "", {}).get("enum") or []
        if category and item.get("shelf") and (not options or item["shelf"] in options):
            args[category] = item["shelf"]
            if query and not payload.get("terms"):
                args.pop(query)
        if price_cap and payload.get("max_price") is not None:
            args[price_cap] = payload["max_price"]

        mark = tool + json.dumps(args, sort_keys=True)
        found: list[str] = []
        total = None
        for _ in range(int(payload.get("max_pages", MAX_PAGES_PER_SEARCH))):
            reply = self._call(context, tool, args)
            if not reply.get("ok"):
                break
            shape = self._shape(tool, reply["observation"], schema)
            found += self._absorb_rows(reply["observation"], shape)
            total = self._value(reply["observation"], shape.get("count"), total)
            following = self._value(reply["observation"], shape.get("next"), None)
            if following in (None, "", False) or not pages or reply.get("finished"):
                break
            if int(reply.get("left", 0)) <= int(payload.get("keep", 0)):
                break
            args = {**args, pages: following}
        self.searches_done.add(mark)
        ids = list(dict.fromkeys(found))
        context.note(f"found {len(ids)} listing(s) for {item['name']}", total=total)
        return {"ids": ids, "total": total}

    @staticmethod
    def _value(observation: Any, key: str | None, default: Any) -> Any:
        return observation.get(key, default) if key and isinstance(observation, dict) else default

    def _shape(self, tool: str, observation: Any, schema: dict[str, Any]) -> dict[str, Any]:
        if tool in self.shapes or not isinstance(observation, dict):
            return self.shapes.get(tool, {})
        shape = {
            "rows": _rows_key(observation),
            "count": _named(observation, r"total|count|total_?count|num_?results|matches"),
            "next": _named(observation, r"next(_?page)?|next_?(token|offset|cursor)|cursor"),
        }
        if self.model and not shape["rows"]:
            shape = self._shape_from_model(tool, observation, schema) or shape
        if shape["rows"]:
            self.shapes[tool] = shape
        return shape

    def _shape_from_model(self, tool, observation, schema) -> dict[str, Any] | None:
        sample = json.dumps(observation)[:4000]
        prompt = f"{SHAPE}\n\nTOOL: {tool} | parameters: {json.dumps(schema)}\n\nRESULT:\n{sample}"
        try:
            got = ask_json(self.model, prompt, 2500) or {}
        except ModelError:
            return None
        shape = {k: got.get(k) if isinstance(got.get(k), str) else None for k in SHAPE_KEYS}
        return shape if shape["rows"] else None

    def _absorb_rows(self, observation: Any, shape: dict[str, Any]) -> list[str]:
        if not isinstance(observation, dict):
            return []
        rows = observation.get(shape.get("rows") or "")
        if not isinstance(rows, list):
            return []
        rows = [r for r in rows if isinstance(r, dict)]
        if rows and self.id_key is None:
            self.id_key = learn_id_key(rows)
        return [i for i in (self._absorb(row) for row in rows) if i]

    def _absorb(self, row: dict[str, Any], opened: bool = False) -> str | None:
        """Merge one record into memory and return its id."""
        key = self.id_key or learn_id_key([row])
        if key is None or row.get(key) in (None, ""):
            return None
        ident = str(row[key]).strip()
        record = self.records.get(ident)
        if record is None:
            record = Listing(ident)
            self.records[ident] = record
            self.order.append(ident)
        attributes, loose = {}, {}
        for name, value in row.items():
            if name == key:
                continue
            if re.fullmatch(r"(title|name|product_?name|label)", name, re.I):
                record.title = str(value)
            elif re.fullmatch(r"(category|shelf|department|section|type)", name, re.I):
                record.category = str(value)
            elif re.fullmatch(r"(price|cost|amount|unit_?price)", name, re.I):
                record.price = self._price(value)
            elif re.fullmatch(r"(in_?stock|available|availability|buyable)", name, re.I):
                record.in_stock = value not in (False, 0, "no", "out of stock")
            elif re.fullmatch(r"(stock|inventory|quantity_available)", name, re.I):
                record.in_stock = bool(value)
            elif re.fullmatch(r"(description|details|summary|about)", name, re.I):
                record.description = str(value)
            elif isinstance(value, dict):
                attributes.update(value)
            elif not re.fullmatch(r"(error|note|message|url|image)", name, re.I):
                loose[name] = value
        if attributes:
            record.attributes = {**record.attributes, **attributes}
            record.opened = True
        if opened:
            record.attributes = {**loose, **record.attributes}
            record.opened = True
        if ident in self.sold or record.in_stock is False:
            record.in_stock = False
        return ident

    @staticmethod
    def _price(value: Any) -> float | None:
        if isinstance(value, dict):
            value = next((v for v in value.values() if isinstance(v, int | float)), None)
        if isinstance(value, str):
            match = re.search(r"\d+(?:\.\d+)?", value.replace(",", ""))
            value = float(match.group()) if match else None
        return float(value) if isinstance(value, int | float) else None

    def on_open(self, payload: dict[str, Any], context) -> dict[str, Any]:
        """Read listing pages. Payload: tool, schema, ids, keep (steps to leave unspent)."""
        tool, schema = payload["tool"], payload.get("schema") or {}
        target = next(iter(schema.get("required") or text_params(schema)), None)
        opened = []
        for ident in payload["ids"]:
            record = self.records.get(ident)
            if record is not None and record.opened:
                continue
            if not target:
                break
            reply = self._call(context, tool, {target: ident})
            if not reply.get("ok") or reply.get("finished"):
                break
            if isinstance(reply["observation"], dict) and not reply["observation"].get("error"):
                self._absorb(reply["observation"], opened=True)
                opened.append(ident)
            if int(reply.get("left", 0)) <= int(payload.get("keep", 0)):
                break
        return {"opened": opened}

    def on_refresh(self, payload: dict[str, Any], context) -> dict[str, Any]:
        """Re-read listings for changes in price or stock, reporting what moved."""
        tool, schema = payload["tool"], payload.get("schema") or {}
        target = next(iter(schema.get("required") or text_params(schema)), None)
        changed = []
        for ident in payload["ids"]:
            before = self.records.get(ident)
            old = (before.price, before.in_stock) if before else None
            reply = self._call(context, tool, {target: ident}) if target else {}
            if not reply.get("ok"):
                break
            self._absorb(reply["observation"], opened=True)
            record = self.records[ident]
            if old != (record.price, record.in_stock):
                changed.append(ident)
        return {"changed": changed}

    def on_mark_sold(self, payload: dict[str, Any], context) -> dict[str, Any]:
        for ident in payload["ids"]:
            self.sold.add(ident)
            if ident in self.records:
                self.records[ident].in_stock = False
        return {}

    def on_records(self, payload: dict[str, Any], context) -> dict[str, Any]:
        """Records by ids, or those of one requested kind of product."""
        if payload.get("ids") is not None:
            ids = [i for i in payload["ids"] if i in self.records]
        else:
            ids = list(self.order)
        item = payload.get("item")
        if item:
            ids = [
                i
                for i in ids
                if same_kind(item["name"], item.get("shelf", ""), self.records[i].category)
            ]
        return {"records": [self.records[i].to_dict() for i in ids]}

    def on_keys(self, payload: dict[str, Any], context) -> dict[str, Any]:
        """Attribute keys carried by opened listings of an item, most widely carried first."""
        records = self.on_records({"item": payload["item"]}, context)["records"]
        opened = [r for r in records if r["opened"]]
        table: dict[str, dict[str, Any]] = {}
        for record in opened:
            for key, value in record["attributes"].items():
                name = " ".join(words(key))
                if not name or NOT_PROPERTY.search(name):
                    continue
                slot = table.setdefault(name, {"key": name, "carried": 0, "values": []})
                slot["carried"] += 1
                text = str(value).lower()
                if text not in slot["values"]:
                    slot["values"].append(text)
        ranked = sorted(
            table.values(),
            key=lambda s: (bool(CATCH_ALL.search(s["key"])), -s["carried"], s["key"]),
        )
        return {"keys": ranked, "read": len(opened)}
