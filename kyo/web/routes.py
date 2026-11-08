"""The shopper-facing pages."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

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


SORT_LABELS = [
    ("relevance", "Best match"),
    ("price_asc", "Price, low to high"),
    ("price_desc", "Price, high to low"),
    ("title", "Name"),
]


def _price(value: str | None) -> float | None:
    """Read a price field. A blank or invalid one is treated as not given."""
    try:
        number = float(value) if value else None
    except ValueError:
        return None
    return number if number is not None and 0 <= number < 1e9 else None


def _whole(value: str | None, default: int, top: int) -> int:
    try:
        return min(max(1, int(value or default)), top)
    except ValueError:
        return default


@router.get("/search")
def search_page(request: Request, session: Db) -> HTMLResponse:
    params = request.query_params
    q = params.get("q", "").strip()
    category = params.get("category") or None
    known = {c["slug"] for c in catalog.categories(session)}
    category = category if category in known else None
    sort = params.get("sort", "relevance")
    sort = sort if sort in catalog.SORTS else "relevance"
    min_price, max_price = _price(params.get("min_price")), _price(params.get("max_price"))
    in_stock = params.get("in_stock") == "true"
    facets = catalog.facets(session, category) if category else {}
    attributes = {k: params[k] for k in facets if params.get(k)}
    found = catalog.search(
        session,
        q,
        category,
        min_price,
        max_price,
        attributes,
        in_stock,
        sort,
        _whole(params.get("page"), 1, 10000),
        _whole(params.get("page_size"), PAGE_SIZE, catalog.MAX_PAGE_SIZE),
    )
    kept = [(k, v) for k, v in params.multi_items() if v and k != "page"]
    return render(
        request,
        "search.html",
        q=q,
        category=category,
        categories=catalog.categories(session),
        min_price=min_price,
        max_price=max_price,
        in_stock=in_stock,
        facets=facets,
        attributes=attributes,
        sort=sort,
        sorts=SORT_LABELS,
        found=found,
        products=found["results"],
        page_query=urlencode(kept),
    )


@router.get("/product/{sku}")
def product_page(request: Request, sku: str, session: Db) -> HTMLResponse:
    product = catalog.get_product(session, sku)
    if product is None:
        return not_found(request, "We could not find that product.")
    return render(
        request,
        "product.html",
        product=catalog.product_page(product),
        category_slug=product.category.slug,
    )
