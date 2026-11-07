"""The store as seen by the agent team. Agents use the same services as shoppers do.

Agents never see card details and cannot complete a purchase themselves: `checkout` asks the
shopper to approve the exact cart and total, and the shopper's approval places the order.
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from . import cart, catalog
from .db import transaction

MAX_COMPARE = 4


class Shopper(Protocol):
    """The person an agent run works for."""

    def ask(self, question: str) -> str:
        """Ask a question and wait for the reply."""
        ...

    def approve(self, summary: dict[str, Any]) -> dict[str, Any]:
        """Ask to approve a purchase and wait. Returns {approved, order_id?, reason?}."""
        ...


def _tool(name: str, description: str, required=(), **properties) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


class StoreTools:
    """A `StoreBackend` for one agent run, working in that run's own cart."""

    def __init__(
        self, sessions: sessionmaker[Session], user_id: int, run_id: int | None, shopper: Shopper
    ):
        self.sessions = sessions
        self.user_id = user_id
        self.run_id = run_id
        self.shopper = shopper
        with transaction(sessions) as session:
            self._categories = [c["name"] for c in catalog.categories(session)]

    def tools(self) -> list[dict[str, Any]]:
        return [
            _tool(
                "search_products",
                "Search the catalog. Rows do not show attributes.",
                query={"type": "string"},
                category={"type": "string", "enum": self._categories},
                max_price={"type": "number", "minimum": 0},
                page={"type": "integer", "minimum": 1},
            ),
            _tool(
                "get_product",
                "Show one product's details and attributes.",
                required=["product_id"],
                product_id={"type": "string"},
            ),
            _tool(
                "compare_products",
                "Show several products in full, side by side.",
                required=["product_ids"],
                product_ids={
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": MAX_COMPARE,
                },
            ),
            _tool(
                "update_cart",
                "Set the quantity of a product in the cart. 0 removes it.",
                required=["product_id", "quantity"],
                product_id={"type": "string"},
                quantity={"type": "integer", "minimum": 0},
            ),
            _tool("view_cart", "Show the cart."),
            _tool("checkout", "Ask the shopper to approve the cart, and place the order if so."),
            _tool(
                "decline_purchase",
                "End without a purchase.",
                message={"type": "string", "maxLength": 300},
            ),
            _tool(
                "ask_shopper",
                "Send the shopper a question and wait for the reply.",
                required=["message"],
                message={"type": "string", "maxLength": 500},
            ),
        ]

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return getattr(self, f"_{name}")(args)

    def _search_products(self, args: dict[str, Any]) -> dict[str, Any]:
        with transaction(self.sessions) as session:
            found = catalog.search(
                session,
                query=args.get("query") or "",
                category=args.get("category"),
                max_price=args.get("max_price"),
                page=args.get("page", 1),
                page_size=8,
            )
        return {"observation": found}

    def _get_product(self, args: dict[str, Any]) -> dict[str, Any]:
        with transaction(self.sessions) as session:
            product = catalog.get_product(session, args["product_id"])
            page = catalog.product_page(product) if product else None
        return {"observation": page or {"error": f"No product {args['product_id']}."}}

    def _compare_products(self, args: dict[str, Any]) -> dict[str, Any]:
        with transaction(self.sessions) as session:
            found = [catalog.get_product(session, sku) for sku in args["product_ids"]]
            return {"observation": {"products": [catalog.product_page(p) for p in found if p]}}

    def _update_cart(self, args: dict[str, Any]) -> dict[str, Any]:
        with transaction(self.sessions) as session:
            try:
                view = cart.set_line(
                    session, self.user_id, args["product_id"], args["quantity"], self.run_id
                )
            except cart.CartError as error:
                return {"observation": {"error": str(error)}}
        return {"observation": view}

    def _view_cart(self, args: dict[str, Any]) -> dict[str, Any]:
        with transaction(self.sessions) as session:
            return {"observation": cart.view(session, self.user_id, self.run_id)}

    def _checkout(self, args: dict[str, Any]) -> dict[str, Any]:
        with transaction(self.sessions) as session:
            summary = cart.view(session, self.user_id, self.run_id)
        if not summary["lines"]:
            return {"observation": {"accepted": False, "error": "The cart is empty."}}
        outcome = self.shopper.approve(summary)
        if outcome.get("approved"):
            order = {"accepted": True, "order_id": outcome.get("order_id")}
            return {"observation": order, "finished": True}
        if outcome.get("failed"):
            return {"observation": {"accepted": False, "note": outcome["failed"]}}
        reason = outcome.get("reason") or "The shopper did not approve this order."
        return {"observation": {"accepted": False, "note": reason}, "finished": True}

    def _decline_purchase(self, args: dict[str, Any]) -> dict[str, Any]:
        with transaction(self.sessions) as session:
            cart.clear(session, self.user_id, self.run_id)
        return {"observation": {"accepted": True, "declined": True}, "finished": True}

    def _ask_shopper(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"observation": {"reply": self.shopper.ask(args["message"])}}
