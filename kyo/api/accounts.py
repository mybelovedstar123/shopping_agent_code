"""Account endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..store import accounts
from ..store.models import User
from .deps import COOKIE, CurrentUser, db, set_login_cookie

router = APIRouter(tags=["accounts"])
Db = Annotated[Session, Depends(db)]


class Registration(BaseModel):
    email: str
    name: str
    password: str


class Credentials(BaseModel):
    email: str
    password: str


def _public(user: User) -> dict[str, Any]:
    return {"id": user.id, "email": user.email, "name": user.name}


@router.post("/register", status_code=201)
def register(body: Registration, response: Response, session: Db) -> dict[str, Any]:
    try:
        user = accounts.register(session, body.email, body.name, body.password)
    except accounts.AccountError as error:
        raise HTTPException(422, str(error)) from None
    set_login_cookie(response, accounts.start_session(session, user))
    return _public(user)


@router.post("/login")
def login(body: Credentials, response: Response, session: Db) -> dict[str, Any]:
    user = accounts.authenticate(session, body.email, body.password)
    if user is None:
        raise HTTPException(401, "Wrong email or password.")
    set_login_cookie(response, accounts.start_session(session, user))
    return _public(user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, session: Db) -> None:
    accounts.end_session(session, request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE)


@router.get("/me")
def me(user: CurrentUser) -> dict[str, Any]:
    return _public(user)
