"""Shared by agent tests."""


class Notes:
    """A context that records notes and answers requests from a table of fake agents."""

    def __init__(self, handlers=None):
        self.notes = []
        self.requests = []
        self.handlers = handlers or {}

    def note(self, text, **data):
        self.notes.append(text)

    def request(self, recipient, kind, payload=None):
        self.requests.append((recipient, kind, payload))
        return self.handlers[(recipient, kind)](payload or {})


def tool(name, description="", **properties):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties},
        },
    }


STORE_TOOLS = [
    tool(
        "search_products",
        "Search the catalog",
        query={"type": "string"},
        category={"type": "string", "enum": ["Desk Lamps", "Mugs", "Office Chairs"]},
    ),
    tool("get_product", "Show one product's details", product_id={"type": "string"}),
    tool("compare_products", "Compare products", product_ids={"type": "array"}),
    tool("update_cart", "Set a cart line", product_id={"type": "string"}),
    tool("view_cart", "Show the cart"),
    tool("checkout", "Place the order"),
    tool("decline_purchase", "End without a purchase"),
    tool("ask_shopper", "Send the shopper a question", message={"type": "string"}),
]
