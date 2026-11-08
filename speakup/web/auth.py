"""Sign-in state, CSRF protection and helpers shared by the web views."""

from __future__ import annotations

import hmac
import secrets
from functools import wraps
from typing import Callable

from flask import Flask, abort, g, redirect, request, session, url_for

from speakup.database.connection import get_session
from speakup.database.repository import UserRepository

_CSRF_KEY = "_csrf_token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def csrf_token() -> str:
    token = session.get(_CSRF_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[_CSRF_KEY] = token
    return token


def _check_csrf() -> None:
    if request.method in _SAFE_METHODS:
        return
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    expected = session.get(_CSRF_KEY, "")
    if not expected or not hmac.compare_digest(str(sent), str(expected)):
        abort(400, description="Your session expired. Please reload the page and try again.")


def _load_user() -> None:
    g.user_id = None
    g.user_name = None
    user_id = session.get("user_id")
    if user_id is None:
        return
    with get_session() as db:
        user = UserRepository(db).get_by_id(int(user_id))
        if user is None:
            session.clear()
            return
        g.user_id = user.id
        g.user_name = user.name


def sign_in(user_id: int) -> None:
    session.clear()           # new session id on sign-in
    session["user_id"] = user_id
    csrf_token()


def sign_out() -> None:
    session.clear()


def login_required(view: Callable) -> Callable:
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user_id") is None:
            if request.path.startswith("/api/"):
                abort(401)
            return redirect(url_for("web.landing"))
        return view(*args, **kwargs)
    return wrapped


def init_app(app: Flask) -> None:
    app.before_request(_check_csrf)
    app.before_request(_load_user)

    @app.context_processor
    def _inject() -> dict:
        name = g.get("user_name") or ""
        initials = "".join(part[0] for part in name.split()[:2]).upper()
        return {
            "csrf_token": csrf_token,
            "current_user_name": name,
            "current_user_initials": initials or "?",
        }

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'",
        )
        return response
