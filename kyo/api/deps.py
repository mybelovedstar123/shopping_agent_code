"""What API routes share."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from ..store.db import transaction


def db(request: Request) -> Iterator[Session]:
    """A database session for one request, committed when the request succeeds."""
    with transaction(request.app.state.sessions) as session:
        yield session
