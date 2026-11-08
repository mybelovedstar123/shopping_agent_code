"""Agent runs through the API: start, follow, answer, approve."""

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from kyo.app import Settings, create_app
from kyo.store import payments
from kyo.store.db import transaction
from kyo.store.models import AgentRun, Category, Order, Product
from kyo.store.runs import RunManager

GOOD = payments.APPROVED_TEST_CARD
NO_QUESTIONS = "Do not ask me anything."


@pytest.fixture
def app(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'runs.sqlite3'}",
        question_timeout=20.0,
        poll_seconds=0.02,
    )
    app = create_app(settings)
    with transaction(app.state.sessions) as session:
        mugs = Category(name="Mugs", slug="mugs")
        session.add_all(
            [
                Product(
                    sku="M1",
                    title="Red Mug",
                    category=mugs,
                    price_cents=500,
                    stock=3,
                    attributes={"colour": "red", "capacity": "300 ml"},
                ),
                Product(
                    sku="M2",
                    title="Blue Mug",
                    category=mugs,
                    price_cents=700,
                    stock=3,
                    attributes={"colour": "blue", "capacity": "300 ml"},
                ),
            ]
        )
    yield app
    for thread in app.state.runs.threads.values():
        thread.join(5)
    app.state.engine.dispose()


def login(app, email="shopper@example.com"):
    client = TestClient(app)
    client.post(
        "/api/register", json={"email": email, "name": "Shopper", "password": "a long password"}
    )
    return client


@pytest.fixture
def client(app):
    return login(app)


def start(client, request):
    reply = client.post("/api/runs", json={"request": request})
    assert reply.status_code == 201, reply.text
    return reply.json()["id"]


