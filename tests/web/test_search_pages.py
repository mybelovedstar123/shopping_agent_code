import re

from sqlalchemy import select
from webutil import text_of

from kyo.store.db import transaction
from kyo.store.models import Product


def a_product(app, prefix="MUG"):
    with transaction(app.state.sessions) as session:
        rows = session.scalars(select(Product).where(Product.sku.like(f"{prefix}-%"))).all()
        return next(p for p in rows if p.stock > 0)


def skus(reply):
    return re.findall(r'href="/product/([A-Z]+-\d+)"', reply.text)


def prices(reply):
    return [float(x) for x in re.findall(r'class="price">\$([\d.]+)<', reply.text)]


def test_search_finds_products_by_word(client):
    reply = client.get("/search", params={"q": "mug"})
    assert reply.status_code == 200 and "16 products" in text_of(reply)
    assert all(sku.startswith("MUG") for sku in skus(reply))


def test_search_with_no_matches_says_so(client):
    assert "No products match" in client.get("/search", params={"q": "zzzz"}).text


def test_search_can_filter_by_category(client):
    reply = client.get("/search", params={"category": "kettles"})
    assert all(sku.startswith("KET") for sku in skus(reply)) and skus(reply)
    assert 'value="kettles" selected' in reply.text


def test_search_can_filter_by_price(client):
    reply = client.get("/search", params={"category": "mugs", "max_price": 15})
    assert prices(reply) and all(p <= 15 for p in prices(reply))


def test_empty_filter_fields_are_ignored(client):
    reply = client.get(
        "/search",
        params={"q": "mug", "category": "", "min_price": "", "max_price": "", "colour": ""},
    )
    assert reply.status_code == 200 and "16 products" in text_of(reply)


def test_invalid_price_is_ignored_rather_than_an_error(client):
    assert client.get("/search", params={"q": "mug", "max_price": "cheap"}).status_code == 200


def test_category_shows_attribute_filters_that_work(client, app):
    product = a_product(app)
    colour = product.attributes["colour"]
    reply = client.get("/search", params={"category": "mugs", "colour": colour})
    assert 'name="colour"' in reply.text and f'value="{colour}" selected' in reply.text
    with transaction(app.state.sessions) as session:
        for sku in skus(reply):
            found = session.scalar(select(Product).where(Product.sku == sku))
            assert found.attributes["colour"] == colour


def test_search_can_sort_by_price(client):
    params = {"category": "mugs", "sort": "price_asc"}
    ascending = prices(client.get("/search", params=params))
    assert ascending == sorted(ascending) and ascending
    params["sort"] = "price_desc"
    descending = prices(client.get("/search", params=params))
    assert descending == sorted(descending, reverse=True)


def test_unknown_sort_falls_back_rather_than_failing(client):
    assert client.get("/search", params={"q": "mug", "sort": "nonsense"}).status_code == 200


def test_in_stock_filter_hides_sold_out_products(client):
    params = {"category": "mugs", "in_stock": "true", "page_size": 60}
    assert "Out of stock" not in client.get("/search", params=params).text


def test_paging_keeps_the_filters(client):
    reply = client.get("/search", params={"category": "mugs", "sort": "price_asc"})
    link = re.search(r'href="\?([^"]*page=2)"', reply.text).group(1).replace("&amp;", "&")
    assert "category=mugs" in link and "sort=price_asc" in link
    assert prices(client.get(f"/search?{link}"))[0] >= prices(reply)[-1]


def test_page_text_is_escaped(client):
    reply = client.get("/search", params={"q": "<script>alert(1)</script>"})
    assert "<script>alert(1)</script>" not in reply.text
    assert "&lt;script&gt;" in reply.text


def test_product_page_shows_attributes_price_and_stock(client, app):
    product = a_product(app)
    reply = client.get(f"/product/{product.sku}")
    text = text_of(reply)
    assert reply.status_code == 200 and product.title in text
    assert f"${product.price_cents / 100:.2f}" in text and f"{product.stock} in stock" in text
    for value in product.attributes.values():
        assert value in text


def test_product_page_marks_out_of_stock(client, app):
    with transaction(app.state.sessions) as session:
        session.scalar(select(Product).where(Product.sku == "MUG-0001")).stock = 0
    assert "Out of stock" in client.get("/product/MUG-0001").text


def test_unknown_product_gives_a_helpful_404(client):
    reply = client.get("/product/NOPE")
    assert reply.status_code == 404 and "could not find that product" in reply.text


def test_heading_says_what_is_being_shown(client):
    assert "<h1>Mugs</h1>" in client.get("/search", params={"category": "mugs"}).text
    assert "<h1>All products</h1>" in client.get("/search").text
    assert "Results for" in client.get("/search", params={"q": "mug"}).text


def test_favicon_is_served(client):
    assert 'href="/static/favicon.svg"' in client.get("/").text
    assert client.get("/static/favicon.svg").status_code == 200
