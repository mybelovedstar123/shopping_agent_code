import pytest
from fastapi.testclient import TestClient
from webutil import csrf_token, post_form, register

from kyo.web.security import LoginThrottle

FORM_POSTS = ["/login", "/register", "/logout", "/cart/MUG-0001", "/checkout", "/assistant"]


@pytest.mark.parametrize("url", FORM_POSTS)
def test_form_posts_without_a_token_are_refused(client, url):
    client.get("/")
    reply = client.post(url, data={"email": "a@example.com", "password": "x", "quantity": 1})
    assert reply.status_code == 403


@pytest.mark.parametrize("url", FORM_POSTS)
def test_form_posts_with_a_wrong_token_are_refused(client, url):
    client.get("/")
    assert client.post(url, data={"csrf_token": "guess"}).status_code == 403


def test_form_post_without_the_cookie_is_refused(app):
    token = csrf_token(TestClient(app))
    assert TestClient(app).post("/login", data={"csrf_token": token}).status_code == 403


def test_every_form_on_every_page_carries_the_token(client, app):
    assert "csrf_token" in client.get("/login").text
    assert "csrf_token" in client.get("/register").text
    register(client)
    pages = ["/", "/product/MUG-0002", "/assistant", "/cart"]
    for page in pages:
        html = client.get(page).text
        post_forms = html.count('method="post"')
        assert html.count('name="csrf_token"') == post_forms, page


def test_token_stays_the_same_for_a_browser(client):
    first = csrf_token(client)
    client.get("/")
    assert csrf_token(client) == first


def test_csrf_cookie_is_http_only(client):
    cookie = client.get("/").headers["set-cookie"].lower()
    assert "kyo_csrf" in cookie and "httponly" in cookie and "samesite=lax" in cookie


@pytest.mark.parametrize("url", ["/", "/api/health", "/static/site.css", "/nope"])
def test_security_headers_are_on_every_response(client, url):
    headers = client.get(url).headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "same-origin"
    assert "default-src 'self'" in headers["content-security-policy"]
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_pages_with_personal_data_are_not_cached(client):
    assert client.get("/").headers["cache-control"] == "no-store"
    assert "cache-control" not in client.get("/static/site.css").headers


def test_pages_use_no_inline_scripts_so_the_policy_holds(client):
    register(client)
    for page in ("/", "/assistant", "/cart"):
        assert "<script>" not in client.get(page).text and "onclick=" not in client.get(page).text


def test_api_docs_can_load_their_scripts(client):
    reply = client.get("/docs")
    assert reply.status_code == 200 and "content-security-policy" not in reply.headers


def test_secure_cookies_can_be_required(tmp_path):
    from kyo.app import Settings, create_app

    settings = Settings(database_url=f"sqlite:///{tmp_path / 's.sqlite3'}", secure_cookies=True)
    app = create_app(settings)
    client = TestClient(app, base_url="https://testserver", follow_redirects=True)
    assert "secure" in client.get("/").headers["set-cookie"].lower()
    reply = post_form(
        client,
        "/register",
        {"name": "A", "email": "a@example.com", "password": "a long password"},
        follow=False,
    )
    assert "secure" in reply.headers["set-cookie"].lower()
    app.state.engine.dispose()


def test_api_refuses_a_cross_site_style_form_post(client):
    """A simple form on another site cannot send JSON, so API bodies cannot be forged."""
    register(client)
    reply = client.post(
        "/api/checkout",
        content="card_number=4242424242424242",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert reply.status_code == 422
    reply = client.post(
        "/api/runs",
        content='{"request": "Buy everything"}',
        headers={"Content-Type": "text/plain"},
    )
    assert reply.status_code == 422


def failed_login(client, email="shopper@example.com"):
    return post_form(client, "/login", {"email": email, "password": "wrong password"})


def test_repeated_login_failures_are_throttled_even_with_the_right_password(client):
    register(client)
    post_form(client, "/logout", {})
    for _ in range(5):
        assert failed_login(client).status_code == 401
    assert failed_login(client).status_code == 429
    reply = post_form(
        client, "/login", {"email": "shopper@example.com", "password": "a long password"}
    )
    assert reply.status_code == 429 and "Too many failed attempts" in reply.text


def test_api_login_shares_the_same_throttle(client):
    for _ in range(5):
        failed_login(client)
    body = {"email": "shopper@example.com", "password": "anything"}
    assert client.post("/api/login", json=body).status_code == 429


def test_throttle_applies_to_one_email_not_everyone(client):
    for _ in range(5):
        failed_login(client)
    assert failed_login(client, "someone-else@example.com").status_code == 401


def test_successful_login_clears_the_failures(client):
    register(client)
    post_form(client, "/logout", {})
    for _ in range(4):
        failed_login(client)
    good = {"email": "shopper@example.com", "password": "a long password"}
    assert "Log out" in post_form(client, "/login", good).text
    post_form(client, "/logout", {})
    for _ in range(4):
        assert failed_login(client).status_code == 401


def test_throttle_lets_go_after_the_window():
    now = [0.0]
    throttle = LoginThrottle(max_failures=2, window_seconds=10, clock=lambda: now[0])

    class FakeRequest:
        class client:  # noqa: N801
            host = "1.2.3.4"

    throttle.failed(FakeRequest, "a@example.com")
    throttle.failed(FakeRequest, "A@example.com ")
    assert throttle.locked(FakeRequest, "a@example.com")
    now[0] = 11.0
    assert not throttle.locked(FakeRequest, "a@example.com")
