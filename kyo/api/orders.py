"""Checkout and order endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..store import checkout, payments
from ..store.db import transaction
from .deps import CurrentUser, db

router = APIRouter(tags=["orders"])


class CheckoutBody(BaseModel):
    card_number: str = Field(repr=False)
    # The cart's `total_cents` as the client showed it. If given, a cart that now costs anything
    # else is refused instead of charged.
    expected_total_cents: int | None = Field(default=None, ge=0)


@router.post("/checkout", status_code=201)
def place_order(
    body: CheckoutBody,
    request: Request,
    user: CurrentUser,
    idempotency_key: Annotated[str | None, Header(max_length=64)] = None,
) -> dict[str, Any]:
    """Places the order in its own transaction, so any failure undoes all of it."""
    try:
        with transaction(request.app.state.sessions) as session:
            order = checkout.place_order(
                session,
                user.id,
                body.card_number,
                idempotency_key,
                expected_total_cents=body.expected_total_cents,
            )
            return checkout.order_view(order)
    except payments.PaymentDeclined as error:
        raise HTTPException(402, str(error)) from None
    except checkout.CheckoutError as error:
        raise HTTPException(409, str(error)) from None


@router.get("/orders")
def list_orders(user: CurrentUser, session=Depends(db)) -> list[dict[str, Any]]:  # noqa: B008
    return checkout.list_orders(session, user.id)


@router.get("/orders/{order_id}")
def get_order(
    order_id: int,
    user: CurrentUser,
    session=Depends(db),  # noqa: B008
) -> dict[str, Any]:
    found = checkout.get_order(session, user.id, order_id)
    if found is None:
        raise HTTPException(404, "no such order")
    return found
