import pytest
from sqlalchemy.exc import IntegrityError

from kyo.store.db import transaction
from kyo.store.models import AgentRun, CartLine, Category, Product, User


def base_rows(session):
    user = User(email="a@example.com", name="A", password_hash="x")
    category = Category(name="Lamps", slug="lamps")
    product = Product(sku="L1", title="Lamp", category=category, price_cents=100, stock=1)
    session.add_all([user, category, product])
    session.flush()
    return user, product


@pytest.mark.parametrize(
    "fields", [{"price_cents": -1, "stock": 1}, {"price_cents": 1, "stock": -1}]
)
def test_products_reject_negative_prices_and_stock(sessions, fields):
    with pytest.raises(IntegrityError), transaction(sessions) as session:
        category = Category(name="Lamps", slug="lamps")
        session.add(Product(sku="L1", title="Lamp", category=category, **fields))


def test_product_attributes_keep_their_structure(sessions):
    with transaction(sessions) as session:
        category = Category(name="Lamps", slug="lamps")
        session.add(
            Product(
                sku="L1",
                title="Lamp",
                category=category,
                price_cents=1,
                attributes={"colour": "red", "sizes": ["S", "M"]},
            )
        )
    with transaction(sessions) as session:
        assert session.query(Product).one().attributes["sizes"] == ["S", "M"]


def test_cart_rejects_zero_quantity(sessions):
    with pytest.raises(IntegrityError), transaction(sessions) as session:
        user, product = base_rows(session)
        session.add(CartLine(user_id=user.id, product_id=product.id, quantity=0))


def test_own_cart_has_one_line_per_product(sessions):
    with pytest.raises(IntegrityError), transaction(sessions) as session:
        user, product = base_rows(session)
        session.add(CartLine(user_id=user.id, product_id=product.id, quantity=1))
        session.flush()
        session.add(CartLine(user_id=user.id, product_id=product.id, quantity=1))


def test_a_run_has_a_separate_cart_from_the_shopper(sessions):
    with transaction(sessions) as session:
        user, product = base_rows(session)
        run = AgentRun(user_id=user.id, request="Buy a lamp")
        session.add(run)
        session.flush()
        session.add(CartLine(user_id=user.id, product_id=product.id, quantity=1))
        session.add(CartLine(user_id=user.id, run_id=run.id, product_id=product.id, quantity=2))
    with transaction(sessions) as session:
        assert session.query(CartLine).count() == 2


def test_foreign_keys_are_enforced(sessions):
    with pytest.raises(IntegrityError), transaction(sessions) as session:
        session.add(CartLine(user_id=999, product_id=999, quantity=1))


def test_a_failed_transaction_changes_nothing(sessions):
    with pytest.raises(RuntimeError), transaction(sessions) as session:
        session.add(User(email="a@example.com", name="A", password_hash="x"))
        session.flush()
        raise RuntimeError("something went wrong")
    with transaction(sessions) as session:
        assert session.query(User).count() == 0
