import re

import pytest
from fastapi.testclient import TestClient

from kyo.app import Settings, create_app
from kyo.store.seed import seed_catalog


@pytest.fixture
def app(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'web.sqlite3'}",
        question_timeout=20.0,
        poll_seconds=0.02,
    )
    app = create_app(settings)
    seed_catalog(app.state.sessions)
    yield app
    for thread in app.state.runs.threads.values():
        thread.join(5)
    app.state.engine.dispose()


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=True)


def text_of(response):
    """The visible text of a page, for checking what a shopper would read."""
    body = re.sub(r"<(script|style).*?</\1>", " ", response.text, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", body).split())


def post_form(client, url, data, follow=True):
    """Submit a form the way a browser does."""
    return client.post(url, data=data, follow_redirects=follow)


def register(client, follow=True, email="shopper@example.com"):
    data = {"name": "Shopper", "email": email, "password": "a long password"}
    return post_form(client, "/register", data, follow)
