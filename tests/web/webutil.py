"""Helpers for web tests: reading pages and filling in forms as a browser would."""

import html
import re


def text_of(response):
    """The visible text of a page, for checking what a shopper would read."""
    body = re.sub(r"<(script|style).*?</\1>", " ", response.text, flags=re.S)
    return html.unescape(" ".join(re.sub(r"<[^>]+>", " ", body).split()))


def csrf_token(client):
    """The token this browser's forms carry."""
    if "kyo_csrf" not in client.cookies:
        client.get("/")
    return client.cookies["kyo_csrf"]


def post_form(client, url, data, follow=True):
    """Submit a form the way a browser does, including its CSRF token."""
    return client.post(
        url, data={**data, "csrf_token": csrf_token(client)}, follow_redirects=follow
    )


def register(client, follow=True, email="shopper@example.com"):
    data = {"name": "Shopper", "email": email, "password": "a long password"}
    return post_form(client, "/register", data, follow)
