"""The agent team shopping in the real store, against a real database."""

import pytest

from kyo.store import checkout, payments
from kyo.store.accounts import register
from kyo.store.db import transaction
from kyo.store.models import AgentRun, Order, Product
from kyo.store.seed import seed_catalog
from kyo.store.tools import StoreTools
from kyo.team import run_team


class FakeShopper:
    """Answers questions from a table, and approves through the real checkout service."""

    def __init__(self, sessions, user_id, run_id, approves=True, answers=None):
        self.sessions, self.user_id, self.run_id = sessions, user_id, run_id
        self.approves = approves
        self.answers = answers or {}
        self.asked = []
        self.approvals = []

    def ask(self, question):
        self.asked.append(question)
        return next((a for k, a in self.answers.items() if k in question), "I don't mind.")

    def approve(self, summary):
        self.approvals.append(summary)
        if not self.approves:
            return {"approved": False}
        with transaction(self.sessions) as session:
            order = checkout.place_order(
                session,
                self.user_id,
                payments.APPROVED_TEST_CARD,
                run_id=self.run_id,
                expected_total_cents=summary["total_cents"],
            )
            return {"approved": True, "order_id": order.id}


@pytest.fixture
def setup(sessions):
    seed_catalog(sessions)
    with transaction(sessions) as session:
        user = register(session, "shopper@example.com", "Shopper", "a long password")
        run = AgentRun(user_id=user.id, request="test")
        session.add(run)
        session.flush()
        return sessions, user.id, run.id


def make(setup, **options):
    sessions, user_id, run_id = setup
    shopper = FakeShopper(sessions, user_id, run_id, **options)
    return StoreTools(sessions, user_id, run_id, shopper), shopper


def test_every_tool_has_a_valid_schema_and_roles_can_be_guessed(setup):
    from kyo.agents.request import guess_roles

    tools, _ = make(setup)
    assert guess_roles(tools.tools()) == {
        "search": "search_products",
        "open": "get_product",
        "compare": "compare_products",
        "cart": "update_cart",
        "cart_view": "view_cart",
        "buy": "checkout",
        "refuse": "decline_purchase",
        "ask": "ask_shopper",
    }


def test_search_rows_hide_attributes_until_the_page_is_read(setup):
    tools, _ = make(setup)
    rows = tools.call("search_products", {"category": "Mugs"})["observation"]["results"]
    assert "attributes" not in rows[0]
    page = tools.call("get_product", {"product_id": rows[0]["id"]})["observation"]
    assert page["attributes"]


def test_compare_shows_several_products(setup):
    tools, _ = make(setup)
    reply = tools.call("compare_products", {"product_ids": ["MUG-0001", "MUG-0002", "NOPE"]})
    assert [p["id"] for p in reply["observation"]["products"]] == ["MUG-0001", "MUG-0002"]


def test_unknown_product_and_bad_cart_changes_are_reported_not_raised(setup):
    tools, _ = make(setup)
    assert "error" in tools.call("get_product", {"product_id": "NOPE"})["observation"]
    reply = tools.call("update_cart", {"product_id": "NOPE", "quantity": 1})
    assert "error" in reply["observation"]


def test_checkout_of_an_empty_cart_is_refused_without_asking(setup):
    tools, shopper = make(setup)
    assert "error" in tools.call("checkout", {})["observation"]
    assert shopper.approvals == []


def test_declined_approval_ends_without_an_order(setup):
    tools, shopper = make(setup, approves=False)
    tools.call("update_cart", {"product_id": _in_stock(setup, "MUG"), "quantity": 1})
    reply = tools.call("checkout", {})
    assert reply["finished"] and reply["observation"]["accepted"] is False
    assert _orders(setup) == 0


def _in_stock(setup, prefix):
    sessions, _, _ = setup
    with transaction(sessions) as session:
        rows = session.query(Product).filter(Product.sku.like(f"{prefix}-%"), Product.stock > 2)
        return rows.first().sku


def _orders(setup):
    with transaction(setup[0]) as session:
        return session.query(Order).count()


def _product(setup, sku):
    with transaction(setup[0]) as session:
        product = session.query(Product).filter_by(sku=sku).one()
        return product.title, dict(product.attributes), product.price_cents, product.stock


def test_team_buys_in_the_real_store_only_after_approval(setup):
    sku = _in_stock(setup, "MUG")
    _, attributes, price, stock = _product(setup, sku)
    tools, shopper = make(setup)
    colour, capacity = attributes["colour"], attributes["capacity"]
    request = f"Buy a {colour} mug with a capacity of {capacity}. Do not ask me anything."
    result = run_team(request, tools, max_steps=40)
    assert result["outcome"] == "purchased", result["reason"]
    assert len(shopper.approvals) == 1
    with transaction(setup[0]) as session:
        order = session.query(Order).one()
        assert order.run_id == setup[2] and order.total_cents == shopper.approvals[0]["total_cents"]
        bought = order.lines[0].sku
        assert session.query(Product).filter_by(sku=bought).one().attributes["colour"] == colour


def test_team_does_not_buy_when_the_shopper_refuses(setup):
    sku = _in_stock(setup, "MUG")
    colour = _product(setup, sku)[1]["colour"]
    tools, shopper = make(setup, approves=False)
    result = run_team(f"Buy a {colour} mug. Do not ask me anything.", tools, max_steps=40)
    assert result["outcome"] == "declined" and len(shopper.approvals) == 1
    assert _orders(setup) == 0


def test_team_declines_what_the_store_does_not_have(setup):
    tools, shopper = make(setup)
    result = run_team("Buy a purple mug. Do not ask me anything.", tools, max_steps=40)
    assert result["outcome"] == "declined" and shopper.approvals == [] and _orders(setup) == 0


def test_team_asks_the_shopper_through_the_tool(setup):
    sku = _in_stock(setup, "MUG")
    colour = _product(setup, sku)[1]["colour"]
    tools, shopper = make(setup)
    run_team(f"Buy a {colour} mug. Ask me at most 1 question.", tools, max_steps=40)
    assert len(shopper.asked) == 1 and shopper.asked[0].startswith("What ")


def test_failed_approval_is_reported_and_does_not_end_the_run(setup):
    tools, shopper = make(setup)
    shopper.approve = lambda summary: {
        "approved": False,
        "failed": "Mug no longer has enough stock.",
    }
    tools.call("update_cart", {"product_id": _in_stock(setup, "MUG"), "quantity": 1})
    reply = tools.call("checkout", {})
    assert not reply.get("finished") and "enough stock" in reply["observation"]["note"]
