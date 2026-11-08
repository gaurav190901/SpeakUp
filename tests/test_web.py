"""
tests/test_web.py — the browser version of SpeakUp (Flask app).

Covers accounts, CSRF protection, page rendering, the record → transcribe →
analyse pipeline (with the mock transcriber) and per-user access control.
"""

from __future__ import annotations

import io
import math
import struct
import wave

import pytest

from speakup.config.settings import settings
from speakup.services.word_service import WordService
from speakup.web import create_app, processing

PASSWORD = "GoodPassword123"


@pytest.fixture()
def app(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TRANSCRIPTION_BACKEND", "mock")
    monkeypatch.setattr(settings, "RECORDINGS_DIR", tmp_path / "recordings")
    processing.reset_transcriber()
    WordService().seed_words_if_empty()
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "PROCESS_RECORDINGS_IN_BACKGROUND": False,
    })
    yield app
    processing.reset_transcriber()


@pytest.fixture()
def client(app):
    return app.test_client()


def _csrf(client) -> str:
    client.get("/")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _signup(client, name="Asha", email="asha@example.com"):
    token = _csrf(client)
    return client.post("/signup", data={
        "csrf_token": token, "name": name, "email": email,
        "password": PASSWORD, "confirm": PASSWORD,
    })


def _wav_bytes(seconds: float = 2.0, rate: int = 16_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(int(seconds * rate))
        )
        wav.writeframes(frames)
    return buf.getvalue()


def _current_session_id(client) -> int:
    with client.session_transaction() as sess:
        return int(sess["practice_session_id"])


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

