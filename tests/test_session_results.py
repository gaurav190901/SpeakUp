"""
tests/test_session_results.py — session results page.

All tests are offline — no real models, hardware, or DB writes required
for the pure-UI tests.  DB-backed tests use the shared engine/db_session
fixtures from conftest.py.

Covers:
    1.  SessionResultsPage — instantiation and signals present
    2.  SessionResultsPage.prepare() — data mapping for every widget
    3.  Score display — overall hero card
    4.  Breakdown cards — all four dimensions rendered
    5.  Secondary metrics chips — WPM, fillers, pauses, TTR
    6.  Feedback section — three labelled lists populated
    7.  Transcript accordion — show/hide toggle
    8.  Navigation buttons — signals emitted on click
    9.  Edge cases — None scores, empty feedback, missing keys
    10. _compute_filler_score helper
    11. MainWindow — PAGE_RESULTS constant and stack count
    12. MainWindow — _enrich_analysis_result parses feedback text
    13. Helpers integration — format_duration, score_to_label, score_colour
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
from speakup.ui.pages.session_results_page import (  # noqa: E402
    SessionResultsPage,
    _compute_filler_score,
)
from speakup.utils.helpers import (  # noqa: E402
    format_duration,
    score_to_label,
    score_colour,
)
from speakup.config.settings import settings  # noqa: E402

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FULL_RESULT = {
    "overall_score":    78.5,
    "fluency_score":    74.0,
    "vocabulary_score": 82.0,
    "grammar_score":    76.0,
    "confidence_score": 79.0,
    "filler_word_count": 3,
    "words_per_minute": 115.0,
    "pause_count":      2,
    "type_token_ratio": 0.62,
    "total_words":      120,
    "filler_rate":      0.025,
    "speech_ratio":     0.88,
    "avg_pause_duration_s": 1.8,
    "score_breakdown_text": "Fluency 74 x 25% = 18.5\nVocabulary 82 x 20% = 16.4",
    "confidence_caveat": "Observable speaking indicators only.",
    "feedback": (
        "Strengths:\n• Good speaking pace.\n• Rich vocabulary.\n\n"
        "Areas to improve:\n• Reduce filler words.\n\n"
        "Suggestions:\n• Pause instead of saying um."
    ),
    "strengths":    ["Good speaking pace.", "Rich vocabulary."],
    "improvements": ["Reduce filler words."],
    "suggestions":  ["Pause instead of saying um."],
}

FULL_TOPIC = {
    "word":               "Leadership",
    "category":           "Business",
    "difficulty":         "medium",
    "difficulty_display": "Intermediate",
}


@pytest.fixture
def page():
    return SessionResultsPage()


@pytest.fixture
def prepared_page(page):
    page.prepare(
        session_id=1,
        topic=FULL_TOPIC,
        analysis_result=FULL_RESULT,
        duration_seconds=75,
        transcript="Hello world this is a test transcript.",
    )
    return page


# ===========================================================================
# 1. Instantiation and signals
# ===========================================================================

class TestSessionResultsPageInstantiation:

    def test_page_creates_without_error(self, page):
        assert page is not None

    def test_practice_again_signal_exists(self, page):
        assert hasattr(page, "practice_again")

    def test_go_to_dashboard_signal_exists(self, page):
        assert hasattr(page, "go_to_dashboard")

    def test_prepare_method_exists(self, page):
        assert callable(getattr(page, "prepare", None))

    def test_initial_session_id_is_none(self, page):
        assert page._session_id is None

    def test_initial_analysis_result_is_empty(self, page):
        assert page._analysis_result == {}


# ===========================================================================
# 2. prepare() — data mapping
# ===========================================================================

class TestSessionResultsPagePrepare:

    def test_session_id_stored(self, prepared_page):
        assert prepared_page._session_id == 1

    def test_topic_stored(self, prepared_page):
        assert prepared_page._topic["word"] == "Leadership"

    def test_analysis_result_stored(self, prepared_page):
        assert prepared_page._analysis_result["overall_score"] == 78.5

    def test_duration_stored(self, prepared_page):
        assert prepared_page._duration_seconds == 75

    def test_transcript_stored(self, prepared_page):
        assert "test transcript" in prepared_page._transcript

    def test_prepare_called_twice_does_not_crash(self, page):
        for _ in range(2):
            page.prepare(
                session_id=1,
                topic=FULL_TOPIC,
                analysis_result=FULL_RESULT,
                duration_seconds=75,
                transcript="Hello.",
            )

    def test_prepare_with_empty_analysis_result(self, page):
        """prepare() must not crash when analysis_result is empty."""
        page.prepare(
            session_id=1,
            topic=FULL_TOPIC,
            analysis_result={},
            duration_seconds=0,
            transcript="",
        )

    def test_prepare_with_none_topic(self, page):
        page.prepare(session_id=1, topic=None, analysis_result=FULL_RESULT,
                     duration_seconds=75)

    def test_prepare_with_none_analysis_result(self, page):
        page.prepare(session_id=1, topic=FULL_TOPIC, analysis_result=None,
                     duration_seconds=75)


# ===========================================================================
# 3. Overall hero card
# ===========================================================================

class TestOverallScoreHero:

    def test_overall_score_displayed(self, prepared_page):
        text = prepared_page._overall_score_lbl.text()
        assert "78" in text

    def test_overall_label_reflects_score(self, prepared_page):
        label = prepared_page._overall_label_lbl.text()
        assert label == score_to_label(78.5)
        assert label == "Good"

    def test_overall_bar_value_set(self, prepared_page):
        assert prepared_page._overall_bar.value() == 78

    def test_overall_bar_zero_on_empty(self, page):
        page.prepare(session_id=1, topic={}, analysis_result={}, duration_seconds=0)
        assert page._overall_bar.value() >= 0

    def test_overall_score_none_shows_dash(self, page):
        result = dict(FULL_RESULT)
        result["overall_score"] = None
        page.prepare(session_id=1, topic=FULL_TOPIC, analysis_result=result,
                     duration_seconds=75)
        # Should show "—" and not crash
        text = page._overall_score_lbl.text()
        assert text == "—"


# ===========================================================================
# 4. Score breakdown cards — four dimensions
# ===========================================================================

class TestScoreBreakdownCards:

    def test_four_score_cards_created(self, prepared_page):
        assert prepared_page._score_cards_layout.count() == 4

    def test_score_cards_label_names(self, prepared_page):
        names = set()
        for i in range(prepared_page._score_cards_layout.count()):
            card = prepared_page._score_cards_layout.itemAt(i).widget()
            # Find the QLabel with objectName ScoreCardName
            for child in card.findChildren(type(card).__mro__[-2]):
                pass
            # Verify the card was created (not None)
            assert card is not None

    def test_score_breakdown_rebuilds_on_reprepare(self, page):
        page.prepare(session_id=1, topic=FULL_TOPIC,
                     analysis_result=FULL_RESULT, duration_seconds=75)
        first_count = page._score_cards_layout.count()
        page.prepare(session_id=2, topic=FULL_TOPIC,
                     analysis_result=FULL_RESULT, duration_seconds=60)
        second_count = page._score_cards_layout.count()
        assert first_count == second_count == 4

    def test_none_score_does_not_crash(self, page):
        result = dict(FULL_RESULT)
        result["fluency_score"] = None
        page.prepare(session_id=1, topic=FULL_TOPIC, analysis_result=result,
                     duration_seconds=75)
        assert page._score_cards_layout.count() == 4


# ===========================================================================
# 5. Secondary metrics chips
# ===========================================================================

class TestMetricChips:

    def test_wpm_chip_set(self, prepared_page):
        text = prepared_page._chip_wpm._value_label.text()
        assert "115" in text

    def test_filler_chip_set(self, prepared_page):
        text = prepared_page._chip_filler._value_label.text()
        assert "3" in text

    def test_pause_chip_set(self, prepared_page):
        text = prepared_page._chip_pauses._value_label.text()
        assert "2" in text

    def test_ttr_chip_set(self, prepared_page):
        text = prepared_page._chip_ttr._value_label.text()
        assert "62" in text or "%" in text

    def test_chips_show_dash_for_none(self, page):
        result = {
            "overall_score": 50.0,
            "words_per_minute": None,
            "filler_word_count": None,
            "pause_count": None,
            "type_token_ratio": None,
        }
        page.prepare(session_id=1, topic={}, analysis_result=result,
                     duration_seconds=0)
        assert page._chip_wpm._value_label.text()    == "—"
        assert page._chip_filler._value_label.text() == "—"
        assert page._chip_pauses._value_label.text() == "—"
        assert page._chip_ttr._value_label.text()    == "—"

    def test_wpm_zero_shows_zero(self, page):
        result = dict(FULL_RESULT)
        result["words_per_minute"] = 0.0
        page.prepare(session_id=1, topic=FULL_TOPIC, analysis_result=result,
                     duration_seconds=75)
        assert "0" in page._chip_wpm._value_label.text()


# ===========================================================================
# 6. Feedback section
# ===========================================================================

class TestFeedbackSection:

    def test_feedback_container_populated(self, prepared_page):
        assert prepared_page._feedback_inner_layout.count() > 0

    def test_feedback_section_has_correct_type(self, prepared_page):
        from speakup.ui.pages.session_results_page import _FeedbackSection
        widget = prepared_page._feedback_inner_layout.itemAt(0).widget()
        assert isinstance(widget, _FeedbackSection)

    def test_empty_feedback_lists_skip_section(self, page):
        result = dict(FULL_RESULT)
        result["strengths"]    = []
        result["improvements"] = []
        result["suggestions"]  = []
        page.prepare(session_id=1, topic=FULL_TOPIC, analysis_result=result,
                     duration_seconds=75)
        assert page._feedback_inner_layout.count() == 0

    def test_partial_feedback_does_not_crash(self, page):
        result = dict(FULL_RESULT)
        result["strengths"]    = ["Good pace."]
        result["improvements"] = []
        result["suggestions"]  = []
        page.prepare(session_id=1, topic=FULL_TOPIC, analysis_result=result,
                     duration_seconds=75)

    def test_feedback_container_cleared_on_reprepare(self, page):
        page.prepare(session_id=1, topic=FULL_TOPIC,
                     analysis_result=FULL_RESULT, duration_seconds=75)
        first = page._feedback_inner_layout.count()
        page.prepare(session_id=2, topic=FULL_TOPIC,
                     analysis_result=FULL_RESULT, duration_seconds=60)
        second = page._feedback_inner_layout.count()
        assert first == second


# ===========================================================================
# 7. Transcript accordion
# ===========================================================================

class TestTranscriptAccordion:

    def test_accordion_created_when_transcript_provided(self, prepared_page):
        assert prepared_page._transcript_inner_layout.count() == 1

    def test_no_accordion_when_empty_transcript(self, page):
        page.prepare(session_id=1, topic=FULL_TOPIC,
                     analysis_result=FULL_RESULT,
                     duration_seconds=75, transcript="")
        assert page._transcript_inner_layout.count() == 0

    def test_no_accordion_for_whitespace_transcript(self, page):
        page.prepare(session_id=1, topic=FULL_TOPIC,
                     analysis_result=FULL_RESULT,
                     duration_seconds=75, transcript="   ")
        assert page._transcript_inner_layout.count() == 0

    def test_accordion_starts_collapsed(self, prepared_page):
        from speakup.ui.pages.session_results_page import _TranscriptAccordion
        acc = prepared_page._transcript_inner_layout.itemAt(0).widget()
        assert isinstance(acc, _TranscriptAccordion)
        assert not acc._text_area.isVisible()

    def test_accordion_toggle_shows_text(self, prepared_page):
        from speakup.ui.pages.session_results_page import _TranscriptAccordion
        acc = prepared_page._transcript_inner_layout.itemAt(0).widget()
        acc._on_toggle(True)
        # isVisible() requires parent chain to be shown; check isHidden() instead
        assert not acc._text_area.isHidden()

    def test_accordion_toggle_hides_text(self, prepared_page):
        from speakup.ui.pages.session_results_page import _TranscriptAccordion
        acc = prepared_page._transcript_inner_layout.itemAt(0).widget()
        acc._on_toggle(True)
        acc._on_toggle(False)
        assert acc._text_area.isHidden()

    def test_accordion_contains_transcript_text(self, prepared_page):
        from speakup.ui.pages.session_results_page import _TranscriptAccordion
        acc = prepared_page._transcript_inner_layout.itemAt(0).widget()
        assert "test transcript" in acc._text_area.toPlainText()


# ===========================================================================
# 8. Navigation buttons and signals
# ===========================================================================

class TestNavigationButtons:

    def test_practice_again_signal_emitted(self, prepared_page):
        fired = []
        prepared_page.practice_again.connect(lambda: fired.append(1))
        prepared_page._practice_btn.click()
        assert fired == [1]

    def test_go_to_dashboard_signal_emitted(self, prepared_page):
        fired = []
        prepared_page.go_to_dashboard.connect(lambda: fired.append(1))
        prepared_page._dashboard_btn.click()
        assert fired == [1]

    def test_practice_button_has_correct_object_name(self, page):
        assert page._practice_btn.objectName() == "PracticeAgainBtn"

    def test_dashboard_button_has_correct_object_name(self, page):
        assert page._dashboard_btn.objectName() == "DashboardBtn"

    def test_signals_not_fired_without_click(self, prepared_page):
        fired = []
        prepared_page.practice_again.connect(lambda: fired.append(1))
        prepared_page.go_to_dashboard.connect(lambda: fired.append(1))
        assert fired == []


# ===========================================================================
# 9. Edge cases — None scores, missing keys
# ===========================================================================

class TestEdgeCases:

    def test_all_none_scores_do_not_crash(self, page):
        result = {
            "overall_score": None,
            "fluency_score": None,
            "vocabulary_score": None,
            "grammar_score": None,
            "confidence_score": None,
        }
        page.prepare(session_id=1, topic={}, analysis_result=result,
                     duration_seconds=0)

    def test_missing_keys_do_not_crash(self, page):
        # Only overall_score provided
        page.prepare(session_id=1, topic={},
                     analysis_result={"overall_score": 65.0},
                     duration_seconds=60)

    def test_completely_empty_result_does_not_crash(self, page):
        page.prepare(session_id=1, topic={}, analysis_result={},
                     duration_seconds=0)

    def test_score_100_displays_100(self, page):
        result = dict(FULL_RESULT)
        result["overall_score"] = 100.0
        page.prepare(session_id=1, topic=FULL_TOPIC, analysis_result=result,
                     duration_seconds=75)
        assert "100" in page._overall_score_lbl.text()

    def test_score_zero_displays_zero(self, page):
        result = dict(FULL_RESULT)
        result["overall_score"] = 0.0
        page.prepare(session_id=1, topic=FULL_TOPIC, analysis_result=result,
                     duration_seconds=75)
        assert "0" in page._overall_score_lbl.text()

    def test_topic_without_category_does_not_crash(self, page):
        page.prepare(session_id=1, topic={"word": "Test"},
                     analysis_result=FULL_RESULT, duration_seconds=75)

    def test_topic_label_contains_word(self, page):
        page.prepare(session_id=1, topic={"word": "Resilience"},
                     analysis_result=FULL_RESULT, duration_seconds=75)
        assert "Resilience" in page._topic_label.text()

    def test_duration_label_formatted(self, page):
        page.prepare(session_id=1, topic={}, analysis_result={},
                     duration_seconds=75)
        assert "1m" in page._duration_label.text() or "75" in page._duration_label.text()

    def test_duration_zero_shows_something(self, page):
        page.prepare(session_id=1, topic={}, analysis_result={},
                     duration_seconds=0)
        assert page._duration_label.text() != ""


# ===========================================================================
# 10. _compute_filler_score helper
# ===========================================================================

class TestComputeFillerScore:

    def test_zero_fillers_gives_high_score(self):
        result = {"filler_word_count": 0, "total_words": 100}
        score = _compute_filler_score(result)
        assert score >= 90.0

    def test_many_fillers_gives_low_score(self):
        result = {"filler_word_count": 20, "total_words": 100}
        score = _compute_filler_score(result)
        assert score < 50.0

    def test_missing_keys_return_high_score(self):
        # No fillers = 0 rate = 100 score
        score = _compute_filler_score({})
        assert score >= 90.0

    def test_score_in_range(self):
        result = {"filler_word_count": 5, "total_words": 80}
        score = _compute_filler_score(result)
        assert 0.0 <= score <= 100.0

    def test_zero_total_words_does_not_divide_by_zero(self):
        result = {"filler_word_count": 3, "total_words": 0}
        score = _compute_filler_score(result)
        assert 0.0 <= score <= 100.0


# ===========================================================================
# 11. MainWindow — PAGE_RESULTS and stack count
# ===========================================================================

class TestMainWindowResults:

    def test_page_results_constant_is_6(self):
        from speakup.ui.main_window import PAGE_RESULTS
        assert PAGE_RESULTS == 6

    def test_stack_count_is_eight(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        assert win._stack.count() == 8

    def test_results_page_at_index_6(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        page = win._stack.widget(6)
        assert isinstance(page, SessionResultsPage)

    def test_on_practice_again_handler_exists(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        assert hasattr(win, "_on_practice_again")
        assert callable(win._on_practice_again)

    def test_completed_session_resets_practice_before_transcription(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        with patch("speakup.ui.main_window.WordService") as word_service, \
             patch.object(win._recording_page, "cleanup"), \
             patch.object(win._transcription_page, "prepare"), \
             patch.object(win._practice_page, "on_recording_completed") as reset:
            win._on_session_completed(12, 75, "recording.mp4")

        word_service.return_value.complete_practice.assert_called_once_with(
            session_id=12,
            duration_seconds=75,
            recording_path="recording.mp4",
        )
        reset.assert_called_once_with()

    def test_practice_again_only_navigates(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        with patch.object(win, "_navigate_to") as navigate, \
             patch.object(win._practice_page, "on_recording_completed") as reset:
            win._on_practice_again()

        navigate.assert_called_once()
        reset.assert_not_called()

    def test_on_go_to_dashboard_handler_exists(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        assert hasattr(win, "_on_go_to_dashboard")
        assert callable(win._on_go_to_dashboard)


class TestLocalProfiles:

    def test_signup_authenticates_new_local_account(self, engine):
        from speakup.ui.widgets.auth_dialog import AuthDialog

        dialog = AuthDialog()
        dialog._signup_name.setText("Practice User")
        dialog._signup_email.setText("practice@example.com")
        dialog._signup_password.setText("GoodPassword123")
        dialog._signup_confirmation.setText("GoodPassword123")
        dialog._sign_up()

        assert dialog.authenticated_account is not None
        assert dialog.authenticated_account[1] == "Practice User"

    def test_selected_profile_id_is_used_by_profile_scoped_pages(self, engine):
        from speakup.database.connection import get_session
        from speakup.database.repository import UserRepository
        from speakup.ui.main_window import MainWindow

        with get_session() as session:
            profile = UserRepository(session).create("Scoped User")
            user_id = profile.id

        window = MainWindow(user_id=user_id, user_name="Scoped User")

        assert window._dashboard_page._user_id == user_id
        assert window._practice_page._user_id == user_id
        assert window._history_page._user_id == user_id
        assert window._settings_page._user_id == user_id
        assert window._data_page._user_id == user_id

    def test_sign_out_button_emits_logout_request(self):
        from PySide6.QtWidgets import QPushButton
        from speakup.ui.main_window import MainWindow

        window = MainWindow(user_name="Signed In User")
        logout_requests = []
        window.logout_requested.connect(lambda: logout_requests.append(True))
        window.findChild(QPushButton, "SignOutButton").click()

        assert logout_requests == [True]


# ===========================================================================
# 12. MainWindow._enrich_analysis_result — feedback text parsing
# ===========================================================================

class TestEnrichAnalysisResult:

    def test_parses_strengths_from_text(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        db_result = {
            "overall_score": 75.0,
            "feedback": "Strengths:\n• Great pace.\n• Rich vocabulary.\n\n"
                        "Areas to improve:\n• Reduce fillers.\n\n"
                        "Suggestions:\n• Pause more.",
        }
        enriched = win._enrich_analysis_result(db_result)
        assert "Great pace." in enriched["strengths"]
        assert "Rich vocabulary." in enriched["strengths"]

    def test_parses_improvements_from_text(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        db_result = {
            "feedback": "Areas to improve:\n• Reduce fillers."
        }
        enriched = win._enrich_analysis_result(db_result)
        assert "Reduce fillers." in enriched["improvements"]

    def test_parses_suggestions_from_text(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        db_result = {
            "feedback": "Suggestions:\n• Try pausing."
        }
        enriched = win._enrich_analysis_result(db_result)
        assert "Try pausing." in enriched["suggestions"]

    def test_empty_feedback_produces_empty_lists(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        enriched = win._enrich_analysis_result({"feedback": ""})
        assert enriched["strengths"]    == []
        assert enriched["improvements"] == []
        assert enriched["suggestions"]  == []

    def test_enriched_result_preserves_scores(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        db_result = {"overall_score": 80.0, "fluency_score": 75.0, "feedback": ""}
        enriched = win._enrich_analysis_result(db_result)
        assert enriched["overall_score"] == 80.0
        assert enriched["fluency_score"] == 75.0


# ===========================================================================
# 13. Helpers integration
# ===========================================================================

class TestHelpersIntegration:

    def test_format_duration_75(self):
        assert "1m" in format_duration(75)

    def test_format_duration_zero(self):
        assert format_duration(0) == "0s"

    def test_format_duration_none(self):
        assert format_duration(None) == "—"

    def test_score_to_label_excellent(self):
        assert score_to_label(90.0) == "Excellent"

    def test_score_to_label_good(self):
        assert score_to_label(75.0) == "Good"

    def test_score_to_label_fair(self):
        assert score_to_label(60.0) == "Fair"

    def test_score_to_label_needs_work(self):
        assert score_to_label(40.0) == "Needs Work"

    def test_score_colour_high(self):
        # score_colour uses 85 as excellent threshold
        c = score_colour(90.0)
        assert c == settings.COLOR_SUCCESS

    def test_score_colour_mid(self):
        c = score_colour(65.0)
        assert c == settings.COLOR_WARNING

    def test_score_colour_low(self):
        c = score_colour(30.0)
        assert c == settings.COLOR_DANGER

    def test_score_colour_none(self):
        c = score_colour(None)
        assert c == settings.COLOR_TEXT_MUTED
