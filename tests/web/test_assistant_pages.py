import re
import time

from fastapi.testclient import TestClient
from webutil import post_form, register, text_of


def start(client, request):
    reply = post_form(client, "/assistant", {"request": request})
    assert reply.status_code == 200, reply.text
    return re.search(r"/assistant/(\d+)$", str(reply.url)).group(1)


def wait_for(client, run_id, text, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        page = client.get(f"/assistant/{run_id}")
        if text in page.text:
            return page
        time.sleep(0.03)
    raise AssertionError(f"never saw {text!r}: {text_of(page)[-500:]}")


NOTHING_FITS = "Buy a purple mug. Do not ask me anything."


def test_assistant_requires_login(client):
    assert "/login" in str(client.get("/assistant").url)


def test_navigation_offers_the_assistant(client):
    assert 'href="/assistant"' in register(client).text


def test_empty_request_is_explained(client):
    register(client)
    reply = post_form(client, "/assistant", {"request": "  "})
    assert reply.status_code == 422 and "what you would like" in reply.text


def test_request_starts_a_run_and_shows_progress(client):
    register(client)
    run_id = start(client, NOTHING_FITS)
    page = wait_for(client, run_id, 'data-status="declined"')
    text = text_of(page)
    assert "Buy a purple mug" in text and "What the agents did" in text
    assert "request read 1 item(s) using rules" in text
    assert "selection" in text and "catalog" in text


def test_finished_run_explains_the_outcome(client):
    register(client)
    run_id = start(client, NOTHING_FITS)
    text = text_of(wait_for(client, run_id, 'data-status="declined"'))
    assert "Did not buy" in text and "no listing fits" in text


def test_live_fragment_has_no_page_layout_and_reports_when_finished(client):
    register(client)
    run_id = start(client, NOTHING_FITS)
    wait_for(client, run_id, 'data-status="declined"')
    reply = client.get(f"/assistant/{run_id}/live")
    assert "<html" not in reply.text and 'data-status="declined"' in reply.text
    assert reply.headers["x-run-finished"] == "true"


def test_page_loads_the_live_updates_script(client):
    register(client)
    run_id = start(client, NOTHING_FITS)
    page = client.get(f"/assistant/{run_id}")
    assert 'src="/static/run.js"' in page.text
    assert f'data-live-url="/assistant/{run_id}/live"' in page.text
    assert client.get("/static/run.js").status_code == 200


def test_runs_are_listed_on_the_assistant_page(client):
    register(client)
    run_id = start(client, NOTHING_FITS)
    wait_for(client, run_id, 'data-status="declined"')
    assert f'href="/assistant/{run_id}"' in client.get("/assistant").text


def test_a_request_is_shown_escaped(client):
    register(client)
    run_id = start(client, "Buy <script>alert(1)</script>. Do not ask me anything.")
    page = client.get(f"/assistant/{run_id}")
    assert "<script>alert(1)</script>" not in page.text
    wait_for(client, run_id, "data-status=", 5)
    time.sleep(0.2)


def test_runs_are_private(client, app):
    register(client)
    run_id = start(client, NOTHING_FITS)
    other = TestClient(app, follow_redirects=True)
    register(other, email="other@example.com")
    assert other.get(f"/assistant/{run_id}").status_code == 404
    assert other.get(f"/assistant/{run_id}/live").status_code == 404
    assert f"/assistant/{run_id}" not in other.get("/assistant").text


def test_live_fragment_requires_login(app):
    assert TestClient(app).get("/assistant/1/live").status_code == 401
