"""Turn a cart into an order. All of it happens, or none of it does."""

from __future__ import annotations

import secrets
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from . import payments
from .cart import _lines, clear
from .catalog import to_dollars
from .models import Order, OrderLine, Payment, Product


class CheckoutError(Exception):
    """The order could not be placed. The message is safe to show to the user."""


def place_order(
    session: Session,
    user_id: int,
    card_number: str,
    idempotency_key: str | None = None,
    run_id: int | None = None,
    expected_total_cents: int | None = None,
) -> Order:
    """Charge the card, reduce stock, create the order and empty the cart, in one transaction.

    The caller's transaction must roll back if this raises, which undoes the stock reductions.
    A repeat of the same idempotency key returns the first order without charging again.
    `expected_total_cents` guards an approval: if the cart no longer costs what was approved,
    nothing is bought.
    """
    key = idempotency_key or secrets.token_hex(16)
    earlier = session.scalar(
        select(Order)
        .options(selectinload(Order.lines), selectinload(Order.payment))
        .where(Order.user_id == user_id, Order.idempotency_key == key)
    )
    if earlier is not None:
        return earlier
    lines = _lines(session, user_id, run_id)
    if not lines:
        raise CheckoutError("The cart is empty.")
    total = sum(line.product.price_cents * line.quantity for line in lines)
    if expected_total_cents is not None and total != expected_total_cents:
        raise CheckoutError("The cart has changed since it was approved.")

    order_lines = []
    for line in lines:
        # A conditional update, so two checkouts cannot both take the last unit.
        taken = session.execute(
            update(Product)
            .where(Product.id == line.product_id, Product.stock >= line.quantity)
            .values(stock=Product.stock - line.quantity)
            .execution_options(synchronize_session=False)
        )
        if taken.rowcount != 1:
            raise CheckoutError(f"{line.product.title} no longer has enough stock.")
        order_lines.append(
            OrderLine(
                product_id=line.product_id,
                sku=line.product.sku,
                title=line.product.title,
                unit_price_cents=line.product.price_cents,
                quantity=line.quantity,
            )
        )
    charged = payments.charge(card_number, total)
    order = Order(
        user_id=user_id,
        run_id=run_id,
        total_cents=total,
        idempotency_key=key,
        lines=order_lines,
        payment=Payment(reference=charged.reference, amount_cents=total, card_last4=charged.last4),
    )
    session.add(order)
    clear(session, user_id, run_id)
    session.flush()
    return order


def order_view(order: Order) -> dict[str, Any]:
    return {
        "id": order.id,
        "status": order.status,
        "total": to_dollars(order.total_cents),
        "created_at": order.created_at.isoformat(timespec="seconds"),
        "from_assistant": order.run_id is not None,
        "card_last4": order.payment.card_last4,
        "payment_reference": order.payment.reference,
        "lines": [
            {
                "id": line.sku,
                "title": line.title,
                "quantity": line.quantity,
                "price": to_dollars(line.unit_price_cents),
                "subtotal": to_dollars(line.unit_price_cents * line.quantity),
            }
            for line in order.lines
        ],
    }


def list_orders(session: Session, user_id: int) -> list[dict[str, Any]]:
    orders = session.scalars(
        select(Order)
        .options(selectinload(Order.lines), selectinload(Order.payment))
        .where(Order.user_id == user_id)
        .order_by(Order.id.desc())
    )
    return [order_view(o) for o in orders]


def get_order(session: Session, user_id: int, order_id: int) -> dict[str, Any] | None:
    order = session.scalar(
        select(Order)
        .options(selectinload(Order.lines), selectinload(Order.payment))
        .where(Order.id == order_id, Order.user_id == user_id)
    )
    return order_view(order) if order else None
