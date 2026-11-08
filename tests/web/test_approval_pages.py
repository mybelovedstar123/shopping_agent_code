"""Answering agents and approving purchases through the pages."""

import re
import time

from conftest import post_form, register, text_of
from fastapi.testclient import TestClient
from sqlalchemy import select

from kyo.store import payments
from kyo.store.db import transaction
from kyo.store.models import Order, Product

GOOD = "4242 4242 4242 4242"


def in_stock_mug(app):
    """A mug the shopper can ask for precisely, with the only match for its description."""
    with transaction(app.state.sessions) as session:
        rows = session.scalars(select(Product).where(Product.sku.like("MUG-%"))).all()
        product = next(p for p in rows if p.stock >= 2)
        for other in rows:
            if other.id != product.id:
                other.stock = 0
        product.attributes = {**product.attributes, "colour": "red"}
        return product.sku, product.title, product.price_cents, product.stock


def orders(app):
    with transaction(app.state.sessions) as session:
        return session.query(Order).count()


def start(client, request):
    reply = post_form(client, "/assistant", {"request": request})
    return re.search(r"/assistant/(\d+)$", str(reply.url)).group(1)


def wait_for(client, run_id, text, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        page = client.get(f"/assistant/{run_id}")
        if text in page.text:
            return page
        time.sleep(0.03)
    raise AssertionError(f"never saw {text!r}: {text_of(page)[-600:]}")


def action(page, run_id, kind):
    match = re.search(rf'action="(/assistant/{run_id}/questions/\d+/{kind})"', page.text)
    assert match, text_of(page)[-400:]
    return match.group(1)


BUY = "Buy a red mug. Do not ask me anything."


def test_approval_shows_the_exact_cart_and_total_before_anything_is_bought(client, app):
    sku, title, price, stock = in_stock_mug(app)
    register(client)
    run_id = start(client, BUY)
    page = wait_for(client, run_id, 'name="card_number"')
    text = text_of(page)
    assert title in text and f"${price / 100:.2f}" in text and "Approve" in text
    assert orders(app) == 0
    with transaction(app.state.sessions) as session:
        assert session.scalar(select(Product.stock).where(Product.sku == sku)) == stock


def test_approving_places_the_order_and_links_to_it(client, app):
    sku, title, price, stock = in_stock_mug(app)
    register(client)
    run_id = start(client, BUY)
    page = wait_for(client, run_id, 'name="card_number"')
    data = {"approved": "yes", "card_number": GOOD}
    post_form(client, action(page, run_id, "decision"), data)
    page = wait_for(client, run_id, 'data-status="purchased"')
    assert "Your order is placed" in page.text and 'href="/orders/1"' in page.text
    order = client.get("/orders/1")
    assert title in order.text and "Bought by your assistant" in order.text
    assert "Assistant" in client.get("/orders").text
    with transaction(app.state.sessions) as session:
        assert session.scalar(select(Product.stock).where(Product.sku == sku)) == stock - 1


def test_saying_no_buys_nothing(client, app):
    in_stock_mug(app)
    register(client)
    run_id = start(client, BUY)
    page = wait_for(client, run_id, 'name="card_number"')
    post_form(client, action(page, run_id, "decision"), {"approved": "no"})
    page = wait_for(client, run_id, 'data-status="declined"')
    assert "did not approve" in page.text and orders(app) == 0


def test_declined_card_is_explained_and_can_be_tried_again(client, app):
    in_stock_mug(app)
    register(client)
    run_id = start(client, BUY)
    page = wait_for(client, run_id, 'name="card_number"')
    url = action(page, run_id, "decision")
    bad = {"approved": "yes", "card_number": payments.DECLINED_TEST_CARD}
    reply = post_form(client, url, bad)
    assert reply.status_code == 402 and "The card was declined" in reply.text
    assert 'name="card_number"' in reply.text and orders(app) == 0
    assert payments.DECLINED_TEST_CARD not in reply.text
    post_form(client, url, {"approved": "yes", "card_number": GOOD})
    wait_for(client, run_id, 'data-status="purchased"')
    assert orders(app) == 1


def test_questions_can_be_answered_on_the_page(client, app):
    in_stock_mug(app)
    register(client)
    run_id = start(client, "Buy a mug. Ask me at most 1 question.")
    page = wait_for(client, run_id, 'name="answer"')
    assert "What " in text_of(page) and 'data-status="waiting"' in page.text
    post_form(client, action(page, run_id, "answer"), {"answer": "I don't mind."})
    page = wait_for(client, run_id, 'name="card_number"')
    assert "You said: I don't mind." in text_of(page)
    post_form(client, action(page, run_id, "decision"), {"approved": "no"})
    wait_for(client, run_id, 'data-status="declined"')


def test_blank_answer_is_explained(client, app):
    in_stock_mug(app)
    register(client)
    run_id = start(client, "Buy a mug. Ask me at most 1 question.")
    page = wait_for(client, run_id, 'name="answer"')
    url = action(page, run_id, "answer")
    reply = post_form(client, url, {"answer": "  "})
    assert reply.status_code == 422 and "Type an answer" in reply.text
    post_form(client, url, {"answer": "I don't mind."})
    page = wait_for(client, run_id, 'name="card_number"')
    post_form(client, action(page, run_id, "decision"), {"approved": "no"})


def test_another_shopper_cannot_answer_or_approve(client, app):
    in_stock_mug(app)
    register(client)
    run_id = start(client, BUY)
    page = wait_for(client, run_id, 'name="card_number"')
    url = action(page, run_id, "decision")
    other = TestClient(app, follow_redirects=True)
    register(other, email="other@example.com")
    reply = post_form(other, url, {"approved": "yes", "card_number": GOOD})
    assert reply.status_code == 404 and orders(app) == 0
    post_form(client, url, {"approved": "no"})


def test_approving_twice_buys_once(client, app):
    in_stock_mug(app)
    register(client)
    run_id = start(client, BUY)
    page = wait_for(client, run_id, 'name="card_number"')
    url = action(page, run_id, "decision")
    post_form(client, url, {"approved": "yes", "card_number": GOOD})
    second = post_form(client, url, {"approved": "yes", "card_number": GOOD})
    assert second.status_code == 409 and "already been answered" in second.text
    wait_for(client, run_id, 'data-status="purchased"')
    assert orders(app) == 1


def test_finished_run_shows_no_forms(client, app):
    in_stock_mug(app)
    register(client)
    run_id = start(client, BUY)
    page = wait_for(client, run_id, 'name="card_number"')
    post_form(client, action(page, run_id, "decision"), {"approved": "no"})
    page = wait_for(client, run_id, 'data-status="declined"')
    assert 'name="card_number"' not in page.text
