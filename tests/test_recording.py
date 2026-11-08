"""
tests/test_recording.py — camera and microphone recording system.

Covers:
    1.  RecordingState constants and is_terminal()
    2.  build_output_path() — correct directory structure and filename
    3.  MediaMuxer — validation logic (no real files needed for most tests)
    4.  RecordingTimerWidget — elapsed update, minimum enforcement, signals
    5.  RecordingTimerWidget — format helper
    6.  RecordingService — probe_camera / probe_microphone (unit-level mocking)
    7.  RecordingService — initialise / state tracking
    8.  Session lifecycle — complete_session stores recording_path in DB
    9.  Session lifecycle — minimum duration enforcement in repository
    10. WordService.complete_practice — passes recording_path to DB
    11. PracticePage — navigate_to_recording signal emitted on start_practice
    12. RecordingPage — prepare() resets state; session_completed/abandoned signals

Hardware-dependent tests (real camera/mic) are skipped automatically when
the device is unavailable so the suite can run in CI environments.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Qt bootstrap (needed for any QObject / Signal tests)
# ---------------------------------------------------------------------------
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
from speakup.services.recording_service import (  # noqa: E402
    MediaMuxer,
    RecordingEngine,
    RecordingService,
    RecordingState,
    build_output_path,
    build_wav_path,
)
from speakup.config.settings import settings           # noqa: E402
from tests.conftest import make_user, make_word, make_session  # noqa: E402


# ===========================================================================
# 1. RecordingState constants
# ===========================================================================

class TestRecordingState:

    def test_all_constants_defined(self):
        assert RecordingState.IDLE         == "idle"
        assert RecordingState.INITIALISING == "initialising"
        assert RecordingState.READY        == "ready"
        assert RecordingState.COUNTDOWN    == "countdown"
        assert RecordingState.RECORDING    == "recording"
        assert RecordingState.STOPPING     == "stopping"
        assert RecordingState.COMPLETED    == "completed"
        assert RecordingState.ERROR        == "error"
        assert RecordingState.ABANDONED    == "abandoned"

    def test_terminal_states(self):
        assert RecordingState.is_terminal(RecordingState.COMPLETED)  is True
        assert RecordingState.is_terminal(RecordingState.ERROR)       is True
        assert RecordingState.is_terminal(RecordingState.ABANDONED)   is True

    def test_non_terminal_states(self):
        non_terminal = [
            RecordingState.IDLE,
            RecordingState.INITIALISING,
            RecordingState.READY,
            RecordingState.COUNTDOWN,
            RecordingState.RECORDING,
            RecordingState.STOPPING,
        ]
        for state in non_terminal:
            assert RecordingState.is_terminal(state) is False, \
                f"{state!r} should not be terminal"

    def test_unknown_state_not_terminal(self):
        assert RecordingState.is_terminal("unknown_state") is False


# ===========================================================================
# 2. build_output_path / build_wav_path
# ===========================================================================

class TestBuildOutputPath:

    def test_path_is_under_recordings_dir(self):
        path = build_output_path(42)
        assert str(settings.RECORDINGS_DIR) in str(path)

    def test_filename_contains_session_id(self):
        path = build_output_path(99)
        assert "session_99" in path.name

    def test_extension_is_mp4(self):
        path = build_output_path(1)
        assert path.suffix == ".mp4"

    def test_directory_structure_YYYY_MM(self):
        from datetime import datetime
        now = datetime.now()
        path = build_output_path(7)
        parts = path.parts
        year_str  = str(now.year)
        month_str = f"{now.month:02d}"
        assert year_str  in parts, f"Expected year {year_str} in {parts}"
        assert month_str in parts, f"Expected month {month_str} in {parts}"

    def test_wav_path_same_stem(self):
        mp4 = build_output_path(5)
        wav = build_wav_path(5)
        assert wav.stem   == mp4.stem
        assert wav.parent == mp4.parent
        assert wav.suffix == ".wav"

    def test_different_sessions_produce_different_paths(self):
        assert build_output_path(1) != build_output_path(2)

    def test_directory_created(self):
        path = build_output_path(999)
        assert path.parent.exists(), f"Directory was not created: {path.parent}"


# ===========================================================================
# 3. MediaMuxer
# ===========================================================================

class TestMediaMuxer:

    def test_mux_raises_when_video_missing(self):
        with pytest.raises(RuntimeError, match="Video file missing"):
            MediaMuxer.mux(
                Path("/nonexistent/video.mp4"),
                Path("/nonexistent/audio.wav"),
            )

    def test_mux_raises_when_video_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            empty_video = Path(f.name)
        try:
            with pytest.raises(RuntimeError, match="Video file missing or empty"):
                MediaMuxer.mux(empty_video, Path("/nonexistent/audio.wav"))
        finally:
            empty_video.unlink(missing_ok=True)

    def test_mux_returns_video_path_when_valid(self):
        with tempfile.NamedTemporaryFile(
            suffix=".mp4", delete=False, mode="wb"
        ) as vf:
            vf.write(b"\x00" * 100)   # non-empty fake video
            video_path = Path(vf.name)

        wav_path = video_path.with_suffix(".wav")
        wav_path.write_bytes(b"\x00" * 50)

        try:
            result = MediaMuxer.mux(video_path, wav_path)
            assert result == video_path
        finally:
            video_path.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)

    def test_mux_tolerates_missing_wav(self):
        """Video-only recordings (no mic) should still succeed."""
        with tempfile.NamedTemporaryFile(
            suffix=".mp4", delete=False, mode="wb"
        ) as vf:
            vf.write(b"\x00" * 100)
            video_path = Path(vf.name)

        try:
            result = MediaMuxer.mux(video_path, Path("/nonexistent.wav"))
            assert result == video_path
        finally:
            video_path.unlink(missing_ok=True)


# ===========================================================================
# 4. RecordingTimerWidget — logic (no display needed)
# ===========================================================================

class TestRecordingTimerWidget:

    @pytest.fixture
    def timer(self):
        from speakup.ui.widgets.recording_timer import RecordingTimerWidget
        return RecordingTimerWidget(min_duration=5)   # short for testing

    def test_initial_state(self, timer):
        assert timer.elapsed == 0
        assert timer.can_stop is False
        assert timer.minimum_reached_flag is False

    def test_reset_clears_state(self, timer):
        timer.start()
        timer.update_elapsed(10)
        timer.reset()
        assert timer.elapsed == 0
        assert timer.can_stop is False

    def test_can_stop_false_before_minimum(self, timer):
        timer.start()
        timer.update_elapsed(3)
        assert timer.can_stop is False

    def test_can_stop_true_after_minimum(self, timer):
        timer.start()
        timer.update_elapsed(5)
        assert timer.can_stop is True

    def test_minimum_reached_emitted_once(self, timer):
        reached_count = []
        timer.minimum_reached.connect(lambda: reached_count.append(1))
        timer.start()
        timer.update_elapsed(5)
        timer.update_elapsed(6)   # second call — should NOT re-emit
        timer.update_elapsed(7)
        assert len(reached_count) == 1, "minimum_reached should emit exactly once"

    def test_stop_allowed_signal_emitted(self, timer):
        allowed_values = []
        timer.stop_allowed.connect(lambda v: allowed_values.append(v))
        timer.start()
        timer.update_elapsed(5)
        assert True in allowed_values

    def test_elapsed_ignored_when_inactive(self, timer):
        # Not started — updates should be ignored
        timer.update_elapsed(100)
        assert timer.elapsed == 0

    def test_elapsed_tracked_when_active(self, timer):
        timer.start()
        timer.update_elapsed(3)
        assert timer.elapsed == 3

    def test_stop_deactivates_timer(self, timer):
        timer.start()
        timer.stop()
        timer.update_elapsed(10)
        assert timer.elapsed == 0   # reset by stop → reset

    def test_format_zero(self):
        from speakup.ui.widgets.recording_timer import RecordingTimerWidget
        assert RecordingTimerWidget._format(0)  == "00:00"

    def test_format_59_seconds(self):
        from speakup.ui.widgets.recording_timer import RecordingTimerWidget
        assert RecordingTimerWidget._format(59) == "00:59"

    def test_format_60_seconds(self):
        from speakup.ui.widgets.recording_timer import RecordingTimerWidget
        assert RecordingTimerWidget._format(60) == "01:00"

    def test_format_75_seconds(self):
        from speakup.ui.widgets.recording_timer import RecordingTimerWidget
        assert RecordingTimerWidget._format(75) == "01:15"

    def test_format_3600_seconds(self):
        from speakup.ui.widgets.recording_timer import RecordingTimerWidget
        assert RecordingTimerWidget._format(3600) == "60:00"

    def test_format_negative_clamped_to_zero(self):
        from speakup.ui.widgets.recording_timer import RecordingTimerWidget
        assert RecordingTimerWidget._format(-5) == "00:00"


class TestRecordingEngineControlRequests:

    @pytest.fixture
    def engine(self):
        return RecordingEngine(session_id=1, output_path=Path("recording.mp4"))

    def test_start_request_is_processed_by_preview_loop(self, engine):
        engine._state = RecordingState.READY
        with patch.object(engine, "_start_capture") as start_capture:
            engine.start_recording()

            assert engine._start_requested.is_set()
            assert engine._process_control_requests() is False
            start_capture.assert_called_once_with()
            assert engine._start_requested.is_set() is False

    def test_stop_request_is_processed_by_preview_loop(self, engine):
        engine._state = RecordingState.RECORDING
        with patch.object(
            engine, "_finish_capture", side_effect=engine._stop_requested.set
        ) as finish_capture:
            engine.stop_recording()

            assert engine._finish_requested.is_set()
            assert engine._process_control_requests() is True
            finish_capture.assert_called_once_with()
            assert engine._finish_requested.is_set() is False

    def test_abandon_request_stops_preview_loop(self, engine):
        engine.abandon()

        assert engine._process_control_requests() is True
        assert engine.state == RecordingState.ABANDONED


class TestTimerMaximumDuration:

    @pytest.fixture
    def timer(self):
        from speakup.ui.widgets.recording_timer import RecordingTimerWidget
        return RecordingTimerWidget(min_duration=60, max_duration=600)

    def test_label_shows_maximum(self, timer):
        assert "10:00 max" in timer._min_label.text()

    def test_no_maximum_label_when_cap_disabled(self):
        from speakup.ui.widgets.recording_timer import RecordingTimerWidget
        t = RecordingTimerWidget(min_duration=60, max_duration=0)
        assert "max" not in t._min_label.text()

    def test_no_warning_well_before_cap(self, timer):
        timer.start()
        timer.update_elapsed(300)
        assert "Minimum reached" in timer._status_label.text()

    def test_warns_before_automatic_stop(self, timer):
        timer.start()
        timer.update_elapsed(580)
        assert "stops automatically in 20s" in timer._status_label.text()


class TestMaximumDurationCap:

    @pytest.fixture
    def engine(self):
        return RecordingEngine(session_id=1, output_path=Path("recording.mp4"))

    def _run_recording_loop(self, engine, seconds_elapsed):
        import time
        import numpy as np

        engine._state = RecordingState.RECORDING
        engine._video_writer = None
        engine._recording_start = time.perf_counter() - seconds_elapsed
        engine._cap = MagicMock()
        engine._cap.read.return_value = (True, np.zeros((4, 4, 3), dtype=np.uint8))

        reads = {"n": 0}

        def _read():
            reads["n"] += 1
            if reads["n"] > 3:  # stop the loop if the cap did not trigger
                engine._stop_requested.set()
            return True, np.zeros((4, 4, 3), dtype=np.uint8)

        engine._cap.read.side_effect = _read
        with patch.object(
            engine, "_finish_capture", side_effect=engine._stop_requested.set
        ) as finish_capture:
            engine._preview_loop()
        return finish_capture

    def test_recording_stops_automatically_at_cap(self, engine, monkeypatch):
        monkeypatch.setattr(settings, "MAX_SPEAKING_DURATION", 600)
        finish_capture = self._run_recording_loop(engine, seconds_elapsed=601)
        finish_capture.assert_called_once_with()

    def test_recording_continues_below_cap(self, engine, monkeypatch):
        monkeypatch.setattr(settings, "MAX_SPEAKING_DURATION", 600)
        finish_capture = self._run_recording_loop(engine, seconds_elapsed=30)
        finish_capture.assert_not_called()

    def test_zero_disables_cap(self, engine, monkeypatch):
        monkeypatch.setattr(settings, "MAX_SPEAKING_DURATION", 0)
        finish_capture = self._run_recording_loop(engine, seconds_elapsed=10_000)
        finish_capture.assert_not_called()

    def test_default_cap_fits_openai_upload_limit(self):
        from speakup.services.transcription_providers import OpenAIWhisperProvider

        bytes_per_second = (
            RecordingEngine._AUDIO_SR * RecordingEngine._AUDIO_CHANNELS * 2  # int16
        )
        wav_bytes = 44 + bytes_per_second * settings.MAX_SPEAKING_DURATION
        assert settings.MAX_SPEAKING_DURATION > settings.MIN_SPEAKING_DURATION
        assert wav_bytes < OpenAIWhisperProvider._MAX_AUDIO_BYTES


# ===========================================================================
# 5. RecordingTimerWidget — minimum duration enforcement
# ===========================================================================

class TestMinimumDurationEnforcement:
    """
    These tests verify the business rule:
        Stop Recording is only allowed after MIN_SPEAKING_DURATION seconds.
    """

    @pytest.fixture
    def timer_60(self):
        from speakup.ui.widgets.recording_timer import RecordingTimerWidget
        return RecordingTimerWidget(min_duration=60)

    def test_cannot_stop_at_59_seconds(self, timer_60):
        timer_60.start()
        timer_60.update_elapsed(59)
        assert timer_60.can_stop is False

    def test_can_stop_at_exactly_60_seconds(self, timer_60):
        timer_60.start()
        timer_60.update_elapsed(60)
        assert timer_60.can_stop is True

    def test_can_stop_after_60_seconds(self, timer_60):
        timer_60.start()
        timer_60.update_elapsed(90)
        assert timer_60.can_stop is True

    def test_minimum_reached_flag_false_at_59(self, timer_60):
        timer_60.start()
        timer_60.update_elapsed(59)
        assert timer_60.minimum_reached_flag is False

    def test_minimum_reached_flag_true_at_60(self, timer_60):
        timer_60.start()
        timer_60.update_elapsed(60)
        assert timer_60.minimum_reached_flag is True

    def test_minimum_reached_signal_at_60(self, timer_60):
        fired = []
        timer_60.minimum_reached.connect(lambda: fired.append(1))
        timer_60.start()
        timer_60.update_elapsed(60)
        assert fired == [1]

    def test_minimum_not_reached_before_start(self, timer_60):
        """Elapsed updates before start() must be ignored."""
        timer_60.update_elapsed(60)
        assert timer_60.minimum_reached_flag is False


# ===========================================================================
# 6. RecordingService — probes (mocked)
# ===========================================================================

class TestRecordingServiceProbes:

    def test_probe_camera_returns_bool(self):
        svc = RecordingService()
        result = svc.probe_camera(0)
        assert isinstance(result, bool)

    def test_probe_camera_false_for_invalid_index(self):
        svc = RecordingService()
        # Camera index 99 should almost certainly not exist
        assert svc.probe_camera(99) is False

    def test_probe_microphone_returns_bool(self):
        svc = RecordingService()
        result = svc.probe_microphone()
        assert isinstance(result, bool)

    @patch("speakup.services.recording_service.cv2.VideoCapture")
    def test_probe_camera_returns_false_when_cap_fails(self, mock_cap_class):
        """Mock a camera that fails to open."""
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False
        mock_cap_class.return_value = mock_cap

        svc = RecordingService()
        assert svc.probe_camera(0) is False

    @patch("speakup.services.recording_service.cv2.VideoCapture")
    def test_probe_camera_returns_true_when_cap_succeeds(self, mock_cap_class):
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap_class.return_value = mock_cap

        svc = RecordingService()
        assert svc.probe_camera(0) is True

    @patch("speakup.services.recording_service.sd.query_devices")
    def test_probe_microphone_true_when_input_device_exists(self, mock_query):
        mock_query.return_value = [
            {"max_input_channels": 2, "name": "Microphone"},
        ]
        svc = RecordingService()
        assert svc.probe_microphone() is True

    @patch("speakup.services.recording_service.sd.query_devices")
    def test_probe_microphone_false_when_no_input_devices(self, mock_query):
        mock_query.return_value = [
            {"max_input_channels": 0, "name": "Speakers (output only)"},
        ]
        svc = RecordingService()
        assert svc.probe_microphone() is False

    @patch("speakup.services.recording_service.sd.query_devices")
    def test_probe_microphone_false_on_exception(self, mock_query):
        mock_query.side_effect = Exception("PortAudio not available")
        svc = RecordingService()
        assert svc.probe_microphone() is False

    def test_initial_state_is_idle(self):
        svc = RecordingService()
        assert svc.state == RecordingState.IDLE

    def test_is_recording_false_initially(self):
        svc = RecordingService()
        assert svc.is_recording is False

    def test_output_path_none_before_initialise(self):
        svc = RecordingService()
        assert svc.output_path is None

    def test_duration_zero_before_recording(self):
        svc = RecordingService()
        assert svc.duration_seconds == 0


# ===========================================================================
# 7. Session DB — complete_session stores recording_path
# ===========================================================================

class TestSessionRecordingPath:

    def test_complete_session_stores_recording_path(self, db_session):
        from speakup.database.repository import PracticeSessionRepository
        user = make_user(db_session, "RecPathUser")
        word = make_word(db_session, "RecPathWord")
        ps   = make_session(db_session, user.id, word.id)

        repo = PracticeSessionRepository(db_session)
        repo.complete_session(
            ps.id,
            duration_seconds=75,
            recording_path="/recordings/2026/09/session_1.mp4",
        )
        db_session.commit()

        db_session.refresh(ps)
        assert ps.recording_path == "/recordings/2026/09/session_1.mp4"
        assert ps.duration_seconds == 75

    def test_complete_session_without_recording_path(self, db_session):
        from speakup.database.repository import PracticeSessionRepository
        user = make_user(db_session, "NoPathUser")
        word = make_word(db_session, "NoPathWord")
        ps   = make_session(db_session, user.id, word.id)

        repo = PracticeSessionRepository(db_session)
        repo.complete_session(ps.id, duration_seconds=65)
        db_session.commit()

        db_session.refresh(ps)
        assert ps.recording_path is None
        assert ps.duration_seconds == 65

    def test_complete_session_sets_completed_at(self, db_session):
        from speakup.database.repository import PracticeSessionRepository
        user = make_user(db_session, "CompAtUser")
        word = make_word(db_session, "CompAtWord")
        ps   = make_session(db_session, user.id, word.id)

        repo = PracticeSessionRepository(db_session)
        repo.complete_session(ps.id, duration_seconds=70)
        db_session.commit()

        db_session.refresh(ps)
        assert ps.completed_at is not None

    def test_recording_path_persists_across_session_query(self, db_session):
        from speakup.database.repository import PracticeSessionRepository
        user = make_user(db_session, "PersistUser")
        word = make_word(db_session, "PersistWord")
        ps   = make_session(db_session, user.id, word.id)

        repo = PracticeSessionRepository(db_session)
        expected_path = "/recordings/2026/09/session_999.mp4"
        repo.complete_session(ps.id, duration_seconds=80, recording_path=expected_path)
        db_session.commit()

        # Re-fetch from DB
        fetched = repo.get_by_id(ps.id)
        assert fetched.recording_path == expected_path


# ===========================================================================
# 8. WordService.complete_practice — passes recording_path
# ===========================================================================

class TestWordServiceCompletePractice:

    def test_complete_practice_with_recording_path(self, engine):
        from speakup.services.word_service import WordService
        from speakup.database.connection import get_session
        from speakup.database.models import PracticeSession

        ws = WordService()
        ws.seed_words_if_empty()

        result = ws.assign_topic(user_id=1)
        sid    = result["session_id"]
        ws.start_practice(sid)

        path = "/recordings/2026/09/session_1.mp4"
        ok   = ws.complete_practice(
            session_id=sid,
            duration_seconds=75,
            recording_path=path,
        )
        assert ok is True

        # Verify recording_path was stored
        with get_session() as session:
            ps = session.get(PracticeSession, sid)
            assert ps is not None
            assert ps.recording_path == path
            assert ps.duration_seconds == 75

    def test_complete_practice_without_recording_path(self, engine):
        from speakup.services.word_service import WordService
        ws = WordService()
        ws.seed_words_if_empty()

        result = ws.assign_topic(user_id=1)
        sid    = result["session_id"]
        ws.start_practice(sid)
        ok     = ws.complete_practice(sid, duration_seconds=65)
        assert ok is True


# ===========================================================================
# 9. RecordingPage — unit-level signal tests (no hardware)
# ===========================================================================

class TestRecordingPageSignals:

    @pytest.fixture
    def recording_page(self):
        from speakup.ui.pages.recording_page import RecordingPage
        return RecordingPage()

    def test_prepare_sets_session_id(self, recording_page):
        topic = {
            "word": "Leadership",
            "category": "Business",
            "difficulty": "medium",
            "difficulty_display": "Intermediate",
            "description": "Test.",
        }
        with patch.object(
            recording_page._recording_svc, "probe_camera", return_value=False
        ), patch.object(
            recording_page._recording_svc, "probe_microphone", return_value=False
        ):
            recording_page.prepare(topic, session_id=42)

        assert recording_page._session_id == 42
        assert recording_page._topic == topic

    def test_prepare_with_camera_unavailable_sets_error_state(self, recording_page):
        topic = {"word": "Test", "category": "General", "difficulty": "easy",
                 "difficulty_display": "Beginner", "description": ""}
        with patch.object(
            recording_page._recording_svc, "probe_camera", return_value=False
        ), patch.object(
            recording_page._recording_svc, "probe_microphone", return_value=True
        ):
            recording_page.prepare(topic, session_id=1)

        from speakup.services.recording_service import RecordingState
        assert recording_page._ui_state == RecordingState.ERROR

    def test_session_abandoned_signal_emitted_on_abandon(self, recording_page):
        abandoned_ids = []
        recording_page.session_abandoned.connect(
            lambda sid: abandoned_ids.append(sid)
        )
        recording_page._session_id = 7
        # Patch service so abandon() doesn't actually start anything
        with patch.object(recording_page._recording_svc, "abandon"):
            recording_page._on_abandon()

        assert 7 in abandoned_ids

    def test_cleanup_does_not_raise(self, recording_page):
        """cleanup() must be safe to call even before prepare()."""
        recording_page.cleanup()   # should not raise


# ===========================================================================
# 10. PracticePage — navigate_to_recording signal
# ===========================================================================

class TestPracticePageSignal:

    def test_topic_shuffle_reveals_assigned_word(self):
        from speakup.ui.pages.practice_page import _TopicCard

        card = _TopicCard()
        topic = {
            "word": "Climate Change",
            "category": "Environment",
            "difficulty_display": "Intermediate",
        }

        card.show_topic(topic, animate=True)
        assert card._word_label.text() != topic["word"]
        assert card._shuffle_timer.isActive()

        for _ in range(card._shuffle_steps):
            card._advance_topic_shuffle()

        assert card._word_label.text() == topic["word"]
        assert not card._shuffle_timer.isActive()
        card.close()

    def test_navigate_to_recording_signal_exists(self):
        from speakup.ui.pages.practice_page import PracticePage
        pp = PracticePage()
        assert hasattr(pp, "navigate_to_recording")

    def test_on_recording_completed_resets_session_id(self):
        from speakup.ui.pages.practice_page import PracticePage
        pp = PracticePage()
        pp._current_session_id = 99
        pp._current_topic = {"word": "Test"}
        # Patch _load_topic so it doesn't hit the DB
        with patch.object(pp, "_load_topic"):
            pp.on_recording_completed()
        assert pp._current_session_id is None

    def test_on_recording_abandoned_resets_session_id(self):
        from speakup.ui.pages.practice_page import PracticePage
        pp = PracticePage()
        pp._current_session_id = 42
        pp._current_topic = {"word": "Test"}
        with patch.object(pp, "_load_topic"):
            pp.on_recording_abandoned()
        assert pp._current_session_id is None
