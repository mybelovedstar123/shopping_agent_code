"""A shopper buying from start to finish through the pages, as a browser would."""

import re

from sqlalchemy import select
from webutil import post_form, register, text_of

from kyo.store import payments
from kyo.store.db import transaction
from kyo.store.models import Order, Product

GOOD = "4242 4242 4242 4242"


def in_stock(app):
    with transaction(app.state.sessions) as session:
        rows = session.scalars(select(Product).where(Product.sku.like("MUG-%"))).all()
        product = next(p for p in rows if p.stock >= 3)
        return product.sku, product.title, product.price_cents, product.stock


def add(client, sku, quantity=1):
    return post_form(client, f"/cart/{sku}", {"quantity": quantity})


def checkout_key(client):
    page = client.get("/checkout")
    return re.search(r'name="checkout_key" value="([^"]+)"', page.text).group(1)


def pay(client, card=GOOD, key=None):
    data = {"card_number": card, "checkout_key": key or checkout_key(client)}
    return post_form(client, "/checkout", data)


def test_full_purchase_through_the_pages(client, app):
    sku, title, price, stock = in_stock(app)
    register(client)
    page = client.get(f"/product/{sku}")
    assert f'action="/cart/{sku}"' in page.text
    reply = add(client, sku, 2)
    assert title in reply.text and f"${price * 2 / 100:.2f}" in text_of(reply)
    assert f"${price * 2 / 100:.2f}" in text_of(client.get("/checkout"))
    reply = pay(client)
    assert reply.status_code == 200 and "Your order is placed" in reply.text
    assert "ending 4242" in text_of(reply) and title in reply.text
    assert "Your cart is empty" in client.get("/cart").text
    assert "Order 1" in text_of(client.get("/orders"))
    with transaction(app.state.sessions) as session:
        assert session.scalar(select(Product.stock).where(Product.sku == sku)) == stock - 2
        assert session.query(Order).one().total_cents == price * 2


def test_cart_requires_login_and_returns_afterwards(client, app):
    sku = in_stock(app)[0]
    reply = client.get("/cart")
    assert "/login" in str(reply.url) and "next=/cart" in str(reply.url).replace("%2F", "/")
    reply = add(client, sku)
    assert "/login" in str(reply.url)


def test_quantity_can_be_changed_and_a_line_removed(client, app):
    sku, _, price, _ = in_stock(app)
    register(client)
    add(client, sku, 1)
    assert f"${price * 3 / 100:.2f}" in text_of(add(client, sku, 3))
    assert "Your cart is empty" in add(client, sku, 0).text


def test_too_many_is_explained_and_changes_nothing(client, app):
    sku, _, _, stock = in_stock(app)
    register(client)
    reply = add(client, sku, stock + 1)
    assert reply.status_code == 422 and f"Only {stock} of {sku} in stock" in reply.text
    assert "Your cart is empty" in reply.text


def test_invalid_quantity_is_explained(client, app):
    sku = in_stock(app)[0]
    register(client)
    assert add(client, sku, "lots").status_code == 422


def test_declined_card_shows_the_reason_and_keeps_the_cart(client, app):
    sku, title, _, stock = in_stock(app)
    register(client)
    add(client, sku)
    reply = pay(client, payments.DECLINED_TEST_CARD)
    assert reply.status_code == 402 and "The card was declined" in reply.text
    assert title in client.get("/cart").text
    with transaction(app.state.sessions) as session:
        assert session.scalar(select(Product.stock).where(Product.sku == sku)) == stock


def test_card_number_is_not_echoed_back(client, app):
    register(client)
    add(client, in_stock(app)[0])
    reply = pay(client, payments.DECLINED_TEST_CARD)
    assert payments.DECLINED_TEST_CARD not in reply.text


def test_checkout_of_an_empty_cart_goes_back_to_the_cart(client):
    register(client)
    assert str(client.get("/checkout").url).endswith("/cart")


def test_submitting_the_payment_form_twice_places_one_order(client, app):
    sku, _, _, stock = in_stock(app)
    register(client)
    add(client, sku)
    key = checkout_key(client)
    first = pay(client, key=key)
    second = pay(client, key=key)
    assert first.url == second.url and "Your order is placed" in second.text
    with transaction(app.state.sessions) as session:
        assert session.query(Order).count() == 1
        assert session.scalar(select(Product.stock).where(Product.sku == sku)) == stock - 1


def test_stock_gone_before_payment_is_explained(client, app):
    sku, title, _, _ = in_stock(app)
    register(client)
    add(client, sku)
    with transaction(app.state.sessions) as session:
        session.scalar(select(Product).where(Product.sku == sku)).stock = 0
    reply = pay(client)
    assert reply.status_code == 409 and f"{title} no longer has enough stock" in reply.text


def test_orders_and_order_pages_are_private(client, app):
    from fastapi.testclient import TestClient

    register(client)
    add(client, in_stock(app)[0])
    order_url = str(pay(client).url)
    other = TestClient(app, follow_redirects=True)
    register(other, email="other@example.com")
    assert other.get(order_url).status_code == 404
    assert "You have no orders yet" in other.get("/orders").text


def test_navigation_links_to_cart_and_orders_when_signed_in(client):
    assert 'href="/cart"' not in client.get("/").text
    reply = register(client)
    assert 'href="/cart"' in reply.text and 'href="/orders"' in reply.text


def test_out_of_stock_product_has_no_add_form(client, app):
    with transaction(app.state.sessions) as session:
        session.scalar(select(Product).where(Product.sku == "MUG-0001")).stock = 0
    assert 'action="/cart/MUG-0001"' not in client.get("/product/MUG-0001").text


def test_a_price_change_after_the_page_was_shown_is_not_charged(client, app):
    sku, title, price, stock = in_stock(app)
    register(client)
    add(client, sku, 2)
    page = client.get("/checkout")
    assert f"Pay ${price * 2 / 100:.2f}" in page.text
    key = re.search(r'name="checkout_key" value="([^"]+)"', page.text).group(1)
    shown = re.search(r'name="expected_total_cents" value="([^"]+)"', page.text).group(1)
    with transaction(app.state.sessions) as session:
        session.scalar(select(Product).where(Product.sku == sku)).price_cents = price + 100
    data = {"card_number": GOOD, "checkout_key": key, "expected_total_cents": shown}
    reply = post_form(client, "/checkout", data)
    new_total = f"${(price + 100) * 2 / 100:.2f}"
    assert reply.status_code == 409
    assert f"The cart total is now {new_total}, not the ${price * 2 / 100:.2f} shown" in reply.text
    assert f"Pay {new_total}" in reply.text, "the page offers the new total instead"
    with transaction(app.state.sessions) as session:
        assert session.query(Order).count() == 0
        assert session.scalar(select(Product.stock).where(Product.sku == sku)) == stock
    assert title in client.get("/cart").text
