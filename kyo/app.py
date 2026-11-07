"""Build the web application."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from . import __version__
from .api.accounts import router as accounts_router
from .api.cart import router as cart_router
from .api.catalog import router as catalog_router
from .store.db import make_engine, make_sessions
from .store.models import Base


@dataclass
class Settings:
    database_url: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    app = FastAPI(title="Kyo Shop", version=__version__)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessions = make_sessions(engine)
    app.include_router(catalog_router, prefix="/api")
    app.include_router(accounts_router, prefix="/api")
    app.include_router(cart_router, prefix="/api")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def sessions_of(app: FastAPI) -> sessionmaker[Session]:
    return app.state.sessions