class TestAccounts:

    def test_landing_page_shows_sign_in(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert b"Speak English" in res.data
        assert b"Welcome back" in res.data

    def test_landing_signup_mode(self, client):
        res = client.get("/?mode=signup")
        assert b"Create your account" in res.data

    def test_signup_signs_in_and_shows_welcome(self, client):
        res = _signup(client)
        assert res.status_code == 302
        page = client.get("/dashboard")
        assert page.status_code == 200
        assert b"Asha" in page.data
        assert b"How SpeakUp works" in page.data

    def test_signup_validation_error_is_friendly(self, client):
        token = _csrf(client)
        res = client.post("/signup", data={
            "csrf_token": token, "name": "A", "email": "a@example.com",
            "password": PASSWORD, "confirm": "Different12345",
        })
        assert res.status_code == 400
        assert b"don&#39;t match" in res.data

    def test_login_and_logout(self, client):
        _signup(client)
        token = _csrf(client)
        client.post("/logout", data={"csrf_token": token})
        assert client.get("/dashboard").status_code == 302

        token = _csrf(client)
        bad = client.post("/login", data={"csrf_token": token, "email": "asha@example.com",
                                          "password": "WrongPassword1"})
        assert bad.status_code == 400
        good = client.post("/login", data={"csrf_token": token, "email": "asha@example.com",
                                           "password": PASSWORD})
        assert good.status_code == 302
        assert client.get("/dashboard").status_code == 200

    def test_post_without_csrf_token_is_rejected(self, client):
        res = client.post("/login", data={"email": "x@example.com", "password": PASSWORD})
        assert res.status_code == 400

    def test_pages_require_sign_in(self, client):
        for path in ("/dashboard", "/practice", "/history", "/settings"):
            assert client.get(path).status_code == 302
        assert client.get("/api/sessions/1/status").status_code == 401

    def test_security_headers(self, client):
        res = client.get("/")
        assert res.headers["X-Frame-Options"] == "DENY"
        assert "script-src 'self'" in res.headers["Content-Security-Policy"]


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

class TestPages:

    def test_all_pages_render(self, client):
        _signup(client)
        for path in ("/dashboard", "/practice", "/history", "/settings", "/dashboard?range=7d"):
            res = client.get(path)
            assert res.status_code == 200, path

    def test_practice_shows_topic_with_hints(self, client):
        _signup(client)
        res = client.get("/practice")
        assert b"Your topic" in res.data
        assert b"Ideas to talk about" in res.data
        assert b"Start recording" in res.data

    def test_practice_keeps_same_topic_on_reload(self, client):
        _signup(client)
        client.get("/practice")
        first = _current_session_id(client)
        client.get("/practice")
        assert _current_session_id(client) == first

    def test_new_topic_with_filters(self, client):
        _signup(client)
        client.get("/practice")
        first = _current_session_id(client)
        token = _csrf(client)
        res = client.post("/practice/new", data={"csrf_token": token,
                                                 "category": "Food", "level": "Beginner"})
        assert res.status_code == 302
        assert _current_session_id(client) != first
        page = client.get("/practice")
        assert b"Food" in page.data
        assert b"Beginner" in page.data

    def test_settings_save_preferences(self, client):
        _signup(client)
        token = _csrf(client)
        res = client.post("/settings", data={"csrf_token": token,
                                             "difficulty": "hard", "goal": "minutes_10"},
                          follow_redirects=True)
        assert b"preferences were saved" in res.data
        assert b'value="minutes_10" selected' in res.data

    def test_unknown_page_is_404(self, client):
        assert client.get("/no-such-page").status_code == 404


# ---------------------------------------------------------------------------
# Recording pipeline
# ---------------------------------------------------------------------------

class TestRecordingFlow:

    def _record(self, client, data=None):
        client.get("/practice")
        sid = _current_session_id(client)
        token = _csrf(client)
        headers = {"X-CSRF-Token": token}
        assert client.post(f"/api/sessions/{sid}/start", headers=headers).status_code == 200
        res = client.post(
            f"/api/sessions/{sid}/recording", headers=headers,
            data=data or {"audio": (io.BytesIO(_wav_bytes()), "recording.wav", "audio/wav"),
                          "duration": "2.0"},
            content_type="multipart/form-data",
        )
        return sid, res

    def test_full_flow_produces_results(self, client):
        _signup(client)
        sid, res = self._record(client)
        assert res.status_code == 200
        assert res.get_json()["redirect"].endswith(f"/sessions/{sid}/processing")

        status = client.get(f"/api/sessions/{sid}/status").get_json()
        assert status["stage"] == "done"
        assert status["redirect"].endswith(f"/sessions/{sid}")

        page = client.get(f"/sessions/{sid}")
        assert page.status_code == 200
        assert b"mock transcript" in page.data
        assert b"What went well" in page.data

        assert client.get("/history").data.count(f"/sessions/{sid}".encode()) == 1
        assert b"Practise now" in client.get("/dashboard").data

    def test_recording_is_saved_as_wav(self, client):
        _signup(client)
        sid, _ = self._record(client)
        wavs = list(settings.RECORDINGS_DIR.rglob(f"session_{sid}.wav"))
        assert len(wavs) == 1

    def test_upload_without_audio_is_rejected(self, client):
        _signup(client)
        sid, res = self._record(client, data={"duration": "1"})
        assert res.status_code == 400

    def test_cannot_upload_twice(self, client):
        _signup(client)
        sid, _ = self._record(client)
        token = _csrf(client)
        again = client.post(
            f"/api/sessions/{sid}/recording", headers={"X-CSRF-Token": token},
            data={"audio": (io.BytesIO(_wav_bytes()), "r.wav", "audio/wav")},
            content_type="multipart/form-data",
        )
        assert again.status_code == 409

    def test_empty_transcript_reports_error(self, client, monkeypatch):
        from speakup.services.transcription_providers import MockTranscriptionProvider
        from speakup.services.transcription_service import TranscriptionService
        silent = TranscriptionService(provider=MockTranscriptionProvider(empty_result=True))
        monkeypatch.setattr(processing, "_get_transcriber", lambda: silent)
        _signup(client)
        sid, _ = self._record(client)
        status = client.get(f"/api/sessions/{sid}/status").get_json()
        assert status["stage"] == "error"
        assert "couldn't hear any speech" in status["message"]

    def test_undecodable_audio_reports_error(self, client):
        _signup(client)
        sid, res = self._record(client, data={
            "audio": (io.BytesIO(b"not really audio"), "r.webm", "audio/webm"), "duration": "3"})
        assert res.status_code == 200
        status = client.get(f"/api/sessions/{sid}/status").get_json()
        assert status["stage"] == "error"
        assert "couldn't read that recording" in status["message"]

    def test_abandon_returns_to_practice(self, client):
        _signup(client)
        client.get("/practice")
        sid = _current_session_id(client)
        token = _csrf(client)
        res = client.post(f"/api/sessions/{sid}/abandon", headers={"X-CSRF-Token": token})
        assert res.get_json()["redirect"].endswith("/practice")


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

class TestIsolation:

    def test_users_cannot_see_each_others_sessions(self, app):
        alice, bob = app.test_client(), app.test_client()
        _signup(alice, "Alice", "alice@example.com")
        _signup(bob, "Bob", "bob@example.com")

        sid, _ = TestRecordingFlow()._record(alice)
        assert alice.get(f"/sessions/{sid}").status_code == 200

        assert bob.get(f"/sessions/{sid}").status_code == 404
        assert bob.get(f"/api/sessions/{sid}/status").status_code == 404
        token = _csrf(bob)
        assert bob.post(f"/api/sessions/{sid}/abandon",
                        headers={"X-CSRF-Token": token}).status_code == 404
        assert f"/sessions/{sid}".encode() not in bob.get("/history").data


class TestHelpers:

    def test_parse_feedback_text(self):
        from speakup.web.results import parse_feedback_text
        text = "Strengths:\n• Clear\n\nAreas to improve:\n• Fewer fillers\nSuggestions:\n• Slow down"
        assert parse_feedback_text(text) == {
            "strengths": ["Clear"], "improvements": ["Fewer fillers"], "suggestions": ["Slow down"],
        }
        assert parse_feedback_text(None)["strengths"] == []

    def test_score_band(self):
        from speakup.web.results import score_band
        assert score_band(None) == "none"
        assert score_band(80) == "good"
        assert score_band(60) == "ok"
        assert score_band(10) == "low"
