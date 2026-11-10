"""Accounts, password hashing and login sessions."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import now
from .models import LoginSession, User

SESSION_LIFETIME = timedelta(days=14)
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 200
_N, _R, _P = 2**14, 8, 1
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountError(Exception):
    """An account action was refused. The message is safe to show to the user."""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        expected = bytes.fromhex(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


# Checked when an email is unknown, so failed logins take the same time either way.
_DECOY_HASH = hash_password(secrets.token_hex(8))


def register(session: Session, email: str, name: str, password: str) -> User:
    email = email.strip().lower()
    name = name.strip()
    if not EMAIL.match(email) or len(email) > 254:
        raise AccountError("Enter a valid email address.")
    if not name or len(name) > 100:
        raise AccountError("Enter your name.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"Passwords must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AccountError(f"Passwords can be at most {MAX_PASSWORD_LENGTH} characters.")
    user = User(email=email, name=name, password_hash=hash_password(password))
    session.add(user)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise AccountError("An account with this email already exists.") from None
    return user


def authenticate(session: Session, email: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        verify_password(password, _DECOY_HASH)
        return None
    return user if verify_password(password, user.password_hash) else None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(session: Session, user: User) -> str:
    """Create a login session and return its token. Only the token's hash is stored."""
    token = secrets.token_urlsafe(32)
    session.add(
        LoginSession(
            token_hash=_token_hash(token), user_id=user.id, expires_at=now() + SESSION_LIFETIME
        )
    )
    session.flush()
    return token


def user_for_token(session: Session, token: str | None) -> User | None:
    if not token:
        return None
    login = session.get(LoginSession, _token_hash(token))
    if login is None or login.revoked or login.expires_at <= now():
        return None
    return session.get(User, login.user_id)


def end_session(session: Session, token: str | None) -> None:
    if token:
        login = session.get(LoginSession, _token_hash(token))
        if login is not None:
            login.revoked = True


def lock_user(session: Session, user_id: int) -> None:
    """Take a write lock on a user until the transaction ends, so that changes to what the user
    owns happen one at a time. Any database blocks a second writer to the same row."""
    session.execute(
        update(User)
        .where(User.id == user_id)
        .values(name=User.name)
        .execution_options(synchronize_session=False)
    )


def end_all_sessions(session: Session, user_id: int) -> None:
    session.execute(
        update(LoginSession).where(LoginSession.user_id == user_id).values(revoked=True)
    )
