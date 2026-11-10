"""Cart rules. A cart belongs to a user, or to one of that user's agent runs."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .catalog import get_product, to_dollars
from .models import CartLine, Product

MAX_QUANTITY = 99


class CartError(Exception):
    """A cart change was refused. The message is safe to show to the user."""


def _lines(session: Session, user_id: int, run_id: int | None) -> list[CartLine]:
    statement = (
        select(CartLine)
        .options(joinedload(CartLine.product).joinedload(Product.category))
        .where(CartLine.user_id == user_id)
        .where(CartLine.run_id == run_id if run_id is not None else CartLine.run_id.is_(None))
        .order_by(CartLine.id)
    )
    return list(session.scalars(statement))


def view(session: Session, user_id: int, run_id: int | None = None) -> dict[str, Any]:
    lines = []
    total = 0
    for line in _lines(session, user_id, run_id):
        subtotal = line.product.price_cents * line.quantity
        total += subtotal
        lines.append(
            {
                "id": line.product.sku,
                "title": line.product.title,
                "quantity": line.quantity,
                "price": to_dollars(line.product.price_cents),
                "subtotal": to_dollars(subtotal),
                "stock": line.product.stock,
            }
        )
    return {"lines": lines, "total": to_dollars(total), "total_cents": total}


def set_line(
    session: Session, user_id: int, sku: str, quantity: int, run_id: int | None = None
) -> dict[str, Any]:
    """Set a product's quantity. Zero removes it."""
    product = get_product(session, sku)
    if product is None:
        raise CartError(f"No product {sku}.")
    if quantity < 0 or quantity > MAX_QUANTITY:
        raise CartError(f"Quantity must be between 0 and {MAX_QUANTITY}.")
    if quantity > product.stock:
        raise CartError(f"Only {product.stock} of {sku} in stock.")
    line = next((x for x in _lines(session, user_id, run_id) if x.product_id == product.id), None)
    if quantity == 0:
        if line is not None:
            session.delete(line)
    elif line is None:
        session.add(
            CartLine(user_id=user_id, run_id=run_id, product_id=product.id, quantity=quantity)
        )
    else:
        line.quantity = quantity
    session.flush()
    return view(session, user_id, run_id)


def add_to_line(
    session: Session, user_id: int, sku: str, quantity: int, run_id: int | None = None
) -> dict[str, Any]:
    """Add to a product's quantity, as an "Add to cart" button does."""
    if quantity < 1:
        raise CartError(f"Quantity must be between 1 and {MAX_QUANTITY}.")
    product = get_product(session, sku)
    if product is None:
        raise CartError(f"No product {sku}.")
    line = next((x for x in _lines(session, user_id, run_id) if x.product_id == product.id), None)
    held = line.quantity if line is not None else 0
    return set_line(session, user_id, sku, held + quantity, run_id)


def clear(session: Session, user_id: int, run_id: int | None = None) -> None:
    for line in _lines(session, user_id, run_id):
        session.delete(line)
    session.flush()
