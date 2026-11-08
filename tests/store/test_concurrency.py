"""Checkout must stay correct when requests race or are repeated."""

import threading
import time

import pytest
from sqlalchemy import select

from kyo.store import cart, checkout, payments
from kyo.store.accounts import register
from kyo.store.db import transaction
from kyo.store.models import Category, Order, Product

CARD = payments.APPROVED_TEST_CARD


@pytest.fixture
def store(sessions):
    with transaction(sessions) as session:
        session.add(
            Product(
                sku="M1",
                title="Mug",
                category=Category(name="Mugs", slug="mugs"),
                price_cents=500,
                stock=3,
            )
        )
    return sessions


def shoppers(sessions, count):
    ids = []
    with transaction(sessions) as session:
        for n in range(count):
            ids.append(register(session, f"s{n}@example.com", "S", "a long password").id)
    return ids


def fill_cart(sessions, user_id, quantity=1):
    with transaction(sessions) as session:
        cart.set_line(session, user_id, "M1", quantity)


def race(tasks):
    """Run tasks at the same moment. Returns their results or the exceptions they raised."""
    start = threading.Barrier(len(tasks))
    results = [None] * len(tasks)

    def work(index, task):
        start.wait()
        try:
            results[index] = task()
        except Exception as error:  # the test inspects what each task did
            results[index] = error

    threads = [threading.Thread(target=work, args=(i, t)) for i, t in enumerate(tasks)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    return results


def stock(sessions):
    with transaction(sessions) as session:
        return session.scalar(select(Product.stock).where(Product.sku == "M1"))


def order_count(sessions):
    with transaction(sessions) as session:
        return session.query(Order).count()


def buy(sessions, user_id, key=None):
    def task():
        with transaction(sessions) as session:
            return checkout.place_order(session, user_id, CARD, key).id

    return task


def test_concurrent_checkouts_cannot_oversell(store):
    users = shoppers(store, 8)
    for user_id in users:
        fill_cart(store, user_id)
    results = race([buy(store, u) for u in users])
    won = [r for r in results if isinstance(r, int)]
    lost = [r for r in results if isinstance(r, checkout.CheckoutError)]
    assert len(won) == 3 and len(lost) == 5, results
    assert stock(store) == 0 and order_count(store) == 3


def test_losers_of_a_race_keep_their_carts(store):
    users = shoppers(store, 5)
    for user_id in users:
        fill_cart(store, user_id)
    results = race([buy(store, u) for u in users])
    with transaction(store) as session:
        for user_id, result in zip(users, results, strict=True):
            lines = cart.view(session, user_id)["lines"]
            assert (lines == []) == isinstance(result, int), result


def test_concurrent_repeats_of_one_checkout_make_one_order(store):
    user_id = shoppers(store, 1)[0]
    fill_cart(store, user_id)
    results = race([buy(store, user_id, "same-key") for _ in range(6)])
    assert all(isinstance(r, int) for r in results), results
    assert len(set(results)) == 1
    assert order_count(store) == 1 and stock(store) == 2


def test_concurrent_checkouts_with_different_keys_buy_one_cart_once(store):
    """Two tabs with one cart: the second finds it empty, and nothing is bought twice."""
    user_id = shoppers(store, 1)[0]
    fill_cart(store, user_id)
    results = race([buy(store, user_id, f"key-{n}") for n in range(4)])
    assert order_count(store) == 1 and stock(store) == 2, results


def test_stock_never_goes_negative_with_larger_quantities(store):
    users = shoppers(store, 4)
    for user_id in users:
        fill_cart(store, user_id, 2)
    race([buy(store, u) for u in users])
    assert stock(store) == 1 and order_count(store) == 1


@pytest.fixture
def slow_cart_read(monkeypatch):
    """Hold the dangerous window open: every checkout pauses just after reading its cart."""
    real = checkout._lines

    def slow(*args, **kwargs):
        found = real(*args, **kwargs)
        time.sleep(0.2)
        return found

    monkeypatch.setattr(checkout, "_lines", slow)


def test_two_tabs_cannot_buy_one_cart_twice_even_in_the_worst_timing(store, slow_cart_read):
    user_id = shoppers(store, 1)[0]
    fill_cart(store, user_id)
    results = race([buy(store, user_id, f"tab-{n}") for n in range(2)])
    assert order_count(store) == 1 and stock(store) == 2, results
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], checkout.CheckoutError), results


def test_repeats_of_one_checkout_get_one_order_even_in_the_worst_timing(store, slow_cart_read):
    user_id = shoppers(store, 1)[0]
    fill_cart(store, user_id)
    results = race([buy(store, user_id, "same-key") for _ in range(2)])
    assert all(isinstance(r, int) for r in results) and len(set(results)) == 1, results
    assert order_count(store) == 1 and stock(store) == 2
