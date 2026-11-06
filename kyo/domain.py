"""The shared vocabulary of the team: what is wanted, and what the store offers.

Everything converts to and from plain dicts, because agents exchange only JSON values.
A requirement is a string in the form "property: constraint".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

PICK_RULES = ("any", "cheapest", "most", "least")
ROLES = ("search", "open", "compare", "ask", "cart", "cart_view", "buy", "refuse")


def _from_dict(cls, data: dict[str, Any]):
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Item:
    """One product the shopper wants."""

    name: str
    shelf: str = ""
    quantity: int = 1
    musts: list[str] = field(default_factory=list)
    told: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    free: list[str] = field(default_factory=list)

    def needs(self) -> list[str]:
        """The requirements in force now."""
        return [need for need in self.musts + self.told if need not in self.dropped]

    def add_need(self, need: str) -> None:
        if need in self.dropped:
            self.dropped.remove(need)
        if need not in self.musts + self.told:
            self.told.append(need)

    def drop_property(self, prop: str) -> list[str]:
        """Give up every live requirement on a property. Returns what was dropped."""
        wanted = prop.split(":", 1)[0].strip().lower()
        gone = [n for n in self.needs() if n.split(":", 1)[0].strip().lower() == wanted]
        self.dropped.extend(gone)
        return gone

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Item:
        return _from_dict(cls, data)


@dataclass
class Brief:
    """A shopping request in structured form."""

    request: str = ""
    items: list[Item] = field(default_factory=list)
    budget: float | None = None
    budget_scope: str = "total"
    pick: str = "any"
    pick_measure: str = ""
    in_stock_only: bool = True
    messages: int = 0
    steps: int | None = None
    process: list[dict[str, Any]] = field(default_factory=list)
    quiet_decline: bool = True
    wishes: list[str] = field(default_factory=list)
    fits: list[dict[str, int]] = field(default_factory=list)
    lookups: list[str] = field(default_factory=list)
    roles: dict[str, str] = field(default_factory=dict)
    buy_args: dict[str, Any] = field(default_factory=dict)
    refuse_args: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Brief:
        brief = _from_dict(cls, data)
        brief.items = [Item.from_dict(i) if isinstance(i, dict) else i for i in brief.items]
        return brief


@dataclass
class Listing:
    """A store listing as the catalog agent understands it."""

    id: str
    title: str = ""
    category: str = ""
    price: float | None = None
    in_stock: bool = True
    attributes: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    opened: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Listing:
        return _from_dict(cls, data)
