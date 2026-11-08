import pytest
from conftest import post_form, register, text_of

from kyo.store.db import transaction
from kyo.store.models import User


def test_registration_page_creates_an_account_and_signs_in(client, app):
    reply = register(client)
    assert reply.status_code == 200 and str(reply.url).endswith("/")
    assert "Log out" in reply.text and "Shopper" in reply.text
    with transaction(app.state.sessions) as session:
        assert session.query(User).one().email == "shopper@example.com"


def test_login_and_logout_through_forms(client):
    register(client)
    reply = post_form(client, "/logout", {})
    assert "Log in" in reply.text and "Log out" not in reply.text
    reply = post_form(
        client, "/login", {"email": "shopper@example.com", "password": "a long password"}
    )
    assert "Log out" in reply.text


def test_anonymous_visitors_see_log_in_and_register(client):
    text = text_of(client.get("/"))
    assert "Log in" in text and "Create account" in text


def test_wrong_password_shows_an_error_and_keeps_the_email(client):
    register(client)
    post_form(client, "/logout", {})
    reply = post_form(client, "/login", {"email": "shopper@example.com", "password": "nope nope"})
    assert reply.status_code == 401 and "Wrong email or password" in reply.text
    assert 'value="shopper@example.com"' in reply.text and "nope nope" not in reply.text


@pytest.mark.parametrize(
    "change, message",
    [
        ({"email": "not-an-email"}, "valid email"),
        ({"name": " "}, "your name"),
        ({"password": "short"}, "at least 8"),
    ],
)
def test_registration_errors_are_clear_and_keep_what_was_typed(client, change, message):
    data = {"name": "Shopper", "email": "s@example.com", "password": "a long password", **change}
    reply = post_form(client, "/register", data)
    assert reply.status_code == 422 and message in reply.text
    assert "a long password" not in reply.text


def test_duplicate_email_is_explained(client):
    register(client)
    post_form(client, "/logout", {})
    reply = register(client)
    assert reply.status_code == 422 and "already exists" in reply.text


def test_login_returns_to_the_page_that_asked_for_it(client):
    register(client)
    post_form(client, "/logout", {})
    data = {"email": "shopper@example.com", "password": "a long password", "next": "/search?q=mug"}
    reply = post_form(client, "/login", data)
    assert str(reply.url).endswith("/search?q=mug")


@pytest.mark.parametrize(
    "target", ["https://evil.example/", "//evil.example/", "/\\evil.example", "javascript:alert(1)"]
)
def test_login_never_redirects_to_another_site(client, target):
    register(client)
    post_form(client, "/logout", {})
    data = {"email": "shopper@example.com", "password": "a long password", "next": target}
    reply = post_form(client, "/login", data, follow=False)
    assert reply.status_code == 303 and reply.headers["location"] == "/"


def test_login_pages_redirect_when_already_signed_in(client):
    register(client)
    assert str(client.get("/login").url).endswith("/")
    assert str(client.get("/register").url).endswith("/")


def test_session_cookie_settings_on_the_form_login(client):
    reply = register(client, follow=False)
    cookie = reply.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
