"""What API routes share."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..store import accounts
from ..store.db import transaction
from ..store.models import User

COOKIE = "kyo_session"


def db(request: Request) -> Iterator[Session]:
    """A database session for one request, committed when the request succeeds.

    Declare it with scope="function", so the commit happens before the response is sent: a
    failed commit is then an error reply, not a success that was never saved.
    """
    with transaction(request.app.state.sessions) as session:
        yield session


def set_login_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(accounts.SESSION_LIFETIME.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.secure_cookies,
    )


def current_user(
    request: Request, session: Annotated[Session, Depends(db, scope="function")]
) -> User:
    user = accounts.user_for_token(session, request.cookies.get(COOKIE))
    if user is None:
        raise HTTPException(401, "Log in first.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
