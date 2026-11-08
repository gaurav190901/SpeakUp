"""
SpeakUp web application.

A Flask app that serves SpeakUp in the browser on this computer. It reuses
the same services as the desktop app (topics, transcription, analysis,
progress, gamification), so both share one database and one set of rules.

Run it with ``python webapp.py``.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from flask import Flask

from speakup.config.settings import settings


def _load_secret_key() -> str:
    """Return a persistent secret key for signing session cookies.

    Stored in the per-user data folder so sign-ins survive restarts and the
    key is never part of the source code or the git repository.
    """
    key_file = Path(settings.APP_DATA_DIR) / "web_secret.key"
    try:
        if key_file.exists():
            key = key_file.read_text(encoding="utf-8").strip()
            if key:
                return key
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_hex(32)
        key_file.write_text(key, encoding="utf-8")
        return key
    except OSError:
        # Fall back to a per-process key; users just need to sign in again
        # after a restart.
        return secrets.token_hex(32)


def create_app(config: dict | None = None) -> Flask:
    """Application factory."""
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=None,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=60 * 1024 * 1024,   # recordings up to 60 MB
        PROCESS_RECORDINGS_IN_BACKGROUND=True,
    )
    if config:
        app.config.update(config)
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = _load_secret_key()

    from speakup.web import auth, views  # noqa: PLC0415

    auth.init_app(app)
    app.register_blueprint(views.bp)
    return app
