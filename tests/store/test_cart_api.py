import pytest
from fastapi.testclient import TestClient

from kyo.store import cart
from kyo.store.db import transaction
from kyo.store.models import AgentRun, User


def put(client, sku, quantity):
    return client.put(f"/api/cart/{sku}", json={"quantity": quantity})


def test_cart_starts_empty(shopper):
    assert shopper.get("/api/cart").json() == {"lines": [], "total": 0.0, "total_cents": 0}


def test_add_and_total(shopper):
    put(shopper, "L1", 2)
    reply = put(shopper, "L2", 1).json()
    assert reply["total"] == 70.5 and reply["total_cents"] == 7050
    assert [(x["id"], x["quantity"], x["subtotal"]) for x in reply["lines"]] == [
        ("L1", 2, 60.0),
        ("L2", 1, 10.5),
    ]


def test_setting_a_line_replaces_its_quantity(shopper):
    put(shopper, "L1", 2)
    assert put(shopper, "L1", 3).json()["lines"][0]["quantity"] == 3


def test_a_change_that_could_not_be_saved_is_not_reported_as_done(
    shopper, stocked_app, monkeypatch
):
    from sqlalchemy.orm import Session

    def fail(self):
        raise RuntimeError("the database refused the commit")

    client = TestClient(stocked_app, raise_server_exceptions=False, cookies=shopper.cookies)
    with monkeypatch.context() as patched:
        patched.setattr(Session, "commit", fail)
        reply = put(client, "L1", 2)
    assert reply.status_code == 500
    assert shopper.get("/api/cart").json()["lines"] == []


def test_zero_or_delete_removes_a_line(shopper):
    put(shopper, "L1", 2)
    assert put(shopper, "L1", 0).json()["lines"] == []
    put(shopper, "L1", 2)
    assert shopper.delete("/api/cart/L1").json()["lines"] == []


def test_removing_a_line_not_in_the_cart_is_harmless(shopper):
    assert shopper.delete("/api/cart/L1").status_code == 200


@pytest.mark.parametrize(
    "sku, quantity, message",
    [
        ("NOPE", 1, "No product NOPE"),
        ("L1", 6, "Only 5 of L1 in stock"),
        ("M1", 1, "Only 0 of M1 in stock"),
        ("L1", -1, "between 0 and 99"),
        ("L1", 100, "between 0 and 99"),
    ],
)
def test_refused_changes(shopper, sku, quantity, message):
    reply = put(shopper, sku, quantity)
    assert reply.status_code == 422 and message in reply.json()["detail"]
    assert shopper.get("/api/cart").json()["lines"] == []


def test_non_integer_quantity_is_rejected(shopper):
    assert put(shopper, "L1", "lots").status_code == 422


def test_cart_requires_login(stocked_app):
    client = TestClient(stocked_app)
    assert client.get("/api/cart").status_code == 401
    assert put(client, "L1", 1).status_code == 401
    assert client.delete("/api/cart/L1").status_code == 401


def test_carts_are_private(shopper, other_shopper):
    put(shopper, "L1", 1)
    assert other_shopper.get("/api/cart").json()["lines"] == []
    put(other_shopper, "L1", 3)
    assert shopper.get("/api/cart").json()["lines"][0]["quantity"] == 1
    other_shopper.delete("/api/cart/L1")
    assert shopper.get("/api/cart").json()["lines"][0]["quantity"] == 1


def test_a_run_cart_is_separate_from_the_shopper_cart(shopper, stocked_app):
    put(shopper, "L1", 1)
    with transaction(stocked_app.state.sessions) as session:
        user = session.query(User).first()
        run = AgentRun(user_id=user.id, request="Buy a lamp")
        session.add(run)
        session.flush()
        cart.set_line(session, user.id, "L2", 1, run.id)
        assert [x["id"] for x in cart.view(session, user.id, run.id)["lines"]] == ["L2"]
        assert [x["id"] for x in cart.view(session, user.id)["lines"]] == ["L1"]
        cart.clear(session, user.id, run.id)
        assert cart.view(session, user.id, run.id)["lines"] == []
        assert len(cart.view(session, user.id)["lines"]) == 1
