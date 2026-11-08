"""The shopper-facing pages."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..api.deps import COOKIE, db, set_login_cookie
from ..store import accounts, cart, catalog, checkout, payments
from ..store.db import transaction

HERE = Path(__file__).parent
STATIC_DIR = HERE / "static"
templates = Jinja2Templates(directory=HERE / "templates")


def load_user(request: Request, session: Annotated[Session, Depends(db)]) -> None:
    """Make the signed-in user, if any, available to every page."""
    request.state.user = accounts.user_for_token(session, request.cookies.get(COOKIE))


router = APIRouter(
    include_in_schema=False, default_response_class=HTMLResponse, dependencies=[Depends(load_user)]
)
Db = Annotated[Session, Depends(db)]
PAGE_SIZE = 12


def render(request: Request, template: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    return templates.TemplateResponse(request, template, context, status_code=status_code)


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


def safe_next(target: str | None) -> str:
    """Only ever redirect within this site."""
    target = target or "/"
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def redirect(target: str) -> RedirectResponse:
    return RedirectResponse(target, status_code=303)


@router.get("/login", response_model=None)
def login_page(request: Request, next: str = "/") -> HTMLResponse | RedirectResponse:
    if request.state.user:
        return redirect(safe_next(next))
    return render(request, "login.html", next=safe_next(next), email="", error=None)


@router.post("/login", response_model=None)
def login(
    request: Request,
    session: Db,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
) -> HTMLResponse | RedirectResponse:
    user = accounts.authenticate(session, email, password)
    if user is None:
        return render(
            request,
            "login.html",
            401,
            next=safe_next(next),
            email=email,
            error="Wrong email or password.",
        )
    response = redirect(safe_next(next))
    set_login_cookie(response, accounts.start_session(session, user))
    return response


@router.get("/register", response_model=None)
def register_page(request: Request, next: str = "/") -> HTMLResponse | RedirectResponse:
    if request.state.user:
        return redirect(safe_next(next))
    return render(request, "register.html", next=safe_next(next), name="", email="", error=None)


@router.post("/register", response_model=None)
def register(
    request: Request,
    session: Db,
    name: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
) -> HTMLResponse | RedirectResponse:
    try:
        user = accounts.register(session, email, name, password)
    except accounts.AccountError as error:
        return render(
            request,
            "register.html",
            422,
            next=safe_next(next),
            name=name,
            email=email,
            error=str(error),
        )
    response = redirect(safe_next(next))
    set_login_cookie(response, accounts.start_session(session, user))
    return response


@router.post("/logout")
def logout(request: Request, session: Db) -> RedirectResponse:
    accounts.end_session(session, request.cookies.get(COOKIE))
    response = redirect("/")
    response.delete_cookie(COOKIE)
    return response


def login_first(request: Request) -> RedirectResponse:
    """Send a visitor to log in, returning to a safe page afterwards."""
    target = request.url.path if request.method == "GET" else request.headers.get("referer", "/")
    if target.startswith(str(request.base_url)):
        target = "/" + target[len(str(request.base_url)) :]
    return redirect(f"/login?next={quote(safe_next(target))}")


@router.get("/cart", response_model=None)
def cart_page(request: Request, session: Db) -> HTMLResponse | RedirectResponse:
    user = request.state.user
    if not user:
        return login_first(request)
    return render(request, "cart.html", cart=cart.view(session, user.id), error=None)


@router.post("/cart/{sku}", response_model=None)
def change_cart(
    request: Request, sku: str, session: Db, quantity: Annotated[str, Form()] = "1"
) -> HTMLResponse | RedirectResponse:
    user = request.state.user
    if not user:
        return login_first(request)
    try:
        cart.set_line(session, user.id, sku, int(quantity))
    except ValueError:
        error = "Enter a whole number for the quantity."
    except cart.CartError as refused:
        error = str(refused)
    else:
        return redirect("/cart")
    return render(request, "cart.html", 422, cart=cart.view(session, user.id), error=error)


@router.get("/checkout", response_model=None)
def checkout_page(request: Request, session: Db) -> HTMLResponse | RedirectResponse:
    user = request.state.user
    if not user:
        return login_first(request)
    view = cart.view(session, user.id)
    if not view["lines"]:
        return redirect("/cart")
    return render(
        request, "checkout.html", cart=view, error=None, checkout_key=secrets.token_hex(16)
    )


@router.post("/checkout", response_model=None)
def pay(
    request: Request,
    card_number: Annotated[str, Form()] = "",
    checkout_key: Annotated[str, Form(max_length=64)] = "",
) -> HTMLResponse | RedirectResponse:
    """The payment form carries a key, so submitting it twice places one order."""
    user = request.state.user
    if not user:
        return login_first(request)
    sessions = request.app.state.sessions
    status = 409
    try:
        with transaction(sessions) as session:
            order = checkout.place_order(session, user.id, card_number, checkout_key or None)
            return redirect(f"/orders/{order.id}?placed=1")
    except payments.PaymentDeclined as declined:
        error, status = str(declined), 402
    except checkout.CheckoutError as refused:
        error = str(refused)
    with transaction(sessions) as session:
        view = cart.view(session, user.id)
    if not view["lines"]:
        return redirect("/cart")
    return render(
        request, "checkout.html", status, cart=view, error=error, checkout_key=secrets.token_hex(16)
    )


@router.get("/orders", response_model=None)
def orders_page(request: Request, session: Db) -> HTMLResponse | RedirectResponse:
    user = request.state.user
    if not user:
        return login_first(request)
    return render(request, "orders.html", orders=checkout.list_orders(session, user.id))


@router.get("/orders/{order_id}", response_model=None)
def order_page(
    request: Request, order_id: int, session: Db, placed: int = 0
) -> HTMLResponse | RedirectResponse:
    user = request.state.user
    if not user:
        return login_first(request)
    order = checkout.get_order(session, user.id, order_id)
    if order is None:
        return not_found(request, "We could not find that order.")
    return render(request, "order.html", order=order, placed=bool(placed))
