"""The rules for whether a listing meets a requirement, as code.

These are the rules stated in the GUIDE prompt. An attribute whose key names the required
property decides. A key for a different property decides nothing. When no attribute names
the property, the wanted value stated elsewhere in the listing counts, unless the
requirement is a measurement: a bare figure elsewhere does not say what it measures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .domain import Listing

# Each unit as its size in a base unit of its dimension.
UNITS: dict[str, tuple[str, float]] = {}
for _dimension, _table in {
    "length": {
        "mm": 1,
        "millimetre": 1,
        "millimeter": 1,
        "cm": 10,
        "centimetre": 10,
        "centimeter": 10,
        "m": 1000,
        "metre": 1000,
        "meter": 1000,
        "in": 25.4,
        "inch": 25.4,
        "inches": 25.4,
        "ft": 304.8,
        "foot": 304.8,
        "feet": 304.8,
    },
    "mass": {
        "mg": 0.001,
        "g": 1,
        "gram": 1,
        "kg": 1000,
        "kilogram": 1000,
        "oz": 28.3495,
        "ounce": 28.3495,
        "lb": 453.592,
        "lbs": 453.592,
        "pound": 453.592,
    },
    "volume": {"ml": 1, "millilitre": 1, "milliliter": 1, "l": 1000, "litre": 1000, "liter": 1000},
    "power": {"w": 1, "watt": 1, "kw": 1000, "kilowatt": 1000},
    "storage": {"mb": 1, "gb": 1000, "tb": 1000000},
}.items():
    for _name, _size in _table.items():
        UNITS[_name] = (_dimension, float(_size))

SYNONYMS = {"color": "colour", "gray": "grey", "aluminum": "aluminium", "kind": "type"}
_FILLER = {"a", "an", "the", "of", "in", "with", "and"}
_NUMBER = r"(\d+(?:\.\d+)?)"
_UNIT = r"\s*-?\s*([a-zA-Z]+)?"


def words(text: Any) -> list[str]:
    """Normalise text to comparable words: split camelCase, lower-case, singular, synonyms."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(text))
    out = []
    for word in re.findall(r"[a-z0-9.]+", text.lower()):
        word = word.strip(".")
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        if word:
            out.append(SYNONYMS.get(word, word))
    return out


def key_names_property(key: str, prop: str) -> bool:
    """A key names a property when one holds all the other's meaningful words."""
    a, b = set(words(key)) - _FILLER, set(words(prop)) - _FILLER
    if not a or not b:
        return False
    return b <= a or (len(a) >= 2 and a <= b) or a == b


def _quantity(text: str) -> tuple[float, str | None] | None:
    """Read '12 inch' as (number, unit name)."""
    match = re.search(_NUMBER + _UNIT, text)
    if not match:
        return None
    unit = (match.group(2) or "").lower() or None
    return float(match.group(1)), unit


def _span(text: str) -> tuple[float, float, str | None] | None:
    """Read a value or a range such as '10-20 cm' as (low, high, unit)."""
    match = re.search(_NUMBER + r"\s*(?:-|–|to)\s*" + _NUMBER + _UNIT, text)
    if match:
        unit = (match.group(3) or "").lower() or None
        return float(match.group(1)), float(match.group(2)), unit
    single = _quantity(text)
    return (single[0], single[0], single[1]) if single else None


def _convert(number: float, unit: str | None, target: str | None) -> float | None:
    """Convert to the target unit. Returns None when the units cannot be compared."""
    if unit == target or target is None or unit is None:
        return number
    if unit not in UNITS:
        unit = unit[:-1] if unit.endswith("s") and unit[:-1] in UNITS else unit
    if target not in UNITS:
        target = target[:-1] if target.endswith("s") and target[:-1] in UNITS else target
    if unit == target:
        return number
    if unit in UNITS and target in UNITS and UNITS[unit][0] == UNITS[target][0]:
        return number * UNITS[unit][1] / UNITS[target][1]
    return None


LOWER = r"at least|minimum|min|over|above|more than|from|no less than"
UPPER = r"at most|maximum|max|under|below|less than|up to|no more than|within"
NEGATION = r"(?:not|no|never|without|other than|except|anything but|must not be)"


