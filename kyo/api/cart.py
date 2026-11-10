"""Cart endpoints. The cart is always the signed-in user's own."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..store import cart
from .deps import CurrentUser, db

router = APIRouter(tags=["cart"])
Db = Annotated[Session, Depends(db, scope="function")]


class Quantity(BaseModel):
    quantity: int


@router.get("/cart")
def view_cart(user: CurrentUser, session: Db) -> dict[str, Any]:
    return cart.view(session, user.id)


@router.put("/cart/{sku}")
def set_line(sku: str, body: Quantity, user: CurrentUser, session: Db) -> dict[str, Any]:
    try:
        return cart.set_line(session, user.id, sku, body.quantity)
    except cart.CartError as error:
        raise HTTPException(422, str(error)) from None


@router.delete("/cart/{sku}")
def remove_line(sku: str, user: CurrentUser, session: Db) -> dict[str, Any]:
    try:
        return cart.set_line(session, user.id, sku, 0)
    except cart.CartError as error:
        raise HTTPException(404, str(error)) from None
