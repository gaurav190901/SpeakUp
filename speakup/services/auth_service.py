"""Local email/password authentication for SpeakUp profiles."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

from speakup.database.connection import get_session
from speakup.database.repository import UserCredentialRepository, UserRepository

_HASH_SCHEME = "pbkdf2_sha256"
_HASH_ITERATIONS = 600_000
_EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _HASH_ITERATIONS
    )
    return "$".join((
        _HASH_SCHEME,
        str(_HASH_ITERATIONS),
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    ))


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        scheme, iteration_text, salt_text, digest_text = encoded_hash.split("$", 3)
        if scheme != _HASH_SCHEME:
            return False
        iterations = int(iteration_text)
        if not 100_000 <= iterations <= 2_000_000:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(actual, expected)


class AuthenticationService:
    """Register and authenticate local accounts without a network service."""

    def register(
        self, display_name: str, email: str, password: str
    ) -> tuple[int, str]:
        display_name = display_name.strip()
        email = email.strip().casefold()

        if not display_name or len(display_name) > 100:
            raise ValueError("Display name must be between 1 and 100 characters.")
        if len(email) > 254 or not _EMAIL_PATTERN.fullmatch(email):
            raise ValueError("Enter a valid email address.")
        if not 10 <= len(password) <= 128:
            raise ValueError("Password must be between 10 and 128 characters.")

        password_hash = hash_password(password)
        with get_session() as session:
            credential_repository = UserCredentialRepository(session)
            if credential_repository.get_by_email(email):
                raise ValueError("An account with this email already exists.")

            user_repository = UserRepository(session)
            user = user_repository.get_unclaimed_by_name(display_name)
            if user is None:
                user = user_repository.create(display_name)

            credential_repository.create(user.id, email, password_hash)
            return user.id, user.name

    def login(self, email: str, password: str) -> tuple[int, str] | None:
        email = email.strip().casefold()
        if not 10 <= len(password) <= 128:
            return None
        with get_session() as session:
            credential = UserCredentialRepository(session).get_by_email(email)
            if credential is None or not verify_password(
                password, credential.password_hash
            ):
                return None
            return credential.user_id, credential.user.name