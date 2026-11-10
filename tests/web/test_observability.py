import io
import json
import logging
import re
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from webutil import post_form, register, text_of

from kyo.logs import JsonFormatter, log, scrub
from kyo.store import payments
from kyo.store.db import transaction
from kyo.store.models import Product

CARD = "4242424242424242"


@pytest.fixture
def logs():
    """Everything the application logs during a test, as parsed JSON."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    log.addHandler(handler)
    level = log.level
    log.setLevel(logging.INFO)
    yield lambda: [json.loads(line) for line in stream.getvalue().splitlines()]
    log.removeHandler(handler)
    log.setLevel(level)


def only_red_mug(app):
    with transaction(app.state.sessions) as session:
        rows = session.scalars(select(Product).where(Product.sku.like("MUG-%"))).all()
        product = next(p for p in rows if p.stock >= 2)
        for other in rows:
            if other.id != product.id:
                other.stock = 0
        product.attributes = {**product.attributes, "colour": "red"}


def wait_for(client, url, text, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        page = client.get(url)
        if text in page.text:
            return page
        time.sleep(0.03)
    raise AssertionError(f"never saw {text!r}")


def buy(client, app):
    only_red_mug(app)
    register(client)
    reply = post_form(client, "/assistant", {"request": "Buy a red mug. Do not ask me anything."})
    run_id = re.search(r"/assistant/(\d+)$", str(reply.url)).group(1)
    url = f"/assistant/{run_id}"
    page = wait_for(client, url, 'name="card_number"')
    action = re.search(rf'action="({url}/questions/\d+/decision)"', page.text).group(1)
    post_form(client, action, {"approved": "yes", "card_number": CARD})
    wait_for(client, url, 'data-status="purchased"')
    return run_id


@pytest.mark.parametrize(
    "text",
    ["card 4242424242424242 please", "4242 4242 4242 4242", "4242-4242-4242-4242"],
)
def test_scrub_masks_card_numbers(text):
    assert "4242" not in scrub(text)


@pytest.mark.parametrize(
    "text",
    ["4242  4242  4242  4242", "4242\t4242\t4242\t4242", "4242 - 4242 - 4242 - 4242", "42 " * 8],
)
def test_scrub_masks_every_spacing_the_payment_simulator_accepts(text):
    assert payments.charge(text, 100).last4 == "4242"
    assert "4242" not in scrub(f"my card is {text} thanks")


def test_scrub_leaves_ordinary_numbers(text="order 12345 for 2 mugs"):
    assert scrub(text) == text


def test_requests_are_logged_as_json_without_query_strings(client, logs):
    client.get("/search", params={"q": "secret-search-words"})
    entry = next(e for e in logs() if e.get("path") == "/search")
    assert entry["method"] == "GET" and entry["status"] == 200 and entry["ms"] >= 0
    assert entry["level"] == "INFO" and entry["logger"] == "kyo"
    assert "secret-search-words" not in json.dumps(logs())


def test_a_run_is_logged_with_its_id_from_start_to_finish(client, app, logs):
    run_id = int(buy(client, app))
    for thread in app.state.runs.threads.values():
        thread.join(5)
    messages = {e["message"]: e for e in logs() if e.get("run_id") == run_id}
    assert "run started" in messages and "run finished" in messages
    assert messages["run finished"]["status"] == "purchased"


def test_logs_never_contain_passwords_or_card_numbers(client, app, logs):
    buy(client, app)
    text = json.dumps(logs())
    assert CARD not in text and "a long password" not in text


def test_card_number_typed_into_a_request_stays_out_of_the_logs(client, logs):
    register(client)
    post_form(client, "/assistant", {"request": f"Buy a purple mug with card {CARD}."})
    for thread in client.app.state.runs.threads.values():
        thread.join(5)
    assert CARD not in json.dumps(logs())


def test_failed_run_is_logged_as_an_error(client, logs, monkeypatch):
    import kyo.store.runs as runs_module

    def explode(*args, **kwargs):
        raise RuntimeError("the team could not start")

    monkeypatch.setattr(runs_module, "run_team", explode)
    register(client)
    post_form(client, "/assistant", {"request": "Buy a mug"})
    for thread in client.app.state.runs.threads.values():
        thread.join(5)
    entry = next(e for e in logs() if e["message"] == "run failed")
    assert entry["level"] == "ERROR" and "could not start" in entry["error"]


def test_timeline_shows_every_step_and_the_tool_used(client, app):
    run_id = buy(client, app)
    page = client.get(f"/assistant/{run_id}/timeline")
    text = text_of(page)
    assert page.status_code == 200 and "Run timeline" in text
    assert "search_products" in text and "get_product" in text and "update_cart" in text
    assert "checkout" in text and "gateway" in text and "coordinator" not in text.split("#")[0]
    assert "Store steps used" in text and "Model review none" in text


def test_timeline_is_linked_from_the_run_page(client, app):
    run_id = buy(client, app)
    assert f'href="/assistant/{run_id}/timeline"' in client.get(f"/assistant/{run_id}").text


def test_timeline_never_shows_the_card_number(client, app):
    run_id = buy(client, app)
    assert CARD not in client.get(f"/assistant/{run_id}/timeline").text


def test_timeline_is_private(client, app):
    run_id = buy(client, app)
    other = TestClient(app, follow_redirects=True)
    register(other, email="other@example.com")
    assert other.get(f"/assistant/{run_id}/timeline").status_code == 404
    assert "/login" in str(
        TestClient(app, follow_redirects=True).get(f"/assistant/{run_id}/timeline").url
    )


def test_card_number_typed_into_a_request_or_answer_is_not_stored(client, app):
    from sqlalchemy import inspect, text

    only_red_mug(app)
    register(client)
    typed = f"Buy a mug. Ask me at most 1 question. My card is {CARD}."
    reply = post_form(client, "/assistant", {"request": typed})
    url = re.search(r"/assistant/\d+$", str(reply.url)).group(0)
    page = wait_for(client, url, 'name="answer"')
    action = re.search(rf'action="({url}/questions/\d+/answer)"', page.text).group(1)
    post_form(client, action, {"answer": f"Use {CARD} and I do not mind."})
    page = wait_for(client, url, 'name="card_number"')
    decision = re.search(rf'action="({url}/questions/\d+/decision)"', page.text).group(1)
    post_form(client, decision, {"approved": "no"})
    wait_for(client, url, 'data-status="declined"')
    with app.state.engine.connect() as connection:
        for table in inspect(connection).get_table_names():
            for row in connection.execute(text(f'SELECT * FROM "{table}"')):
                assert CARD not in str(tuple(row)), table
