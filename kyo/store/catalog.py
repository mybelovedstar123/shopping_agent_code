"""Search and read the catalog. Used by the API, the web pages and the agents' tools."""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..fit import words
from .models import Category, Product

SORTS = ("relevance", "price_asc", "price_desc", "title")
MAX_PAGE_SIZE = 60


def to_dollars(cents: int) -> float:
    return round(cents / 100, 2)


def product_row(product: Product) -> dict[str, Any]:
    """The summary shown in lists. Attributes appear only on the product page."""
    return {
        "id": product.sku,
        "title": product.title,
        "category": product.category.name,
        "price": to_dollars(product.price_cents),
        "in_stock": product.stock > 0,
    }


def product_page(product: Product) -> dict[str, Any]:
    return {
        **product_row(product),
        "description": product.description,
        "stock": product.stock,
        "attributes": dict(product.attributes),
    }


def categories(session: Session) -> list[dict[str, Any]]:
    rows = session.scalars(select(Category).order_by(Category.name))
    return [{"name": c.name, "slug": c.slug} for c in rows]


def category_by_slug(session: Session, slug: str) -> Category | None:
    return session.scalar(select(Category).where(Category.slug == slug))


def get_product(session: Session, sku: str) -> Product | None:
    return session.scalar(
        select(Product).options(joinedload(Product.category)).where(Product.sku == sku)
    )


def _score(product: Product, wanted: list[str]) -> int | None:
    """How well a product matches words. None when a word is missing entirely."""
    title = set(words(product.title))
    category = set(words(product.category.name))
    rest = set(words(product.description))
    for value in product.attributes.values():
        rest |= set(words(value))
    score = 0
    for word in wanted:
        if word in title:
            score += 3
        elif word in category:
            score += 2
        elif word in rest:
            score += 1
        else:
            return None
    return score


def _has_attribute(product: Product, name: str, wanted: str) -> bool:
    value = product.attributes.get(name)
    return value is not None and str(value).lower() == wanted.lower()


def search(
    session: Session,
    query: str = "",
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    attributes: dict[str, str] | None = None,
    in_stock: bool = False,
    sort: str = "relevance",
    page: int = 1,
    page_size: int = 12,
) -> dict[str, Any]:
    """Find products. Prices are in dollars. `category` is a category's name or slug."""
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {', '.join(SORTS)}")
    page = max(1, page)
    page_size = min(max(1, page_size), MAX_PAGE_SIZE)
    statement = select(Product).join(Product.category).options(joinedload(Product.category))
    if category:
        statement = statement.where((Category.name == category) | (Category.slug == category))
    if min_price is not None:
        statement = statement.where(Product.price_cents >= round(min_price * 100))
    if max_price is not None:
        statement = statement.where(Product.price_cents <= round(max_price * 100))
    if in_stock:
        statement = statement.where(Product.stock > 0)

    wanted = words(query)
    scored = []
    for product in session.scalars(statement.order_by(Product.id)):
        score = _score(product, wanted)
        if score is None:
            continue
        if all(_has_attribute(product, k, v) for k, v in (attributes or {}).items()):
            scored.append((score, product))
    if sort == "relevance":
        scored.sort(key=lambda pair: -pair[0])
    elif sort == "title":
        scored.sort(key=lambda pair: pair[1].title)
    else:
        scored.sort(key=lambda pair: pair[1].price_cents, reverse=sort == "price_desc")
    total = len(scored)
    start = (page - 1) * page_size
    chosen = [product for _, product in scored[start : start + page_size]]
    return {
        "results": [product_row(p) for p in chosen],
        "total": total,
        "page": page,
        "page_size": page_size,
        "next_page": page + 1 if start + page_size < total else None,
    }


def facets(session: Session, category: str) -> dict[str, list[dict[str, Any]]]:
    """The attribute values available in a category, with how many products have each."""
    statement = (
        select(Product)
        .join(Product.category)
        .where((Category.name == category) | (Category.slug == category))
    )
    counts: dict[str, Counter] = {}
    for product in session.scalars(statement):
        for key, value in product.attributes.items():
            counts.setdefault(key, Counter())[str(value)] += 1
    return {
        key: [{"value": v, "count": n} for v, n in sorted(counter.items())]
        for key, counter in sorted(counts.items())
    }
