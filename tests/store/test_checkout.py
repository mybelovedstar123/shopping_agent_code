import pytest
from sqlalchemy import inspect, select, text

from kyo.store import payments
from kyo.store.db import transaction
from kyo.store.models import Order, Product

GOOD = payments.APPROVED_TEST_CARD
BAD = payments.DECLINED_TEST_CARD


def add(client, sku, quantity):
    assert client.put(f"/api/cart/{sku}", json={"quantity": quantity}).status_code == 200


def buy(client, card=GOOD, key=None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post("/api/checkout", json={"card_number": card}, headers=headers)


def stock(app, sku):
    with transaction(app.state.sessions) as session:
        return session.scalar(select(Product.stock).where(Product.sku == sku))


@pytest.mark.parametrize(
    "number", ["4242 4242 4242 4242", "4242-4242-4242-4242", "4111111111111111"]
)
def test_valid_cards_are_charged(number):
    charged = payments.charge(number, 500)
    assert charged.amount_cents == 500 and charged.reference.startswith("sim_")
    assert charged.last4 == number[-4:]


@pytest.mark.parametrize("number", ["", "1234", "4242424242424241", "abcd" * 4, BAD])
def test_invalid_and_declined_cards_are_refused(number):
    with pytest.raises(payments.PaymentDeclined):
        payments.charge(number, 500)


def test_checkout_creates_the_order_reduces_stock_and_empties_the_cart(shopper, stocked_app):
    add(shopper, "L1", 2)
    add(shopper, "L2", 1)
    reply = buy(shopper)
    assert reply.status_code == 201
    order = reply.json()
    assert order["total"] == 70.5 and order["card_last4"] == "4242"
    assert [(x["id"], x["quantity"], x["subtotal"]) for x in order["lines"]] == [
        ("L1", 2, 60.0),
        ("L2", 1, 10.5),
    ]
    assert stock(stocked_app, "L1") == 3 and stock(stocked_app, "L2") == 0
    assert shopper.get("/api/cart").json()["lines"] == []


def test_declined_card_changes_nothing(shopper, stocked_app):
    add(shopper, "L1", 2)
    reply = buy(shopper, BAD)
    assert reply.status_code == 402 and reply.json()["detail"] == "The card was declined."
    assert stock(stocked_app, "L1") == 5
    assert len(shopper.get("/api/cart").json()["lines"]) == 1
    assert shopper.get("/api/orders").json() == []


def test_empty_cart_cannot_be_checked_out(shopper):
    reply = buy(shopper)
    assert reply.status_code == 409 and "empty" in reply.json()["detail"]


def test_stock_gone_since_carting_changes_nothing(shopper, stocked_app):
    add(shopper, "L1", 2)
    add(shopper, "L2", 1)
    with transaction(stocked_app.state.sessions) as session:
        session.scalar(select(Product).where(Product.sku == "L2")).stock = 0
    reply = buy(shopper)
    assert reply.status_code == 409 and "Slim Desk Lamp" in reply.json()["detail"]
    assert stock(stocked_app, "L1") == 5, "the first line's stock must be restored"
    assert shopper.get("/api/orders").json() == []


def test_repeated_idempotency_key_returns_the_same_order(shopper, stocked_app):
    add(shopper, "L1", 1)
    first = buy(shopper, key="abc").json()
    add(shopper, "L1", 1)
    second = buy(shopper, key="abc").json()
    assert second["id"] == first["id"]
    assert stock(stocked_app, "L1") == 4, "the card and the stock were charged only once"
    assert len(shopper.get("/api/orders").json()) == 1


def test_price_shown_on_an_order_survives_later_price_changes(shopper, stocked_app):
    add(shopper, "L1", 1)
    order = buy(shopper).json()
    with transaction(stocked_app.state.sessions) as session:
        session.scalar(select(Product).where(Product.sku == "L1")).price_cents = 99999
    assert shopper.get(f"/api/orders/{order['id']}").json()["lines"][0]["price"] == 30.0


def test_card_numbers_are_never_stored(shopper, stocked_app):
    add(shopper, "L1", 1)
    buy(shopper)
    with stocked_app.state.engine.connect() as connection:
        for table in inspect(connection).get_table_names():
            for row in connection.execute(text(f'SELECT * FROM "{table}"')):
                assert GOOD not in str(tuple(row)), table


def test_orders_are_private(shopper, other_shopper):
    add(shopper, "L1", 1)
    order = buy(shopper).json()
    assert other_shopper.get("/api/orders").json() == []
    assert other_shopper.get(f"/api/orders/{order['id']}").status_code == 404


def test_checkout_requires_login(stocked_app):
    from fastapi.testclient import TestClient

    assert buy(TestClient(stocked_app)).status_code == 401


def test_orders_are_listed_newest_first(shopper, stocked_app):
    add(shopper, "L1", 1)
    first = buy(shopper).json()
    add(shopper, "L1", 1)
    second = buy(shopper).json()
    listed = shopper.get("/api/orders").json()
    assert [o["id"] for o in listed] == [second["id"], first["id"]]
    with transaction(stocked_app.state.sessions) as session:
        assert session.query(Order).count() == 2


def set_price(app, sku, cents):
    with transaction(app.state.sessions) as session:
        session.scalar(select(Product).where(Product.sku == sku)).price_cents = cents


def test_a_total_that_changed_since_it_was_shown_is_not_charged(shopper, stocked_app):
    add(shopper, "L1", 2)
    shown = shopper.get("/api/cart").json()["total_cents"]
    set_price(stocked_app, "L1", 4500)
    reply = shopper.post("/api/checkout", json={"card_number": GOOD, "expected_total_cents": shown})
    assert reply.status_code == 409
    assert reply.json()["detail"] == (
        "The cart total is now $90.00, not the $60.00 shown. Check it and pay again."
    )
    assert stock(stocked_app, "L1") == 5 and shopper.get("/api/orders").json() == []
    assert shopper.get("/api/cart").json()["total_cents"] == 9000
    reply = shopper.post("/api/checkout", json={"card_number": GOOD, "expected_total_cents": 9000})
    assert reply.status_code == 201 and reply.json()["total"] == 90.0
