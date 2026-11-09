"""One whole journey on the real seeded catalog, as a shopper would make it."""

import re
import time

from sqlalchemy import select
from webutil import post_form, register, text_of

from kyo.store.db import transaction
from kyo.store.models import CartLine, Order, Product

CARD = "4242 4242 4242 4242"


def wait_for(client, url, text, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        page = client.get(url)
        if text in page.text:
            return page
        time.sleep(0.03)
    raise AssertionError(f"never saw {text!r}: {text_of(page)[-700:]}")


def buyable_colour(app):
    """A colour that some in-stock mug comes in, read from the seeded catalog."""
    with transaction(app.state.sessions) as session:
        rows = session.scalars(select(Product).where(Product.sku.like("MUG-%"))).all()
        return next(p.attributes["colour"] for p in rows if p.stock >= 2)


def test_register_browse_ask_the_assistant_approve_and_see_the_order(client, app):
    # A new shopper signs up and looks around.
    assert "Log out" in register(client).text
    assert "Mugs" in client.get("/").text
    assert "16 products" in text_of(client.get("/category/mugs"))

    # They put something in their own cart, then hand a request to the assistant.
    with transaction(app.state.sessions) as session:
        own = session.scalars(
            select(Product).where(Product.sku.like("LMP-%"), Product.stock > 0)
        ).first()
        own_sku = own.sku
    post_form(client, f"/cart/{own_sku}", {"quantity": 1})
    colour = buyable_colour(app)
    request = f"Buy 2 {colour} mugs. Ask me at most 2 questions. Get my approval before ordering."
    reply = post_form(client, "/assistant", {"request": request})
    url = re.search(r"/assistant/\d+$", str(reply.url)).group(0)

    # The team asks one question, then asks to approve the cart it chose.
    page = wait_for(client, url, 'name="answer"')
    action = re.search(rf'action="({url}/questions/\d+/answer)"', page.text).group(1)
    post_form(client, action, {"answer": "I don't mind."})
    page = wait_for(client, url, "May I place this order")
    action = re.search(rf'action="({url}/questions/\d+/answer)"', page.text).group(1)
    post_form(client, action, {"answer": "Yes, go ahead."})

    # Checkout still needs explicit approval of the exact cart and total, with payment.
    page = wait_for(client, url, 'name="card_number"')
    text = text_of(page)
    assert "2 x" in text and colour in text and "Approve and pay" in text
    with transaction(app.state.sessions) as session:
        assert session.query(Order).count() == 0, "nothing is bought before approval"
    action = re.search(rf'action="({url}/questions/\d+/decision)"', page.text).group(1)
    post_form(client, action, {"approved": "yes", "card_number": CARD})

    # The order is real, correct, and in the shopper's history.
    page = wait_for(client, url, 'data-status="purchased"')
    order_url = re.search(r'href="(/orders/\d+)"', page.text).group(1)
    order_page = text_of(client.get(order_url))
    assert "Bought by your assistant" in order_page and colour in order_page
    assert "Assistant" in text_of(client.get("/orders"))
    timeline = text_of(client.get(f"{url}/timeline"))
    assert "search_products" in timeline and "checkout" in timeline

    with transaction(app.state.sessions) as session:
        order = session.query(Order).one()
        line = order.lines[0]
        product = session.scalar(select(Product).where(Product.sku == line.sku))
        assert line.quantity == 2 and product.attributes["colour"] == colour
        assert product.sku.startswith("MUG-")
        assert order.total_cents == product.price_cents * 2
        # The shopper's own cart was never touched, and the run's cart is empty.
        lines = session.scalars(select(CartLine)).all()
        assert [(x.product.sku, x.run_id) for x in lines] == [(own_sku, None)]