def _limit_met(constraint: str, value: str) -> bool | None:
    """Check a numeric constraint against a value. None when it is not a numeric constraint."""
    text = constraint.lower()
    between = re.search(r"between\s+" + _NUMBER + _UNIT + r"\s+and\s+" + _NUMBER + _UNIT, text)
    lower = re.search(rf"(?:{LOWER})\s+" + _NUMBER + _UNIT, text)
    upper = re.search(rf"(?:{UPPER})\s+" + _NUMBER + _UNIT, text)
    if not (between or lower or upper):
        return None
    span = _span(value)
    if span is None:
        return False
    low, high, unit = span

    def inside(limit: float, limit_unit: str | None, above: bool) -> bool:
        limit_unit = limit_unit.lower() if limit_unit else None
        edge = _convert(low if above else high, unit, limit_unit)
        if edge is None:
            return False
        return edge >= limit if above else edge <= limit

    if between:
        unit_b = between.group(4)
        return inside(float(between.group(1)), between.group(2) or unit_b, True) and inside(
            float(between.group(3)), unit_b, False
        )
    ok = True
    if lower:
        ok = ok and inside(float(lower.group(1)), lower.group(2), True)
    if upper:
        ok = ok and inside(float(upper.group(1)), upper.group(2), False)
    return ok


def _is_measure(constraint: str) -> bool:
    """A requirement whose value is a number, such as '30 cm' or 'at least 2'."""
    return bool(re.search(r"\d", constraint))


def _words_present(wanted: str, value: str) -> bool:
    """The wanted value's words all appear, in any order, in one element of the value."""
    want = set(words(wanted)) - _FILLER
    if not want:
        return False
    return any(want <= set(words(part)) for part in _elements(value))


def _elements(value: Any) -> list[str]:
    if isinstance(value, list | tuple):
        return [str(v) for v in value]
    text = str(value)
    return [text, *re.split(r"[,/;]|\s+and\s+", text)]


def _value_met(constraint: str, value: Any) -> bool:
    """Whether an attribute value satisfies a constraint."""
    text = constraint.strip()
    negated = re.match(rf"^{NEGATION}\s+(.*)$", text, re.I)
    if negated:
        return not _value_met(negated.group(1), value)
    joined = ", ".join(str(v) for v in value) if isinstance(value, list | tuple) else str(value)
    limit = _limit_met(text, joined)
    if limit is not None:
        return limit
    choices = re.split(r"\s+or\s+|\s*,\s*", re.sub(r"^(?:either|one of)\s+", "", text, flags=re.I))
    if len(choices) > 1:
        return any(_value_met(choice, value) for choice in choices if choice)
    if _is_measure(text):
        wanted = _quantity(text)
        found = _span(joined)
        if wanted and found:
            low = _convert(found[0], found[2], wanted[1])
            high = _convert(found[1], found[2], wanted[1])
            if low is None or high is None:
                return False
            return low == high and abs(low - wanted[0]) < 1e-6
    return _words_present(text, value)


@dataclass(frozen=True)
class Verdict:
    """met is None when the listing's page must be read before deciding."""

    met: bool | None
    reason: str


def check_need(need: str, listing: Listing) -> Verdict:
    """Decide whether a listing meets one requirement written as 'property: constraint'."""
    prop, separator, constraint = need.partition(":")
    if not separator:
        prop, constraint = "", need
    prop, constraint = prop.strip(), constraint.strip()

    deciding = [k for k in listing.attributes if key_names_property(k, prop)] if prop else []
    if deciding:
        for key in deciding:
            if _value_met(constraint, listing.attributes[key]):
                return Verdict(True, f"{key} = {listing.attributes[key]}")
        key = deciding[0]
        return Verdict(False, f"{key} is {listing.attributes[key]}, needs {constraint}")

    negated = re.match(rf"^{NEGATION}\s+(.*)$", constraint, re.I)
    if negated:
        if _words_present(negated.group(1), f"{listing.title}, {listing.description}"):
            return Verdict(False, f"the listing mentions {negated.group(1)}")
        if listing.opened:
            return Verdict(True, f"no sign of {negated.group(1)}")
        return Verdict(None, "its page has not been read")
    if _words_present(constraint, listing.category):
        return Verdict(True, f"its category is {listing.category}")
    if not _is_measure(constraint):
        places = [listing.title, listing.description]
        places += [str(v) for v in listing.attributes.values()]
        for place in places:
            if _value_met(constraint, place):
                return Verdict(True, f"stated in the listing: {str(place)[:60]}")
    if not listing.opened:
        return Verdict(None, "its page has not been read")
    return Verdict(False, f"nothing states {prop or 'the requirement'}: {constraint}")


def check_needs(needs: list[str], listing: Listing) -> dict[str, Verdict]:
    return {need: check_need(need, listing) for need in needs}


def same_kind(item_name: str, shelf: str, category: str) -> bool:
    """Whether a category label is the requested kind of product."""
    if not category:
        return True
    label = set(words(category)) - _FILLER
    if shelf:
        return label == set(words(shelf)) - _FILLER
    wanted = set(words(item_name)) - _FILLER
    return bool(label and wanted and (label & wanted))
