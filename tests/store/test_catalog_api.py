import pytest
from fastapi.testclient import TestClient

from kyo.app import Settings, create_app
from kyo.store.db import transaction
from kyo.store.models import Category, Product


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'api.sqlite3'}"))
    with transaction(app.state.sessions) as session:
        lamps = Category(name="Desk Lamps", slug="desk-lamps")
        mugs = Category(name="Mugs", slug="mugs")
        session.add_all(
            [
                Product(
                    sku="L1",
                    title="Arc Desk Lamp in red",
                    description="A lamp",
                    category=lamps,
                    price_cents=3000,
                    stock=5,
                    attributes={"colour": "red", "power": "8 W"},
                ),
                Product(
                    sku="L2",
                    title="Slim Desk Lamp in blue",
                    description="A lamp with a dimmer",
                    category=lamps,
                    price_cents=1000,
                    stock=0,
                    attributes={"colour": "blue", "power": "8 W"},
                ),
                Product(
                    sku="L3",
                    title="Halo Desk Lamp in red",
                    category=lamps,
                    price_cents=2000,
                    stock=1,
                    attributes={"colour": "red", "power": "12 W"},
                ),
                Product(
                    sku="M1",
                    title="Harbor Mug",
                    description="Holds red tea",
                    category=mugs,
                    price_cents=500,
                    stock=9,
                    attributes={"colour": "white"},
                ),
            ]
        )
    with TestClient(app) as test_client:
        yield test_client
    app.state.engine.dispose()


def ids(reply):
    assert reply.status_code == 200, reply.text
    return [row["id"] for row in reply.json()["results"]]


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_lists_categories(client):
    assert [c["slug"] for c in client.get("/api/categories").json()] == ["desk-lamps", "mugs"]


def test_search_by_word_matches_title_category_and_attributes(client):
    assert ids(client.get("/api/products", params={"q": "arc"})) == ["L1"]
    assert set(ids(client.get("/api/products", params={"q": "lamps"}))) == {"L1", "L2", "L3"}
    assert ids(client.get("/api/products", params={"q": "dimmer"})) == ["L2"]


def test_search_ranks_title_matches_above_description_matches(client):
    assert ids(client.get("/api/products", params={"q": "red"}))[-1] == "M1"


def test_all_words_must_match(client):
    assert ids(client.get("/api/products", params={"q": "arc blue"})) == []


def test_filter_by_category_name_or_slug(client):
    assert ids(client.get("/api/products", params={"category": "mugs"})) == ["M1"]
    assert ids(client.get("/api/products", params={"category": "Mugs"})) == ["M1"]


def test_filter_by_price_includes_the_edges(client):
    reply = client.get("/api/products", params={"min_price": 10, "max_price": 20})
    assert set(ids(reply)) == {"L2", "L3"}


def test_filter_by_attribute(client):
    reply = client.get("/api/products", params={"colour": "RED", "power": "12 W"})
    assert ids(reply) == ["L3"]


def test_filter_in_stock(client):
    assert "L2" not in ids(client.get("/api/products", params={"in_stock": True}))


@pytest.mark.parametrize(
    "sort, expected",
    [
        ("price_asc", ["M1", "L2", "L3", "L1"]),
        ("price_desc", ["L1", "L3", "L2", "M1"]),
        ("title", ["L1", "L3", "M1", "L2"]),
    ],
)
def test_sorting(client, sort, expected):
    assert ids(client.get("/api/products", params={"sort": sort})) == expected


def test_paging(client):
    first = client.get("/api/products", params={"page_size": 3, "sort": "title"}).json()
    assert first["total"] == 4 and first["next_page"] == 2 and len(first["results"]) == 3
    second = client.get("/api/products", params={"page_size": 3, "page": 2, "sort": "title"}).json()
    assert [r["id"] for r in second["results"]] == ["L2"] and second["next_page"] is None


@pytest.mark.parametrize(
    "params", [{"sort": "nonsense"}, {"page": 0}, {"page_size": 999}, {"max_price": -1}]
)
def test_invalid_parameters_are_rejected(client, params):
    assert client.get("/api/products", params=params).status_code == 422


def test_rows_leave_attributes_to_the_product_page(client):
    row = client.get("/api/products", params={"q": "arc"}).json()["results"][0]
    assert row == {
        "id": "L1",
        "title": "Arc Desk Lamp in red",
        "category": "Desk Lamps",
        "price": 30.0,
        "in_stock": True,
    }


def test_product_page_shows_everything(client):
    page = client.get("/api/products/L1").json()
    assert page["attributes"] == {"colour": "red", "power": "8 W"}
    assert page["stock"] == 5 and page["description"] == "A lamp"


def test_unknown_product_is_not_found(client):
    assert client.get("/api/products/NOPE").status_code == 404


def test_facets_count_attribute_values(client):
    reply = client.get("/api/categories/desk-lamps/facets").json()
    assert reply["colour"] == [{"value": "blue", "count": 1}, {"value": "red", "count": 2}]


def test_facets_of_an_unknown_category_are_not_found(client):
    assert client.get("/api/categories/nope/facets").status_code == 404
