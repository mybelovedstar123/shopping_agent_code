"""Catalog endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..store import catalog
from .deps import db

router = APIRouter(tags=["catalog"])
Db = Annotated[Session, Depends(db)]

RESERVED = {"q", "category", "min_price", "max_price", "in_stock", "sort", "page", "page_size"}


@router.get("/categories")
def list_categories(session: Db) -> list[dict[str, Any]]:
    return catalog.categories(session)


@router.get("/categories/{category}/facets")
def category_facets(category: str, session: Db) -> dict[str, Any]:
    if catalog.category_by_slug(session, category) is None:
        raise HTTPException(404, "no such category")
    return catalog.facets(session, category)


@router.get("/products")
def search_products(
    request: Request,
    session: Db,
    q: str = "",
    category: str | None = None,
    min_price: Annotated[float | None, Query(ge=0)] = None,
    max_price: Annotated[float | None, Query(ge=0)] = None,
    in_stock: bool = False,
    sort: str = "relevance",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=catalog.MAX_PAGE_SIZE)] = 12,
) -> dict[str, Any]:
    """Any other query parameter filters by that attribute, as in ?colour=red."""
    attributes = {k: v for k, v in request.query_params.items() if k not in RESERVED}
    try:
        return catalog.search(
            session, q, category, min_price, max_price, attributes, in_stock, sort, page, page_size
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.get("/products/{sku}")
def product_detail(sku: str, session: Db) -> dict[str, Any]:
    product = catalog.get_product(session, sku)
    if product is None:
        raise HTTPException(404, "no such product")
    return catalog.product_page(product)
