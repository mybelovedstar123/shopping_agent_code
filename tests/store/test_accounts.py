from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from kyo.app import Settings, create_app
from kyo.store import accounts
from kyo.store.db import now, transaction
from kyo.store.models import LoginSession, User


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'api.sqlite3'}"))
    yield app
    app.state.engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


ALICE = {"email": "alice@example.com", "name": "Alice", "password": "correct horse"}


def test_password_hash_verifies_only_the_right_password():
    stored = accounts.hash_password("secret value")
    assert stored.startswith("scrypt$") and "secret value" not in stored
    assert accounts.verify_password("secret value", stored)
    assert not accounts.verify_password("wrong value", stored)


def test_same_password_hashes_differently_each_time():
    assert accounts.hash_password("same") != accounts.hash_password("same")


@pytest.mark.parametrize("stored", ["", "scrypt$bad", "scrypt$a$b$c$d$e"])
def test_malformed_stored_hash_never_verifies(stored):
    assert not accounts.verify_password("x", stored)


def test_register_signs_in_and_returns_no_secrets(client):
    reply = client.post("/api/register", json=ALICE)
    assert reply.status_code == 201
    assert reply.json() == {"id": 1, "email": "alice@example.com", "name": "Alice"}
    assert client.get("/api/me").json()["email"] == "alice@example.com"


def test_password_is_stored_hashed(client, app):
    client.post("/api/register", json=ALICE)
    with transaction(app.state.sessions) as session:
        assert session.query(User).one().password_hash.startswith("scrypt$")


def test_session_cookie_is_http_only(client):
    reply = client.post("/api/register", json=ALICE)
    cookie = reply.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


@pytest.mark.parametrize(
    "change, message",
    [
        ({"email": "not-an-email"}, "valid email"),
        ({"name": "  "}, "your name"),
        ({"password": "short"}, "at least 8"),
        ({"password": "p" * 201}, "at most 200"),
    ],
)
def test_register_validates_input(client, change, message):
    reply = client.post("/api/register", json={**ALICE, **change})
    assert reply.status_code == 422 and message in reply.json()["detail"]


def test_email_can_register_only_once_ignoring_case(client):
    client.post("/api/register", json=ALICE)
    reply = client.post("/api/register", json={**ALICE, "email": "ALICE@example.com"})
    assert reply.status_code == 422 and "already exists" in reply.json()["detail"]


def test_login_with_the_right_password(client):
    client.post("/api/register", json=ALICE)
    client.post("/api/logout")
    reply = client.post("/api/login", json={"email": ALICE["email"], "password": ALICE["password"]})
    assert reply.status_code == 200 and client.get("/api/me").status_code == 200


@pytest.mark.parametrize(
    "email, password",
    [("alice@example.com", "wrong password"), ("nobody@example.com", "correct horse")],
)
def test_failed_logins_give_the_same_answer(client, email, password):
    client.post("/api/register", json=ALICE)
    client.post("/api/logout")
    reply = client.post("/api/login", json={"email": email, "password": password})
    assert reply.status_code == 401 and reply.json()["detail"] == "Wrong email or password."
    assert client.get("/api/me").status_code == 401


def test_me_requires_login(client):
    assert client.get("/api/me").status_code == 401


def test_invented_token_is_rejected(client):
    client.cookies.set("kyo_session", "made-up")
    assert client.get("/api/me").status_code == 401


def test_logout_revokes_the_session_on_the_server(client):
    client.post("/api/register", json=ALICE)
    token = client.cookies["kyo_session"]
    client.post("/api/logout")
    client.cookies.set("kyo_session", token)
    assert client.get("/api/me").status_code == 401


def test_expired_session_is_rejected(client, app):
    client.post("/api/register", json=ALICE)
    with transaction(app.state.sessions) as session:
        session.query(LoginSession).one().expires_at = now() - timedelta(seconds=1)
    assert client.get("/api/me").status_code == 401


def test_only_a_hash_of_the_token_is_stored(client, app):
    client.post("/api/register", json=ALICE)
    with transaction(app.state.sessions) as session:
        assert session.query(LoginSession).one().token_hash != client.cookies["kyo_session"]


def test_all_sessions_can_be_ended(client, app):
    client.post("/api/register", json=ALICE)
    with transaction(app.state.sessions) as session:
        accounts.end_all_sessions(session, session.query(User).one().id)
    assert client.get("/api/me").status_code == 401
