"""Build the web application."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.accounts import router as accounts_router
from .api.cart import router as cart_router
from .api.catalog import router as catalog_router
from .api.orders import router as orders_router
from .api.runs import router as runs_router
from .models.http import model_from_env
from .store.db import make_engine, make_sessions
from .store.models import Base
from .store.runs import RunManager
from .web.routes import STATIC_DIR
from .web.routes import router as web_router


@dataclass
class Settings:
    database_url: str | None = None
    question_timeout: float = 900.0
    poll_seconds: float = 0.25


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    app = FastAPI(title="Kyo Shop", version=__version__)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessions = make_sessions(engine)
    app.state.runs = RunManager(
        app.state.sessions,
        model_factory=model_from_env,
        question_timeout=settings.question_timeout,
        poll_seconds=settings.poll_seconds,
    )
    app.state.runs.recover()
    app.include_router(catalog_router, prefix="/api")
    app.include_router(accounts_router, prefix="/api")
    app.include_router(cart_router, prefix="/api")
    app.include_router(orders_router, prefix="/api")
    app.include_router(runs_router, prefix="/api")
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
