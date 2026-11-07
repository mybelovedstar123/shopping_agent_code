import pytest
from fastapi.testclient import TestClient

from kyo.app import Settings, create_app
from kyo.store.db import make_engine, make_sessions, transaction
from kyo.store.models import Base, Category, Product


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def sessions(engine):
    return make_sessions(engine)


@pytest.fixture
def stocked_app(tmp_path):
    """An application with two lamps and a mug in stock."""
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'app.sqlite3'}"))
    with transaction(app.state.sessions) as session:
        lamps = Category(name="Desk Lamps", slug="desk-lamps")
        mugs = Category(name="Mugs", slug="mugs")
        session.add_all(
            [
                Product(
                    sku="L1",
                    title="Arc Desk Lamp",
                    category=lamps,
                    price_cents=3000,
                    stock=5,
                    attributes={"colour": "red"},
                ),
                Product(
                    sku="L2",
                    title="Slim Desk Lamp",
                    category=lamps,
                    price_cents=1050,
                    stock=1,
                    attributes={"colour": "blue"},
                ),
                Product(
                    sku="M1",
                    title="Harbor Mug",
                    category=mugs,
                    price_cents=500,
                    stock=0,
                    attributes={"colour": "white"},
                ),
            ]
        )
    yield app
    app.state.engine.dispose()


def _user(app, email):
    client = TestClient(app)
    reply = client.post(
        "/api/register", json={"email": email, "name": "Shopper", "password": "a long password"}
    )
    assert reply.status_code == 201
    return client


@pytest.fixture
def shopper(stocked_app):
    return _user(stocked_app, "shopper@example.com")


@pytest.fixture
def other_shopper(stocked_app):
    return _user(stocked_app, "other@example.com")
