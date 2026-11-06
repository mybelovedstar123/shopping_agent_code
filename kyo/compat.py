"""Read part-to-device compatibility from listing text. Ported from `_fit_marks` in agent.py."""

from __future__ import annotations

import re
from typing import Any

from .domain import Listing

FIT_CODE = r"[A-Z][A-Za-z]{0,5} ?\d{3,6}"
PART_CODE = r"[A-Z]{1,4}\d{3,6}"


def codes_in(text: Any) -> list[str]:
    found = re.findall(r"\b" + FIT_CODE + r"\b", str(text or ""))
    return [re.sub(r"\s+", " ", c).strip().lower() for c in found]


def marks(text: Any) -> dict[str, list[str]]:
    """Codes a listing goes by, and codes it says it works with."""
    text = str(text or "")
    own: list[str] = []
    for hit in re.finditer(
        r"\bmodel\s*(?:no\.?|number|#)\s*[:.]?\s*(" + FIT_CODE + r")\b", text, re.I
    ):
        own += codes_in(hit.group(1))
    for hit in re.finditer(r"\b(?:also sold as|also known as|sold also as)\s+([^.;]+)", text, re.I):
        own += codes_in(hit.group(1))
    works: list[str] = []
    for hit in re.finditer(
        r"\b(?<!not )(?<!never )(?:works with|compatible with|fits)\s*:?\s+([^.;]+)", text, re.I
    ):
        works += codes_in(hit.group(1))
    return {"codes": own, "works": works}


def listing_text(listing: Listing) -> str:
    parts = [listing.title, listing.description]
    parts += [f"{key} {value}" for key, value in listing.attributes.items()]
    return ". ".join(parts)


def fit_problem(part: Listing, device: Listing) -> str | None:
    """Why a part does not work with a device. None when it does, or when the text cannot tell."""
    works = set(marks(listing_text(part))["works"])
    codes = set(marks(listing_text(device))["codes"])
    if works and codes and not works & codes:
        return f"{part.id} works with {', '.join(sorted(works))}, not with {device.id}"
    return None
