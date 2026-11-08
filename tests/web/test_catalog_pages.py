import re

from webutil import text_of


def test_home_lists_every_category(client):
    reply = client.get("/")
    assert reply.status_code == 200 and "text/html" in reply.headers["content-type"]
    for name in ("Desk Lamps", "Mugs", "Office Chairs", "Notebooks"):
        assert name in reply.text
    assert 'href="/category/desk-lamps"' in reply.text


def test_pages_are_usable_on_a_phone(client):
    assert 'name="viewport" content="width=device-width' in client.get("/").text


def test_pages_say_payments_are_simulated(client):
    assert "Payments are simulated" in text_of(client.get("/"))


def test_stylesheet_is_served(client):
    reply = client.get("/static/site.css")
    assert reply.status_code == 200 and "text/css" in reply.headers["content-type"]


def test_category_page_lists_products_with_prices(client):
    reply = client.get("/category/mugs")
    assert reply.status_code == 200
    assert "16 products" in text_of(reply)
    assert re.search(r'href="/product/MUG-\d{4}"', reply.text)
    assert re.search(r"\$\d+\.\d\d", reply.text)


def test_category_page_marks_products_out_of_stock(client):
    pages = "".join(client.get("/category/mugs", params={"page": p}).text for p in (1, 2))
    assert "Out of stock" in pages


def test_category_page_pages_through_products(client):
    first = client.get("/category/mugs")
    assert 'href="?page=2"' in first.text and "Previous" not in first.text
    second = client.get("/category/mugs", params={"page": 2})
    assert 'href="?page=1"' in second.text and "Next" not in text_of(second)
    assert first.text != second.text


def test_unknown_category_gives_a_helpful_404(client):
    reply = client.get("/category/nope")
    assert reply.status_code == 404 and "could not find that category" in reply.text
