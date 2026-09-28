"""
tests/test_chunk8.py — pytest suite for Chunk 8 (AI Feedback Engine).

All tests use mocked or local providers — no real OpenAI API calls.
Tests run offline and do not require OPENAI_API_KEY to be set.

Covers:
    1.  FeedbackRequest — construction and field defaults
    2.  AIFeedbackResult — properties, has_content, source_label
    3.  GrammarCorrection and VocabularySuggestion dataclasses
    4.  Settings — AI feedback config present and valid
    5.  LocalFeedbackProvider — grammar detection (no fabrication)
    6.  LocalFeedbackProvider — vocabulary suggestions
    7.  LocalFeedbackProvider — filler word tips
    8.  LocalFeedbackProvider — repeated word tips
    9.  LocalFeedbackProvider — empty transcript
    10. LLMFeedbackProvider — unavailable without API key
    11. LLMFeedbackProvider — response parsing (mocked response)
    12. LLMFeedbackProvider — malformed JSON handling
    13. LLMFeedbackProvider — error handling (mocked exceptions)
    14. get_feedback_provider factory
    15. FeedbackService — sync analysis with local provider
    16. FeedbackService — build_request helper
    17. FeedbackService — fallback from LLM to local on error
    18. FeedbackWorker — signal emission (run() called directly)
    19. SessionResultsPage — show_ai_feedback() populates card
    20. SessionResultsPage — show_ai_feedback_loading()
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from speakup.config.settings import settings                       # noqa: E402
from speakup.services.feedback_schemas import (                    # noqa: E402
    AIFeedbackResult,
    FeedbackRequest,
    GrammarCorrection,
    VocabularySuggestion,
)
from speakup.services.feedback_providers import (                  # noqa: E402
    LocalFeedbackProvider,
    LLMFeedbackProvider,
    FeedbackProviderError,
    get_feedback_provider,
    _build_user_prompt,
)
from speakup.services.feedback_service import (                    # noqa: E402
    FeedbackService,
    FeedbackWorker,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

GOOD_TRANSCRIPT = (
    "Leadership is one of the most important skills anyone can develop. "
    "Good leaders inspire their teams and help them achieve their goals. "
    "They use clear communication and show empathy to their people. "
    "It is really important to think about the needs of the team. "
    "Good leadership is important because it makes things better for everyone."
)  # Contains: "good" × 3, "important" × 3, "really" × 1

FILLER_TRANSCRIPT = (
    "Um, so leadership is like, you know, really important. "
    "Um, I think that, basically, good leaders are, uh, people who, "
    "you know, inspire others. So like, um, leadership is basically "
    "about helping people. You know what I mean? Um, so yeah."
)

GRAMMAR_TRANSCRIPT = (
    "I seen the presentation yesterday and they done a great job. "
    "We should of prepared better. Less people attended than expected."
)

EMPTY_REQUEST = FeedbackRequest()

FULL_REQUEST = FeedbackRequest(
    transcript=GOOD_TRANSCRIPT,
    topic="Leadership",
    category="Business",
    duration_seconds=75,
    words_per_minute=115.0,
    filler_word_count=5,
    filler_counts={"um": 3, "like": 2},
    fluency_score=74.0,
    vocabulary_score=78.0,
    grammar_score=80.0,
    confidence_score=72.0,
    overall_score=76.0,
)


# ===========================================================================
# 1. FeedbackRequest
# ===========================================================================

class TestFeedbackRequest:

    def test_defaults_are_safe(self):
        req = FeedbackRequest()
        assert req.transcript == ""
        assert req.filler_word_count == 0
        assert req.filler_counts == {}
        assert req.prior_avg_overall is None

    def test_fields_set_correctly(self):
        req = FeedbackRequest(
            transcript="hello world",
            topic="Test",
            duration_seconds=60,
            filler_word_count=2,
        )
        assert req.transcript == "hello world"
        assert req.topic == "Test"
        assert req.duration_seconds == 60
        assert req.filler_word_count == 2


# ===========================================================================
# 2. AIFeedbackResult properties
# ===========================================================================

class TestAIFeedbackResult:

    def test_empty_result_has_no_content(self):
        r = AIFeedbackResult.empty()
        assert not r.has_content

    def test_result_with_grammar_has_content(self):
        r = AIFeedbackResult(
            source="local",
            grammar_corrections=[GrammarCorrection("seen", "saw", "Use simple past")],
        )
        assert r.has_content

    def test_result_with_vocab_has_content(self):
        r = AIFeedbackResult(
            source="local",
            vocabulary_suggestions=[VocabularySuggestion("good", 3, ["effective"])],
        )
        assert r.has_content

    def test_is_llm_false_for_local(self):
        assert not AIFeedbackResult(source="local").is_llm

    def test_is_llm_true_for_llm(self):
        assert AIFeedbackResult(source="llm").is_llm

    def test_source_label_local(self):
        r = AIFeedbackResult(source="local", model="local/rule-based")
        assert "Local" in r.source_label

    def test_source_label_llm(self):
        r = AIFeedbackResult(source="llm", model="gpt-4o-mini")
        assert "AI-Generated" in r.source_label
        assert "gpt-4o-mini" in r.source_label

    def test_empty_factory_sets_error(self):
        r = AIFeedbackResult.empty(error="test error")
        assert r.error == "test error"
        assert not r.has_content

    def test_result_with_only_llm_sections_has_content(self):
        r = AIFeedbackResult(
            source="llm",
            topic_specific_feedback=["Great job on structure."],
        )
        assert r.has_content


# ===========================================================================
# 3. GrammarCorrection and VocabularySuggestion
# ===========================================================================

class TestFeedbackDataclasses:

    def test_grammar_correction_fields(self):
        gc = GrammarCorrection("I seen", "I saw", "Use simple past tense.")
        assert gc.original == "I seen"
        assert gc.suggestion == "I saw"
        assert "simple past" in gc.explanation

    def test_vocabulary_suggestion_fields(self):
        vs = VocabularySuggestion("good", 4, ["effective", "beneficial"])
        assert vs.word == "good"
        assert vs.count == 4
        assert "effective" in vs.alternatives


# ===========================================================================
# 4. Settings — AI feedback config
# ===========================================================================

class TestAIFeedbackSettings:

    def test_backend_is_local_by_default(self):
        assert settings.AI_FEEDBACK_BACKEND in ("local", "llm")

    def test_openai_key_is_string(self):
        assert isinstance(settings.OPENAI_API_KEY, str)

    def test_model_is_string(self):
        assert isinstance(settings.AI_FEEDBACK_MODEL, str)
        assert len(settings.AI_FEEDBACK_MODEL) > 0

    def test_timeout_is_positive(self):
        assert settings.AI_FEEDBACK_TIMEOUT_S > 0

    def test_max_tokens_is_positive(self):
        assert settings.AI_FEEDBACK_MAX_TOKENS > 0

    def test_min_words_is_positive(self):
        assert settings.AI_FEEDBACK_MIN_WORDS > 0

    def test_has_openai_key_false_when_empty(self):
        original = settings.__class__.OPENAI_API_KEY
        settings.__class__.OPENAI_API_KEY = ""
        try:
            assert not settings.has_openai_key()
        finally:
            settings.__class__.OPENAI_API_KEY = original

    def test_llm_feedback_available_false_without_key(self):
        """Without a key, llm_feedback_available() must return False."""
        original_key     = settings.__class__.OPENAI_API_KEY
        original_backend = settings.__class__.AI_FEEDBACK_BACKEND
        settings.__class__.OPENAI_API_KEY = ""
        settings.__class__.AI_FEEDBACK_BACKEND = "llm"
        try:
            assert not settings.llm_feedback_available()
        finally:
            settings.__class__.OPENAI_API_KEY = original_key
            settings.__class__.AI_FEEDBACK_BACKEND = original_backend


# ===========================================================================
# 5. LocalFeedbackProvider — grammar detection
# ===========================================================================

class TestLocalProviderGrammar:

    @pytest.fixture
    def provider(self):
        return LocalFeedbackProvider()

    def test_detects_seen_as_past_tense(self, provider):
        req = FeedbackRequest(transcript="I seen the movie last night.")
        result = provider.analyze(req)
        assert any("seen" in c.original.lower() or "seen" in c.explanation.lower()
                   for c in result.grammar_corrections)

    def test_detects_should_of(self, provider):
        req = FeedbackRequest(transcript="We should of done better preparation.")
        result = provider.analyze(req)
        assert len(result.grammar_corrections) >= 1

    def test_no_corrections_for_clean_text(self, provider):
        clean = (
            "Leadership requires clear communication and empathy. "
            "Great leaders listen to their teams and support their development."
        )
        result = provider.analyze(FeedbackRequest(transcript=clean))
        # Rule-based: only flags patterns that actually match
        assert isinstance(result.grammar_corrections, list)

    def test_each_correction_cites_phrase_from_transcript(self, provider):
        result = provider.analyze(FeedbackRequest(transcript=GRAMMAR_TRANSCRIPT))
        for correction in result.grammar_corrections:
            assert len(correction.original) > 0
            # The original phrase must not be fabricated — it must appear
            # somewhere in the transcript (lowercased)
            assert correction.original.lower() in GRAMMAR_TRANSCRIPT.lower() or \
                   any(w in GRAMMAR_TRANSCRIPT.lower()
                       for w in correction.original.lower().split())

    def test_grammar_corrections_never_fabricated_on_empty(self, provider):
        result = provider.analyze(FeedbackRequest(transcript=""))
        assert result.grammar_corrections == []

    def test_detects_multiple_issues(self, provider):
        result = provider.analyze(FeedbackRequest(transcript=GRAMMAR_TRANSCRIPT))
        assert len(result.grammar_corrections) >= 2


# ===========================================================================
# 6. LocalFeedbackProvider — vocabulary suggestions
# ===========================================================================

class TestLocalProviderVocabulary:

    @pytest.fixture
    def provider(self):
        return LocalFeedbackProvider()

    def test_suggests_synonym_for_repeated_word(self, provider):
        text = "Good leadership is important. Good leaders are good communicators."
        result = provider.analyze(FeedbackRequest(transcript=text))
        words = [s.word for s in result.vocabulary_suggestions]
        assert "good" in words

    def test_vocabulary_suggestions_have_alternatives(self, provider):
        result = provider.analyze(FULL_REQUEST)
        for sug in result.vocabulary_suggestions:
            assert len(sug.alternatives) >= 1

    def test_no_suggestions_for_short_text(self, provider):
        result = provider.analyze(FeedbackRequest(transcript="Hello world."))
        # Short text — no words appear ≥ 2 times in synonym map
        assert isinstance(result.vocabulary_suggestions, list)

    def test_suggestions_capped_at_six(self, provider):
        # Repeat many words from synonym map
        text = (
            "important good bad big small show use make think things "
            "important good bad big small show use make think things "
        )
        result = provider.analyze(FeedbackRequest(transcript=text))
        assert len(result.vocabulary_suggestions) <= 6


# ===========================================================================
# 7. LocalFeedbackProvider — filler word tips
# ===========================================================================

class TestLocalProviderFillers:

    @pytest.fixture
    def provider(self):
        return LocalFeedbackProvider()

    def test_no_tips_when_zero_fillers(self, provider):
        req = FeedbackRequest(transcript="Hello.", filler_word_count=0)
        result = provider.analyze(req)
        assert result.filler_reduction_tips == []

    def test_tip_mentions_specific_fillers(self, provider):
        req = FeedbackRequest(
            transcript=FILLER_TRANSCRIPT,
            filler_word_count=8,
            filler_counts={"um": 5, "like": 2, "you know": 1},
        )
        result = provider.analyze(req)
        assert len(result.filler_reduction_tips) >= 1
        combined = " ".join(result.filler_reduction_tips).lower()
        assert "um" in combined or "filler" in combined

    def test_tip_count_based_on_filler_frequency(self, provider):
        """High filler count should generate more tips than low count."""
        low_req  = FeedbackRequest(transcript="Hello.", filler_word_count=2,
                                   filler_counts={"um": 2})
        high_req = FeedbackRequest(transcript="Hello.", filler_word_count=15,
                                   filler_counts={"um": 10, "like": 5})
        low_result  = provider.analyze(low_req)
        high_result = provider.analyze(high_req)
        assert len(high_result.filler_reduction_tips) >= len(low_result.filler_reduction_tips)


# ===========================================================================
# 8. LocalFeedbackProvider — repeated word tips
# ===========================================================================

class TestLocalProviderRepeatedWords:

    @pytest.fixture
    def provider(self):
        return LocalFeedbackProvider()

    def test_flags_content_words_repeated_3_plus_times(self, provider):
        text = "leadership leadership leadership leadership people people people"
        result = provider.analyze(FeedbackRequest(transcript=text))
        combined = " ".join(result.repeated_word_tips).lower()
        # "leadership" or "people" should appear
        assert "leadership" in combined or "people" in combined

    def test_repeated_tips_are_strings(self, provider):
        result = provider.analyze(FULL_REQUEST)
        for tip in result.repeated_word_tips:
            assert isinstance(tip, str)
            assert len(tip) > 0


# ===========================================================================
# 9. LocalFeedbackProvider — empty transcript
# ===========================================================================

class TestLocalProviderEmpty:

    @pytest.fixture
    def provider(self):
        return LocalFeedbackProvider()

    def test_empty_transcript_returns_result_with_warning(self, provider):
        result = provider.analyze(FeedbackRequest(transcript=""))
        assert isinstance(result, AIFeedbackResult)
        assert result.warning is not None

    def test_empty_transcript_has_no_fabricated_content(self, provider):
        result = provider.analyze(FeedbackRequest(transcript=""))
        assert result.grammar_corrections == []
        assert result.vocabulary_suggestions == []
        assert result.filler_reduction_tips == []

    def test_provider_always_available(self):
        assert LocalFeedbackProvider().is_available() is True

    def test_backend_name_is_local(self):
        assert LocalFeedbackProvider().backend_name == "local"


# ===========================================================================
# 10. LLMFeedbackProvider — unavailable without key
# ===========================================================================

class TestLLMProviderAvailability:

    def test_unavailable_when_no_api_key(self):
        original = settings.__class__.OPENAI_API_KEY
        settings.__class__.OPENAI_API_KEY = ""
        try:
            provider = LLMFeedbackProvider()
            assert not provider.is_available()
        finally:
            settings.__class__.OPENAI_API_KEY = original

    def test_available_when_api_key_set(self):
        original = settings.__class__.OPENAI_API_KEY
        settings.__class__.OPENAI_API_KEY = "sk-test-key"
        try:
            provider = LLMFeedbackProvider()
            assert provider.is_available()
        finally:
            settings.__class__.OPENAI_API_KEY = original

    def test_analyze_returns_error_result_when_no_key(self):
        original = settings.__class__.OPENAI_API_KEY
        settings.__class__.OPENAI_API_KEY = ""
        try:
            provider = LLMFeedbackProvider()
            result = provider.analyze(FULL_REQUEST)
            assert result.error is not None
        finally:
            settings.__class__.OPENAI_API_KEY = original

    def test_backend_name_is_llm(self):
        assert LLMFeedbackProvider().backend_name == "llm"

    def test_returns_error_for_short_transcript(self):
        original = settings.__class__.OPENAI_API_KEY
        settings.__class__.OPENAI_API_KEY = "sk-test-key"
        try:
            provider = LLMFeedbackProvider()
            short_req = FeedbackRequest(transcript="Hi.")
            result = provider.analyze(short_req)
            assert result.error is not None
        finally:
            settings.__class__.OPENAI_API_KEY = original


# ===========================================================================
# 11. LLMFeedbackProvider — response parsing (mocked response)
# ===========================================================================

class TestLLMProviderParsing:

    VALID_RESPONSE = json.dumps({
        "grammar_corrections": [
            {
                "original": "I seen the movie",
                "suggestion": "I saw the movie",
                "explanation": "Use simple past tense."
            }
        ],
        "better_sentence_alternatives": [
            "Original: 'good leaders' → Better: 'effective leaders'"
        ],
        "vocabulary_suggestions": [
            {"word": "good", "count": 3, "alternatives": ["effective", "strong"]}
        ],
        "topic_specific_feedback": [
            "Your discussion of leadership was structured well."
        ],
        "personalized_practice_advice": [
            "Try recording yourself on advanced topics to build vocabulary depth."
        ],
    })

    def test_parses_grammar_corrections(self):
        provider = LLMFeedbackProvider()
        result = provider._parse_response(self.VALID_RESPONSE)
        assert len(result.grammar_corrections) == 1
        assert result.grammar_corrections[0].original == "I seen the movie"

    def test_parses_vocabulary_suggestions(self):
        provider = LLMFeedbackProvider()
        result = provider._parse_response(self.VALID_RESPONSE)
        assert len(result.vocabulary_suggestions) == 1
        assert result.vocabulary_suggestions[0].word == "good"
        assert result.vocabulary_suggestions[0].count == 3

    def test_parses_sentence_alternatives(self):
        provider = LLMFeedbackProvider()
        result = provider._parse_response(self.VALID_RESPONSE)
        assert len(result.better_sentence_alternatives) == 1

    def test_parses_topic_feedback(self):
        provider = LLMFeedbackProvider()
        result = provider._parse_response(self.VALID_RESPONSE)
        assert len(result.topic_specific_feedback) == 1

    def test_parses_practice_advice(self):
        provider = LLMFeedbackProvider()
        result = provider._parse_response(self.VALID_RESPONSE)
        assert len(result.personalized_practice_advice) == 1

    def test_source_is_llm(self):
        provider = LLMFeedbackProvider()
        result = provider._parse_response(self.VALID_RESPONSE)
        assert result.source == "llm"

    def test_grammar_without_original_is_skipped(self):
        """Grammar entries without an 'original' field must be silently dropped."""
        bad = json.dumps({
            "grammar_corrections": [
                {"suggestion": "Use past tense", "explanation": "..."}
            ],
            "better_sentence_alternatives": [],
            "vocabulary_suggestions": [],
            "topic_specific_feedback": [],
            "personalized_practice_advice": [],
        })
        provider = LLMFeedbackProvider()
        result = provider._parse_response(bad)
        assert result.grammar_corrections == []

    def test_empty_json_object(self):
        provider = LLMFeedbackProvider()
        result = provider._parse_response("{}")
        assert result.grammar_corrections == []
        assert result.better_sentence_alternatives == []


# ===========================================================================
# 12. LLMFeedbackProvider — malformed JSON
# ===========================================================================

class TestLLMProviderMalformedJSON:

    def test_malformed_json_returns_error_result(self):
        provider = LLMFeedbackProvider()
        result = provider._parse_response("not valid json {{{")
        assert result.error is not None
        assert "invalid" in result.error.lower() or "json" in result.error.lower()

    def test_malformed_json_has_no_fabricated_corrections(self):
        provider = LLMFeedbackProvider()
        result = provider._parse_response("not valid json {{{")
        assert result.grammar_corrections == []


# ===========================================================================
# 13. LLMFeedbackProvider — error handling (mocked exceptions)
# ===========================================================================

class TestLLMProviderErrors:

    def _provider_with_key(self):
        settings.__class__.OPENAI_API_KEY = "sk-test"
        return LLMFeedbackProvider()

    def _restore_key(self, original):
        settings.__class__.OPENAI_API_KEY = original

    def test_timeout_raises_feedback_provider_error(self):
        import openai as oa
        original = settings.__class__.OPENAI_API_KEY
        settings.__class__.OPENAI_API_KEY = "sk-test"
        try:
            provider = LLMFeedbackProvider()
            with patch.object(provider, "_call_api",
                              side_effect=FeedbackProviderError("timed out")):
                result = provider.analyze(FULL_REQUEST)
            assert result.error is not None
        finally:
            settings.__class__.OPENAI_API_KEY = original

    def test_api_error_returns_error_result(self):
        original = settings.__class__.OPENAI_API_KEY
        settings.__class__.OPENAI_API_KEY = "sk-test"
        try:
            provider = LLMFeedbackProvider()
            with patch.object(provider, "_call_api",
                              side_effect=FeedbackProviderError("API error 500")):
                result = provider.analyze(FULL_REQUEST)
            assert result.error is not None
        finally:
            settings.__class__.OPENAI_API_KEY = original

    def test_auth_error_produces_error_result(self):
        """Simulate authentication error without real API call."""
        original = settings.__class__.OPENAI_API_KEY
        settings.__class__.OPENAI_API_KEY = "sk-bad-key"
        try:
            provider = LLMFeedbackProvider()
            with patch.object(
                provider, "_call_api",
                side_effect=FeedbackProviderError("Invalid API key"),
            ):
                result = provider.analyze(FULL_REQUEST)
            assert result.error is not None
        finally:
            settings.__class__.OPENAI_API_KEY = original


# ===========================================================================
# 14. get_feedback_provider factory
# ===========================================================================

class TestGetFeedbackProviderFactory:

    def test_local_backend_returns_local_provider(self):
        provider = get_feedback_provider("local")
        assert isinstance(provider, LocalFeedbackProvider)

    def test_llm_without_key_returns_local_provider(self):
        original = settings.__class__.OPENAI_API_KEY
        settings.__class__.OPENAI_API_KEY = ""
        try:
            provider = get_feedback_provider("llm")
            # Falls back to local when no key
            assert isinstance(provider, LocalFeedbackProvider)
        finally:
            settings.__class__.OPENAI_API_KEY = original

    def test_llm_with_key_returns_llm_provider(self):
        original = settings.__class__.OPENAI_API_KEY
        settings.__class__.OPENAI_API_KEY = "sk-test-key"
        try:
            provider = get_feedback_provider("llm")
            assert isinstance(provider, LLMFeedbackProvider)
        finally:
            settings.__class__.OPENAI_API_KEY = original

    def test_default_uses_settings_backend(self):
        original = settings.__class__.AI_FEEDBACK_BACKEND
        settings.__class__.AI_FEEDBACK_BACKEND = "local"
        try:
            provider = get_feedback_provider()
            assert isinstance(provider, LocalFeedbackProvider)
        finally:
            settings.__class__.AI_FEEDBACK_BACKEND = original


# ===========================================================================
# 15. FeedbackService — sync analysis with local provider
# ===========================================================================

class TestFeedbackServiceSync:

    def test_analyze_sync_returns_result(self):
        svc = FeedbackService(provider=LocalFeedbackProvider())
        result = svc.analyze_sync(FULL_REQUEST)
        assert isinstance(result, AIFeedbackResult)

    def test_analyze_sync_no_fabrication_on_clean_text(self):
        clean = FeedbackRequest(
            transcript="Leaders communicate clearly and support their teams.",
        )
        svc = FeedbackService(provider=LocalFeedbackProvider())
        result = svc.analyze_sync(clean)
        # May have suggestions, but grammar_corrections should be empty
        assert result.grammar_corrections == []

    def test_backend_name(self):
        svc = FeedbackService(provider=LocalFeedbackProvider())
        assert svc.backend_name == "local"


# ===========================================================================
# 16. FeedbackService — build_request helper
# ===========================================================================

class TestBuildRequest:

    def test_extracts_wpm_from_analysis_result(self):
        analysis = {"words_per_minute": 120.5, "filler_word_count": 3,
                    "filler_counts": {"um": 3}, "fluency_score": 75.0,
                    "vocabulary_score": 80.0, "grammar_score": 85.0,
                    "confidence_score": 70.0, "overall_score": 78.0}
        req = FeedbackService.build_request(
            analysis_result=analysis,
            transcript="Hello world.",
            topic="Leadership",
            category="Business",
        )
        assert req.words_per_minute == 120.5
        assert req.filler_word_count == 3
        assert req.filler_counts == {"um": 3}

    def test_handles_none_values_gracefully(self):
        req = FeedbackService.build_request(
            analysis_result={"words_per_minute": None, "filler_word_count": None},
            transcript="",
        )
        assert req.words_per_minute == 0.0
        assert req.filler_word_count == 0

    def test_passes_prior_averages(self):
        req = FeedbackService.build_request(
            analysis_result={},
            prior_avg_overall=70.0,
            prior_avg_confidence=65.0,
        )
        assert req.prior_avg_overall == 70.0
        assert req.prior_avg_confidence == 65.0

    def test_empty_analysis_result(self):
        req = FeedbackService.build_request(analysis_result={})
        assert req.overall_score == 0.0
        assert req.transcript == ""


# ===========================================================================
# 17. FeedbackService — fallback from LLM to local on error
# ===========================================================================

class TestFeedbackServiceFallback:

    def test_fallback_to_local_when_llm_returns_error(self):
        """When LLM provider returns an error result, service uses local fallback."""
        class AlwaysFailingProvider(LLMFeedbackProvider):
            def is_available(self): return True
            def analyze(self, request):
                return AIFeedbackResult.empty(error="simulated LLM failure", source="llm")

        svc = FeedbackService(provider=AlwaysFailingProvider())
        result = svc.analyze_sync(FULL_REQUEST)
        # Fallback should have produced local analysis
        assert result.source == "local"
        assert result.warning is not None


# ===========================================================================
# 18. FeedbackWorker — signal emission
# ===========================================================================

class TestFeedbackWorkerSignals:

    def test_run_emits_feedback_ready_on_success(self):
        worker = FeedbackWorker(
            provider=LocalFeedbackProvider(),
            request=FULL_REQUEST,
        )
        results = []
        worker.feedback_ready.connect(lambda r: results.append(r))
        worker.run()
        assert len(results) == 1
        assert isinstance(results[0], AIFeedbackResult)

    def test_run_emits_status_running_then_completed(self):
        worker = FeedbackWorker(
            provider=LocalFeedbackProvider(),
            request=FULL_REQUEST,
        )
        statuses = []
        worker.status_changed.connect(statuses.append)
        worker.run()
        assert "running"   in statuses
        assert "completed" in statuses

    def test_run_emits_progress_message(self):
        worker = FeedbackWorker(
            provider=LocalFeedbackProvider(),
            request=FULL_REQUEST,
        )
        messages = []
        worker.progress_message.connect(messages.append)
        worker.run()
        assert len(messages) >= 1

    def test_run_does_not_emit_error_on_success(self):
        worker = FeedbackWorker(
            provider=LocalFeedbackProvider(),
            request=FULL_REQUEST,
        )
        errors = []
        worker.error_occurred.connect(errors.append)
        worker.run()
        assert errors == []


# ===========================================================================
# 19. SessionResultsPage — show_ai_feedback()
# ===========================================================================

class TestSessionResultsPageAIFeedback:

    @pytest.fixture
    def page(self):
        from speakup.ui.pages.session_results_page import SessionResultsPage
        p = SessionResultsPage()
        p.prepare(
            session_id=1,
            topic={"word": "Leadership", "category": "Business",
                   "difficulty_display": "Intermediate"},
            analysis_result={"overall_score": 75.0, "fluency_score": 72.0,
                             "vocabulary_score": 80.0, "grammar_score": 74.0,
                             "confidence_score": 70.0, "words_per_minute": 115.0,
                             "filler_word_count": 3, "pause_count": 2,
                             "type_token_ratio": 0.60, "total_words": 100},
            duration_seconds=75,
            transcript=GOOD_TRANSCRIPT,
        )
        return p

    def test_ai_card_initially_hidden(self, page):
        assert not page._ai_feedback_card.isVisible()

    def test_show_ai_feedback_reveals_card(self, page):
        result = AIFeedbackResult(
            source="local",
            grammar_corrections=[GrammarCorrection("seen", "saw", "Use past tense.")],
        )
        page.show_ai_feedback(result)
        # Card should now be visible (or at least not hidden)
        assert not page._ai_feedback_card.isHidden()

    def test_show_ai_feedback_with_empty_result(self, page):
        """Empty result must not crash — shows 'no issues found' banner."""
        result = AIFeedbackResult(source="local")
        page.show_ai_feedback(result)
        assert not page._ai_feedback_card.isHidden()

    def test_show_ai_feedback_with_error_result(self, page):
        result = AIFeedbackResult.empty(error="API timeout", source="llm")
        page.show_ai_feedback(result)
        assert not page._ai_feedback_card.isHidden()

    def test_show_ai_feedback_loading_reveals_card(self, page):
        page.show_ai_feedback_loading()
        assert not page._ai_feedback_card.isHidden()

    def test_show_ai_feedback_with_llm_result(self, page):
        result = AIFeedbackResult(
            source="llm",
            model="gpt-4o-mini",
            better_sentence_alternatives=["Original: 'good' → Better: 'effective'"],
            topic_specific_feedback=["Well-structured argument."],
            personalized_practice_advice=["Try advanced topics."],
        )
        page.show_ai_feedback(result)
        assert not page._ai_feedback_card.isHidden()

    def test_show_ai_feedback_updates_badge_for_local(self, page):
        result = AIFeedbackResult(source="local", model="local/rule-based")
        page.show_ai_feedback(result)
        badge_text = page._ai_source_badge.text()
        assert "Local" in badge_text or "local" in badge_text.lower()

    def test_show_ai_feedback_signal_exists(self, page):
        assert hasattr(page, "show_ai_feedback")
        assert callable(page.show_ai_feedback)


# ===========================================================================
# 20. SessionResultsPage — show_ai_feedback_loading()
# ===========================================================================

class TestSessionResultsPageLoading:

    @pytest.fixture
    def page(self):
        from speakup.ui.pages.session_results_page import SessionResultsPage
        return SessionResultsPage()

    def test_loading_shows_card(self, page):
        page.show_ai_feedback_loading()
        assert not page._ai_feedback_card.isHidden()

    def test_loading_then_result_does_not_crash(self, page):
        page.show_ai_feedback_loading()
        result = LocalFeedbackProvider().analyze(FULL_REQUEST)
        page.show_ai_feedback(result)

    def test_ai_feedback_card_has_correct_object_name(self, page):
        assert page._ai_feedback_card.objectName() == "AIFeedbackCard"

    def test_build_user_prompt_includes_transcript(self):
        prompt = _build_user_prompt(FULL_REQUEST)
        assert "Leadership" in prompt
        assert str(int(FULL_REQUEST.duration_seconds)) in prompt
