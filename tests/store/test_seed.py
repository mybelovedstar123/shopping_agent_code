from kyo.store.db import transaction
from kyo.store.models import Category, Product
from kyo.store.seed import seed_catalog, slugify


def snapshot(sessions):
    with transaction(sessions) as session:
        return {p.sku: (p.title, p.price_cents, p.attributes) for p in session.query(Product)}


def test_seeds_a_varied_catalog(sessions):
    assert seed_catalog(sessions) >= 120
    with transaction(sessions) as session:
        assert session.query(Category).count() >= 8
        products = session.query(Product).all()
        assert all(p.price_cents > 0 and p.attributes and p.description for p in products)
        assert len({p.title for p in products}) > 40
        assert any(p.stock == 0 for p in products), "some products are out of stock"
        assert len({p.category_id for p in products}) == session.query(Category).count()


def test_seed_is_repeatable_and_safe_to_run_twice(sessions):
    seed_catalog(sessions)
    before = snapshot(sessions)
    assert seed_catalog(sessions) == 0
    assert snapshot(sessions) == before


def test_seed_does_not_overwrite_changes(sessions):
    seed_catalog(sessions)
    with transaction(sessions) as session:
        session.query(Product).filter_by(sku="LMP-0001").one().stock = 999
    seed_catalog(sessions)
    with transaction(sessions) as session:
        assert session.query(Product).filter_by(sku="LMP-0001").one().stock == 999


def test_seed_is_the_same_in_every_database(sessions, tmp_path):
    from kyo.store.db import make_engine, make_sessions
    from kyo.store.models import Base

    other_engine = make_engine(f"sqlite:///{tmp_path / 'other.sqlite3'}")
    Base.metadata.create_all(other_engine)
    other = make_sessions(other_engine)
    seed_catalog(sessions)
    seed_catalog(other)
    assert snapshot(sessions) == snapshot(other)
    other_engine.dispose()


def test_slugify():
    assert slugify("Office Chairs") == "office-chairs"
