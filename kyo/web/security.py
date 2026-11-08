"""Protections every page gets: CSRF tokens, security headers and login throttling."""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections import defaultdict

from fastapi import HTTPException, Request

CSRF_COOKIE = "kyo_csrf"
CSRF_FIELD = "csrf_token"
CSRF_MAX_AGE = 60 * 60 * 24 * 14
# The interactive API docs load their own script from a CDN, so they get no script policy.
DOCS_PATHS = ("/docs", "/redoc")
CONTENT_POLICY = (
    "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; "
    "object-src 'none'"
)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}


async def protect_responses(request: Request, call_next):
    """Give each browser a CSRF token, and add the security headers to every response."""
    token = request.cookies.get(CSRF_COOKIE)
    fresh = not token
    if fresh:
        token = secrets.token_urlsafe(32)
    request.state.csrf_token = token
    response = await call_next(request)
    if fresh:
        response.set_cookie(
            CSRF_COOKIE,
            token,
            max_age=CSRF_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=request.app.state.settings.secure_cookies,
        )
    response.headers.update(SECURITY_HEADERS)
    if not request.url.path.startswith(DOCS_PATHS):
        response.headers["Content-Security-Policy"] = CONTENT_POLICY
    if not request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-store"
    return response


async def verify_csrf(request: Request) -> None:
    """A form post must carry the token from this browser's cookie.

    Another site can make a browser send the cookie, but cannot read it to fill in the form.
    """
    if request.method != "POST":
        return
    form = await request.form()
    sent = str(form.get(CSRF_FIELD) or "")
    expected = request.cookies.get(CSRF_COOKIE) or ""
    if not expected or not hmac.compare_digest(sent, expected):
        raise HTTPException(403, "This form has expired. Go back, reload the page and try again.")


class LoginThrottle:
    """Slows password guessing: too many failures for one email from one address locks it out."""

    def __init__(self, max_failures: int = 5, window_seconds: float = 900.0, clock=time.monotonic):
        self.max_failures = max_failures
        self.window = window_seconds
        self.clock = clock
        self._failures: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    @staticmethod
    def _key(request: Request, email: str) -> tuple[str, str]:
        host = request.client.host if request.client else "unknown"
        return email.strip().lower(), host

    def _recent(self, key: tuple[str, str]) -> list[float]:
        cutoff = self.clock() - self.window
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def locked(self, request: Request, email: str) -> bool:
        with self._lock:
            return len(self._recent(self._key(request, email))) >= self.max_failures

    def failed(self, request: Request, email: str) -> None:
        with self._lock:
            self._failures[self._key(request, email)].append(self.clock())

    def succeeded(self, request: Request, email: str) -> None:
        with self._lock:
            self._failures.pop(self._key(request, email), None)


TOO_MANY_LOGINS = "Too many failed attempts. Wait a few minutes and try again."
