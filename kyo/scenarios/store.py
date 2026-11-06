"""An in-memory store for tests and evaluations, with the awkward habits of real stores.

It pages results in no price order, shows attributes only on product pages, sells out carted
items, changes budgets through notes in tool results, and answers shopper questions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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


@dataclass
class Product:
    id: str
    title: str
    category: str
    price: float
    attributes: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    stock: int = 10


class ScenarioStore:
    """A `StoreBackend` with scripted events.

    answers maps words in a question to the shopper's reply; `sell_out_on_cart` lists products
    that sell out once carted; `refuse_once` is a refusal note for the first checkout;
    `notes` maps a step number to a note added to that step's result.
    """

    PAGE_SIZE = 3

    def __init__(
        self,
        products: list[Product],
        answers: dict[str, str] | None = None,
        sell_out_on_cart: list[str] | None = None,
        refuse_once: str | None = None,
        notes: dict[int, str] | None = None,
        budget: float | None = None,
    ):
        self.products = {p.id: p for p in products}
        self.answers = answers or {}
        self.sell_out_on_cart = set(sell_out_on_cart or [])
        self.refuse_once = refuse_once
        self.notes = notes or {}
        self.budget = budget
        self.cart: dict[str, int] = {}
        self.order: dict[str, int] | None = None
        self.declined = False
        self.questions: list[str] = []
        self.steps = 0

    def tools(self) -> list[dict[str, Any]]:
        categories = sorted({p.category for p in self.products.values()})
        return [
            _tool(
                "search_products",
                "Search the catalog. Results are paged and not sorted by price.",
                query={"type": "string"},
                category={"type": "string", "enum": categories},
                max_price={"type": "number"},
                page={"type": "integer", "minimum": 1},
            ),
            _tool(
                "get_product",
                "Show one product's details and attributes.",
                required=["product_id"],
                product_id={"type": "string"},
            ),
            _tool(
                "update_cart",
                "Set the quantity of a product in the cart. 0 removes it.",
                required=["product_id", "quantity"],
                product_id={"type": "string"},
                quantity={"type": "integer", "minimum": 0},
            ),
            _tool("view_cart", "Show the cart."),
            _tool("checkout", "Place the order for the cart."),
            _tool(
                "decline_purchase",
                "End without a purchase.",
                message={"type": "string", "maxLength": 200},
            ),
            _tool(
                "ask_shopper",
                "Send the shopper a question.",
                required=["message"],
                message={"type": "string"},
            ),
        ]

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.steps += 1
        result = getattr(self, f"_{name}")(args)
        finished = result.pop("_finished", False)
        if self.steps in self.notes:
            result["note"] = self.notes[self.steps]
        return {"observation": result, "finished": finished}

    @staticmethod
    def _row(product: Product) -> dict[str, Any]:
        return {
            "id": product.id,
            "title": product.title,
            "category": product.category,
            "price": product.price,
            "in_stock": product.stock > 0,
        }

    def _search_products(self, args: dict[str, Any]) -> dict[str, Any]:
        wanted = [w.lower().rstrip("s") for w in str(args.get("query") or "").split()]
        found = []
        for product in self.products.values():
            text = f"{product.title} {product.category}".lower()
            if args.get("category") and product.category != args["category"]:
                continue
            if args.get("max_price") is not None and product.price > args["max_price"]:
                continue
            if all(w in text for w in wanted):
                found.append(product)
        page = args.get("page", 1)
        start = (page - 1) * self.PAGE_SIZE
        rows = found[start : start + self.PAGE_SIZE]
        more = start + self.PAGE_SIZE < len(found)
        return {
            "results": [self._row(p) for p in rows],
            "total": len(found),
            "next_page": page + 1 if more else None,
        }

    def _get_product(self, args: dict[str, Any]) -> dict[str, Any]:
        product = self.products.get(args["product_id"])
        if product is None:
            return {"error": f"no product {args['product_id']}"}
        return {
            **self._row(product),
            "description": product.description,
            "attributes": dict(product.attributes),
        }

    def _update_cart(self, args: dict[str, Any]) -> dict[str, Any]:
        ident, quantity = args["product_id"], args["quantity"]
        product = self.products.get(ident)
        if product is None:
            return {"error": f"no product {ident}"}
        if quantity > 0 and product.stock < quantity:
            return {"error": f"only {product.stock} of {ident} in stock"}
        if quantity == 0:
            self.cart.pop(ident, None)
        else:
            self.cart[ident] = quantity
        result = self._view_cart({})
        if ident in self.sell_out_on_cart and quantity > 0:
            self.sell_out_on_cart.discard(ident)
            product.stock = 0
            result["note"] = f"Sorry, {ident} has just sold out and can no longer be bought."
        return result

    def _view_cart(self, args: dict[str, Any]) -> dict[str, Any]:
        lines = [
            {"id": i, "quantity": n, "price": self.products[i].price} for i, n in self.cart.items()
        ]
        return {"lines": lines, "total": round(sum(x["price"] * x["quantity"] for x in lines), 2)}

    def _checkout(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.refuse_once:
            note, self.refuse_once = self.refuse_once, None
            return {"accepted": False, "note": note}
        if not self.cart:
            return {"accepted": False, "error": "the cart is empty"}
        gone = [i for i in self.cart if self.products[i].stock < self.cart[i]]
        if gone:
            return {"accepted": False, "note": f"{gone[0]} has sold out and cannot be bought."}
        self.order = dict(self.cart)
        return {"accepted": True, "_finished": True}

    def _decline_purchase(self, args: dict[str, Any]) -> dict[str, Any]:
        self.declined = True
        return {"accepted": True, "_finished": True}

    def _ask_shopper(self, args: dict[str, Any]) -> dict[str, Any]:
        question = args["message"]
        self.questions.append(question)
        low = question.lower()
        for words, reply in self.answers.items():
            if words.lower() in low:
                return {"reply": reply}
        return {"reply": "I don't mind."}
