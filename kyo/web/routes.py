"""The shopper-facing pages."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..api.deps import db
from ..store import catalog

HERE = Path(__file__).parent
STATIC_DIR = HERE / "static"
templates = Jinja2Templates(directory=HERE / "templates")

router = APIRouter(include_in_schema=False, default_response_class=HTMLResponse)
Db = Annotated[Session, Depends(db)]
PAGE_SIZE = 12


def render(request: Request, name: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def not_found(request: Request, message: str = "We could not find that page.") -> HTMLResponse:
    return render(request, "error.html", 404, title="Not found", message=message)


@router.get("/")
def home(request: Request, session: Db) -> HTMLResponse:
    return render(request, "home.html", categories=catalog.categories(session))


@router.get("/category/{slug}")
def category_page(request: Request, slug: str, session: Db, page: int = 1) -> HTMLResponse:
    category = catalog.category_by_slug(session, slug)
    if category is None:
        return not_found(request, "We could not find that category.")
    found = catalog.search(session, category=slug, page=max(1, page), page_size=PAGE_SIZE)
    return render(
        request, "category.html", category=category, found=found, products=found["results"]
    )
