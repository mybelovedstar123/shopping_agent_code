"""Build the web application."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
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
from .web.routes import STATIC_DIR, render
from .web.routes import router as web_router
from .web.security import LoginThrottle, protect_responses


def _secure_cookies_from_env() -> bool:
    return os.environ.get("KYO_SECURE_COOKIES", "") == "1"


@dataclass
class Settings:
    database_url: str | None = None
    question_timeout: float = 900.0
    poll_seconds: float = 0.25
    secure_cookies: bool = field(default_factory=_secure_cookies_from_env)


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
    app.state.login_throttle = LoginThrottle()
    app.middleware("http")(protect_responses)
    app.include_router(catalog_router, prefix="/api")
    app.include_router(accounts_router, prefix="/api")
    app.include_router(cart_router, prefix="/api")
    app.include_router(orders_router, prefix="/api")
    app.include_router(runs_router, prefix="/api")
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(HTTPException)
    def show_error(request: Request, error: HTTPException):
        wants_page = "text/html" in request.headers.get("accept", "") and not (
            request.url.path.startswith("/api")
        )
        if not wants_page:
            return JSONResponse({"detail": error.detail}, error.status_code)
        return render(
            request, "error.html", error.status_code, title="Sorry", message=str(error.detail)
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