def wait_until(client, run_id, condition, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if condition(run):
            return run
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting; last state: {run}")


def open_question(run, kind):
    return next((q for q in run["questions"] if q["open"] and q["kind"] == kind), None)


def awaiting(kind):
    return lambda run: open_question(run, kind) is not None or run["finished"]


def approve(client, run_id, approved=True, card=GOOD):
    run = wait_until(client, run_id, awaiting("approval"))
    question = open_question(run, "approval")
    assert question, run
    url = f"/api/runs/{run_id}/questions/{question['id']}/decision"
    return client.post(url, json={"approved": approved, "card_number": card})


def finished(client, run_id):
    return wait_until(client, run_id, lambda run: run["finished"])


def stock(app, sku):
    with transaction(app.state.sessions) as session:
        return session.scalar(select(Product.stock).where(Product.sku == sku))


def orders(app):
    with transaction(app.state.sessions) as session:
        return session.query(Order).count()


def test_purchase_happens_only_after_approving_the_exact_cart(client, app):
    run_id = start(client, f"Buy a red mug. {NO_QUESTIONS}")
    run = wait_until(client, run_id, awaiting("approval"))
    question = open_question(run, "approval")
    assert question["total"] == 5.0 and question["lines"][0]["id"] == "M1"
    assert "1 x Red Mug" in question["text"] and "5.00" in question["text"]
    assert orders(app) == 0 and stock(app, "M1") == 3, "nothing is bought before approval"
    decision = approve(client, run_id)
    assert decision.json()["approved"] is True
    run = finished(client, run_id)
    assert run["status"] == "purchased"
    assert run["result"]["order_id"] == decision.json()["order"]["id"]
    assert stock(app, "M1") == 2


def test_refusing_means_no_order_and_an_empty_run_cart(client, app):
    run_id = start(client, f"Buy a red mug. {NO_QUESTIONS}")
    approve(client, run_id, approved=False)
    run = finished(client, run_id)
    assert run["status"] == "declined" and orders(app) == 0 and stock(app, "M1") == 3


def test_declined_card_keeps_the_question_open_for_another_try(client, app):
    run_id = start(client, f"Buy a red mug. {NO_QUESTIONS}")
    reply = approve(client, run_id, card=payments.DECLINED_TEST_CARD)
    assert reply.status_code == 402 and orders(app) == 0
    run = client.get(f"/api/runs/{run_id}").json()
    assert open_question(run, "approval") is not None
    assert approve(client, run_id).json()["approved"] is True
    assert finished(client, run_id)["status"] == "purchased"


def test_stock_lost_before_approval_is_reported_to_the_team(client, app):
    run_id = start(client, f"Buy a red mug. {NO_QUESTIONS}")
    wait_until(client, run_id, awaiting("approval"))
    with transaction(app.state.sessions) as session:
        session.scalar(select(Product).where(Product.sku == "M1")).stock = 0
    decision = approve(client, run_id).json()
    assert decision["approved"] is False and "enough stock" in decision["failed"]
    run = finished(client, run_id)
    assert run["status"] == "declined" and orders(app) == 0


def test_questions_wait_for_the_shopper_and_answers_change_the_purchase(client, app):
    run_id = start(client, "Buy a mug. Ask me at most 1 question.")
    run = wait_until(client, run_id, awaiting("question"))
    question = open_question(run, "question")
    assert run["status"] == "waiting" and question["text"].startswith("What ")
    url = f"/api/runs/{run_id}/questions/{question['id']}/answer"
    if "colour" in question["text"]:
        answer = "Blue, please."
    else:
        answer = "I don't mind."
    assert client.post(url, json={"answer": answer}).status_code == 204
    approve(client, run_id)
    run = finished(client, run_id)
    assert run["status"] == "purchased"
    if "colour" in question["text"]:
        assert "M2" in run["result"]["lines"]


def test_an_answered_question_cannot_be_answered_twice(client):
    run_id = start(client, "Buy a mug. Ask me at most 1 question.")
    question = open_question(wait_until(client, run_id, awaiting("question")), "question")
    url = f"/api/runs/{run_id}/questions/{question['id']}/answer"
    client.post(url, json={"answer": "I don't mind."})
    assert client.post(url, json={"answer": "Blue"}).status_code == 409
    approve(client, run_id, approved=False)


def test_blank_answer_is_refused(client):
    run_id = start(client, "Buy a mug. Ask me at most 1 question.")
    question = open_question(wait_until(client, run_id, awaiting("question")), "question")
    url = f"/api/runs/{run_id}/questions/{question['id']}/answer"
    assert client.post(url, json={"answer": "  "}).status_code == 409
    client.post(url, json={"answer": "I don't mind."})
    approve(client, run_id, approved=False)


def test_runs_and_their_questions_are_private(client, app):
    run_id = start(client, f"Buy a red mug. {NO_QUESTIONS}")
    run = wait_until(client, run_id, awaiting("approval"))
    question = open_question(run, "approval")
    other = login(app, "other@example.com")
    assert other.get(f"/api/runs/{run_id}").status_code == 404
    assert other.get("/api/runs").json() == []
    url = f"/api/runs/{run_id}/questions/{question['id']}/decision"
    assert other.post(url, json={"approved": True, "card_number": GOOD}).status_code == 404
    assert orders(app) == 0
    approve(client, run_id, approved=False)


def test_run_cart_is_separate_from_the_shoppers_cart(client):
    client.put("/api/cart/M2", json={"quantity": 2})
    run_id = start(client, f"Buy a red mug. {NO_QUESTIONS}")
    approve(client, run_id)
    finished(client, run_id)
    lines = client.get("/api/cart").json()["lines"]
    assert [(x["id"], x["quantity"]) for x in lines] == [("M2", 2)]


def test_runs_record_what_each_agent_did(client):
    run_id = start(client, f"Buy a red mug. {NO_QUESTIONS}")
    approve(client, run_id, approved=False)
    run = finished(client, run_id)
    agents = {e["agent"] for e in run["events"]}
    assert {"request", "catalog", "selection", "review"} <= agents
    last = run["events"][-1]["seq"]
    assert client.get(f"/api/runs/{run_id}", params={"since": last}).json()["events"] == []


def test_a_run_with_nothing_to_buy_declines_without_asking_for_approval(client, app):
    run_id = start(client, f"Buy a green mug. {NO_QUESTIONS}")
    run = finished(client, run_id)
    assert run["status"] == "declined" and run["questions"] == [] and orders(app) == 0


@pytest.mark.parametrize("request_text", ["", "   ", "x" * 2001], ids=["empty", "blank", "long"])
def test_invalid_requests_are_refused(client, request_text):
    assert client.post("/api/runs", json={"request": request_text}).status_code == 422


def test_too_many_active_runs_are_refused(client, app):
    ids = [start(client, f"Buy a red mug. {NO_QUESTIONS}") for _ in range(3)]
    reply = client.post("/api/runs", json={"request": "Buy a mug"})
    assert reply.status_code == 422 and "several assistants" in reply.json()["detail"]
    for run_id in ids:
        approve(client, run_id, approved=False)


def test_runs_require_login(app):
    assert TestClient(app).post("/api/runs", json={"request": "x"}).status_code == 401


def test_unanswered_question_times_out_and_the_run_ends(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'timeout.sqlite3'}",
        question_timeout=0.1,
        poll_seconds=0.01,
    )
    app = create_app(settings)
    with transaction(app.state.sessions) as session:
        session.add(
            Product(
                sku="M1",
                title="Red Mug",
                category=Category(name="Mugs", slug="mugs"),
                price_cents=500,
                stock=3,
                attributes={"colour": "red"},
            )
        )
    client = login(app)
    run_id = start(client, f"Buy a red mug. {NO_QUESTIONS}")
    run = finished(client, run_id)
    assert run["status"] == "declined" and "in time" in run["result"]["reason"]
    assert orders(app) == 0
    app.state.engine.dispose()


def test_failure_inside_a_run_is_reported_not_raised(app, client, monkeypatch):
    import kyo.store.runs as runs_module

    def explode(*args, **kwargs):
        raise RuntimeError("the team could not start")

    monkeypatch.setattr(runs_module, "run_team", explode)
    run = finished(client, start(client, "Buy a mug"))
    assert run["status"] == "failed" and "could not start" in run["result"]["reason"]


def test_recover_closes_runs_left_by_a_restart(app, client):
    with transaction(app.state.sessions) as session:
        user_id = session.scalar(select(AgentRun.user_id)) or 1
        session.add(AgentRun(user_id=user_id, request="left behind", status="running"))
    manager = RunManager(app.state.sessions)
    assert manager.recover() == 1
    with transaction(app.state.sessions) as session:
        run = session.scalar(select(AgentRun).where(AgentRun.request == "left behind"))
        assert run.status == "failed" and "restarted" in run.result["reason"]


def test_a_run_with_an_open_question_always_reads_as_waiting():
    """The status and the questions are read separately, so they can briefly disagree."""
    from datetime import datetime

    from kyo.store.models import AgentQuestion
    from kyo.store.runs import run_view

    run = AgentRun(id=1, user_id=1, request="x", status="running")
    run.created_at = datetime(2026, 1, 1)
    run.questions = [AgentQuestion(id=1, run_id=1, kind="question", text="What colour?")]
    assert run_view(run)["status"] == "waiting"
    run.questions[0].answered_at = run.created_at
    assert run_view(run)["status"] == "running"
