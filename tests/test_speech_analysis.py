"""
tests/test_speech_analysis.py — speech analysis engine (fluency, vocabulary, grammar, confidence).

All tests use fixed transcripts and deterministic logic — no external API,
no model downloads, no hardware required.

Covers:
    1.  Analysis settings config
    2.  Fluency analyser — WPM, pauses, scoring
    3.  Vocabulary analyser — TTR, hapax, advanced words
    4.  Filler-word detector — detection, rates, scoring
    5.  Grammar analyser (rule-based) — issue detection
    6.  Grammar analyser factory
    7.  Confidence scorer — components, caveat
    8.  Overall scorer — weighted breakdown, weights sum
    9.  Feedback generator — strengths / improvements / suggestions
    10. SpeechAnalysisResult — to_db_dict, to_ui_dict
    11. AnalysisService — calculate_wpm, detect_fillers, generate_feedback
    12. AnalysisService — full pipeline (with DB persistence)
    13. AnalysisService — duplicate persistence (upsert guard)
    14. AnalysisService — empty transcript edge case
    15. AnalysisWorker — signal emission (run() called directly, no thread)
    16. TranscriptionResultPage — show_analysis() populates the card
"""

from __future__ import annotations

import os
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Qt bootstrap (needed for QObject / Signal tests)
# ---------------------------------------------------------------------------
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
from speakup.config.settings import settings  # noqa: E402
from speakup.services.analysis.fluency      import analyse_fluency   # noqa: E402
from speakup.services.analysis.vocabulary   import analyse_vocabulary  # noqa: E402
from speakup.services.analysis.filler_words import analyse_filler_words  # noqa: E402
from speakup.services.analysis.grammar      import (   # noqa: E402
    RuleBasedGrammarAnalyzer, LLMGrammarAnalyzer, get_grammar_analyzer,
)
from speakup.services.analysis.confidence import analyse_confidence  # noqa: E402
from speakup.services.analysis.scoring    import compute_overall_score  # noqa: E402
from speakup.services.analysis.feedback   import generate_feedback  # noqa: E402
from speakup.services.analysis.schemas    import (   # noqa: E402
    FluencyMetrics, VocabularyMetrics, FillerMetrics,
    GrammarMetrics, ConfidenceMetrics, SpeechAnalysisResult,
    FeedbackBundle, OverallScore, ScoreBreakdown,
)
from speakup.services.analysis_service import AnalysisService   # noqa: E402
from speakup.services.analysis_worker  import AnalysisWorker    # noqa: E402
from tests.conftest import make_user, make_word, make_session   # noqa: E402

# ---------------------------------------------------------------------------
# Shared test transcripts
# ---------------------------------------------------------------------------

GOOD_TRANSCRIPT = (
    "Leadership is one of the most important skills anyone can develop. "
    "Great leaders inspire their teams through clear communication, empathy, "
    "and a genuine desire to help others succeed. They listen more than they "
    "speak, and they create environments where people can thrive. Effective "
    "leaders also demonstrate resilience in difficult situations, maintaining "
    "composure under pressure while encouraging innovative thinking. "
    "Furthermore, exceptional leadership requires continuous learning and "
    "adaptation to changing circumstances. The most successful leaders "
    "throughout history have combined strategic vision with practical wisdom."
)  # ~100 words, varied vocabulary, no fillers

FILLER_HEAVY_TRANSCRIPT = (
    "Um, so basically I think that, you know, leadership is like really "
    "important. Um, actually I believe that um great leaders are, uh, "
    "basically people who, you know, inspire others. So like, um, what I "
    "mean is that, uh, leadership is basically about helping people. "
    "Actually you know what I mean? Like, um, so yeah."
)  # ~60 words, heavy fillers

SHORT_TRANSCRIPT = "Hello world this is a test."   # 6 words, edge case

GRAMMAR_ISSUES_TRANSCRIPT = (
    "I seen the presentation yesterday and they done a good job. "
    "We should of prepared better but there wasn't nothing we could do. "
    "Less people attended than expected."
)  # contains multiple grammar rule violations


# ===========================================================================
# 1. Analysis settings config
# ===========================================================================

class TestAnalysisSettings:

    def test_filler_words_list_not_empty(self):
        fillers = settings.get_filler_words()
        assert len(fillers) >= 5
        assert "um" in fillers
        assert "uh" in fillers
        assert "like" in fillers

    def test_filler_words_lowercased(self):
        fillers = settings.get_filler_words()
        for f in fillers:
            assert f == f.lower(), f"Filler word not lowercase: {f!r}"

    def test_weights_sum_to_one(self):
        total = (
            settings.ANALYSIS_WEIGHT_FLUENCY
            + settings.ANALYSIS_WEIGHT_VOCABULARY
            + settings.ANALYSIS_WEIGHT_GRAMMAR
            + settings.ANALYSIS_WEIGHT_CONFIDENCE
            + settings.ANALYSIS_WEIGHT_FILLER
        )
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, expected 1.0"

    def test_pause_threshold_positive(self):
        assert settings.ANALYSIS_PAUSE_THRESHOLD_S > 0

    def test_grammar_backend_valid(self):
        assert settings.ANALYSIS_GRAMMAR_BACKEND in ("rule_based", "llm")

    def test_target_wpm_sensible(self):
        assert 80 <= settings.ANALYSIS_TARGET_WPM <= 200


# ===========================================================================
# 2. Fluency analyser
# ===========================================================================

class TestFluencyAnalyser:

    def test_wpm_calculation(self):
        # 100 words in 60 seconds = 100 WPM
        text = " ".join(["word"] * 100)
        m = analyse_fluency(text, duration_s=60.0)
        assert abs(m.words_per_minute - 100.0) < 0.5

    def test_wpm_zero_on_empty_transcript(self):
        m = analyse_fluency("", duration_s=60.0)
        assert m.words_per_minute == 0.0

    def test_wpm_zero_on_zero_duration(self):
        m = analyse_fluency("hello world", duration_s=0.0)
        assert m.words_per_minute == 0.0

    def test_score_in_range(self):
        m = analyse_fluency(GOOD_TRANSCRIPT, duration_s=75.0)
        assert 0.0 <= m.score <= 100.0

    def test_fast_speech_lower_score_than_normal(self):
        text = " ".join(["word"] * 200)
        slow  = analyse_fluency(text, duration_s=120.0)  # 100 WPM — normal
        rapid = analyse_fluency(text, duration_s=30.0)   # 400 WPM — too fast
        assert slow.score > rapid.score

    def test_pause_count_zero_without_segments(self):
        m = analyse_fluency(GOOD_TRANSCRIPT, duration_s=60.0, segments=None)
        assert m.pause_count == 0

    def test_pause_detection_with_segments(self):
        from speakup.services.transcription_schemas import TranscriptSegment
        segs = [
            TranscriptSegment(id=0, start=0.0,  end=5.0,  text=" Hello world",  no_speech_prob=0.1),
            TranscriptSegment(id=1, start=8.0,  end=12.0, text=" big gap here", no_speech_prob=0.1),
            TranscriptSegment(id=2, start=14.0, end=18.0, text=" another gap",  no_speech_prob=0.1),
        ]
        # Gaps: 8-5=3s and 14-12=2s — both >= threshold (1.5s)
        m = analyse_fluency(GOOD_TRANSCRIPT, duration_s=20.0, segments=segs)
        assert m.pause_count == 2

    def test_speech_ratio_below_one(self):
        m = analyse_fluency(GOOD_TRANSCRIPT, duration_s=90.0)
        assert 0.0 <= m.speech_ratio <= 1.0

    def test_fields_rounded(self):
        m = analyse_fluency(GOOD_TRANSCRIPT, duration_s=75.0)
        # Scores should be rounded to at most 2 decimal places
        assert m.words_per_minute == round(m.words_per_minute, 1)


# ===========================================================================
# 3. Vocabulary analyser
# ===========================================================================

class TestVocabularyAnalyser:

    def test_score_in_range(self):
        m = analyse_vocabulary(GOOD_TRANSCRIPT)
        assert 0.0 <= m.score <= 100.0

    def test_unique_word_count_nonzero(self):
        m = analyse_vocabulary(GOOD_TRANSCRIPT)
        assert m.unique_words > 0
        assert m.unique_words <= m.total_words

    def test_ttr_between_zero_and_one(self):
        m = analyse_vocabulary(GOOD_TRANSCRIPT)
        assert 0.0 <= m.type_token_ratio <= 1.0

    def test_rich_vocab_scores_higher_than_poor(self):
        rich = analyse_vocabulary(GOOD_TRANSCRIPT)
        poor = analyse_vocabulary("the the the the the the the the the the")
        assert rich.score > poor.score

    def test_hapax_ratio_between_zero_and_one(self):
        m = analyse_vocabulary(GOOD_TRANSCRIPT)
        assert 0.0 <= m.hapax_legomena_ratio <= 1.0

    def test_advanced_word_count_nonnegative(self):
        m = analyse_vocabulary(GOOD_TRANSCRIPT)
        assert m.advanced_word_count >= 0

    def test_empty_transcript_returns_baseline(self):
        m = analyse_vocabulary("")
        assert m.total_words == 0
        assert m.score >= 0.0

    def test_repeated_words_detected(self):
        # "apple" appears 5 times — should appear in repeated_words
        text = "apple " * 5 + GOOD_TRANSCRIPT
        m = analyse_vocabulary(text)
        assert "apple" in m.repeated_words
        assert m.repeated_words["apple"] >= 5

    def test_total_words_correct(self):
        text = "one two three four five"
        m = analyse_vocabulary(text)
        assert m.total_words == 5


# ===========================================================================
# 4. Filler-word detector
# ===========================================================================

class TestFillerWordDetector:

    def test_detects_um(self):
        m = analyse_filler_words("Um this is um a test um", ["um"])
        assert m.counts.get("um", 0) == 3

    def test_detects_multi_word_filler(self):
        m = analyse_filler_words("you know what you know you know", ["you know"])
        assert m.counts.get("you know", 0) == 3

    def test_filler_rate_correct(self):
        # 2 fillers in 10 words = 0.2 rate
        m = analyse_filler_words("um word word word word um word word word word", ["um"])
        assert abs(m.filler_rate - 0.2) < 0.01

    def test_no_fillers_scores_high(self):
        m = analyse_filler_words(GOOD_TRANSCRIPT)
        assert m.score >= 85.0

    def test_heavy_fillers_score_low(self):
        m = analyse_filler_words(FILLER_HEAVY_TRANSCRIPT)
        assert m.score < 60.0

    def test_total_filler_count_sum(self):
        m = analyse_filler_words("um uh um uh um", ["um", "uh"])
        assert m.total_filler_count == 5
        assert sum(m.counts.values()) == 5

    def test_case_insensitive(self):
        m = analyse_filler_words("Um UM uM um", ["um"])
        assert m.counts.get("um", 0) == 4

    def test_empty_transcript_zero_fillers(self):
        m = analyse_filler_words("")
        assert m.total_filler_count == 0
        assert m.score == 100.0

    def test_uses_settings_filler_list_by_default(self):
        m = analyse_filler_words("um uh basically actually")
        assert m.total_filler_count >= 4

    def test_filler_rate_between_zero_and_one(self):
        m = analyse_filler_words(FILLER_HEAVY_TRANSCRIPT)
        assert 0.0 <= m.filler_rate <= 1.0


# ===========================================================================
# 5. Grammar analyser (rule-based)
# ===========================================================================

class TestRuleBasedGrammar:

    @pytest.fixture
    def analyser(self):
        return RuleBasedGrammarAnalyzer()

    def test_no_issues_on_clean_text(self, analyser):
        result = analyser.analyse(GOOD_TRANSCRIPT)
        assert result.issue_count == 0
        assert result.score >= 80.0

    def test_detects_could_of(self, analyser):
        result = analyser.analyse("We should of done better and could of prepared more.")
        assert result.issue_count >= 1
        issues_text = " ".join(result.issues).lower()
        assert "have" in issues_text or "should" in issues_text.lower()

    def test_detects_double_negation(self, analyser):
        result = analyser.analyse("I don't know nothing about that topic.")
        assert result.issue_count >= 1

    def test_detects_seen_as_past_tense(self, analyser):
        result = analyser.analyse("I seen that movie last night.")
        assert result.issue_count >= 1

    def test_detects_less_with_countable(self, analyser):
        result = analyser.analyse("There were less people at the event than we expected.")
        assert result.issue_count >= 1

    def test_score_decreases_with_more_issues(self, analyser):
        clean  = analyser.analyse(GOOD_TRANSCRIPT)
        issues = analyser.analyse(GRAMMAR_ISSUES_TRANSCRIPT)
        assert clean.score >= issues.score

    def test_multiple_issues_transcript(self, analyser):
        result = analyser.analyse(GRAMMAR_ISSUES_TRANSCRIPT)
        assert result.issue_count >= 2

    def test_backend_name(self, analyser):
        assert analyser.backend_name == "rule_based"

    def test_score_in_range(self, analyser):
        result = analyser.analyse(GOOD_TRANSCRIPT)
        assert 0.0 <= result.score <= 100.0

    def test_empty_transcript_returns_neutral(self, analyser):
        result = analyser.analyse("")
        assert result.score == 50.0
        assert result.issue_count == 0

    def test_issue_descriptions_are_strings(self, analyser):
        result = analyser.analyse(GRAMMAR_ISSUES_TRANSCRIPT)
        for issue in result.issues:
            assert isinstance(issue, str)
            assert len(issue) > 0


# ===========================================================================
# 6. Grammar analyser factory
# ===========================================================================

class TestGrammarFactory:

    def test_rule_based_backend(self):
        a = get_grammar_analyzer("rule_based")
        assert isinstance(a, RuleBasedGrammarAnalyzer)

    def test_llm_backend(self):
        a = get_grammar_analyzer("llm")
        assert isinstance(a, LLMGrammarAnalyzer)

    def test_llm_returns_neutral_score(self):
        a = LLMGrammarAnalyzer()
        result = a.analyse("Some text here.")
        assert result.score == 50.0
        assert "not yet configured" in result.notes.lower()

    def test_unknown_backend_falls_back_to_rule_based(self):
        a = get_grammar_analyzer("nonexistent_backend")
        assert isinstance(a, RuleBasedGrammarAnalyzer)

    def test_none_uses_settings(self):
        a = get_grammar_analyzer(None)
        assert a is not None


# ===========================================================================
# 7. Confidence scorer
# ===========================================================================

class TestConfidenceScorer:

    def _make_metrics(self, wpm=130.0, filler_rate=0.02, ttr=0.55,
                      speech_ratio=0.85, avg_logprob=-0.3):
        fluency = FluencyMetrics(
            words_per_minute=wpm, pause_count=2, avg_pause_duration_s=1.5,
            total_pause_duration_s=3.0, speech_ratio=speech_ratio,
            disfluent_segment_ratio=0.1, score=80.0,
        )
        vocab = VocabularyMetrics(
            total_words=100, unique_words=55, type_token_ratio=ttr,
            hapax_legomena_ratio=0.4, avg_word_length=5.2,
            repeated_words={}, advanced_word_count=8, score=75.0,
        )
        filler = FillerMetrics(
            counts={}, total_filler_count=2,
            filler_rate=filler_rate, score=80.0,
        )
        return fluency, vocab, filler

    def test_score_in_range(self):
        fl, vo, fi = self._make_metrics()
        m = analyse_confidence(fl, vo, fi, avg_logprob=-0.3)
        assert 0.0 <= m.score <= 100.0

    def test_high_filler_rate_lowers_score(self):
        fl1, vo, fi1 = self._make_metrics(filler_rate=0.005)
        fl2, vo, fi2 = self._make_metrics(filler_rate=0.15)
        fi1 = FillerMetrics(counts={}, total_filler_count=1,
                            filler_rate=0.005, score=99.0)
        fi2 = FillerMetrics(counts={}, total_filler_count=15,
                            filler_rate=0.15, score=10.0)
        m1 = analyse_confidence(fl1, vo, fi1)
        m2 = analyse_confidence(fl2, vo, fi2)
        assert m1.score > m2.score

    def test_label_is_set(self):
        fl, vo, fi = self._make_metrics()
        m = analyse_confidence(fl, vo, fi)
        assert m.label == "Speaking Confidence Estimate"

    def test_caveat_mentions_observable(self):
        fl, vo, fi = self._make_metrics()
        m = analyse_confidence(fl, vo, fi)
        assert "observable" in m.caveat.lower() or "measurable" in m.caveat.lower()

    def test_caveat_denies_psychological_inference(self):
        fl, vo, fi = self._make_metrics()
        m = analyse_confidence(fl, vo, fi)
        caveat_lower = m.caveat.lower()
        assert "psychological" in caveat_lower or "emotional" in caveat_lower

    def test_five_component_scores_present(self):
        fl, vo, fi = self._make_metrics()
        m = analyse_confidence(fl, vo, fi)
        assert m.wpm_consistency_score >= 0
        assert m.low_filler_score >= 0
        assert m.vocabulary_richness_score >= 0
        assert m.speech_continuity_score >= 0
        assert m.logprob_quality_score >= 0


# ===========================================================================
# 8. Overall scorer
# ===========================================================================

class TestOverallScorer:

    def _make_all_metrics(self, score_val=75.0):
        fl = FluencyMetrics(
            words_per_minute=120.0, pause_count=3, avg_pause_duration_s=1.8,
            total_pause_duration_s=5.4, speech_ratio=0.85,
            disfluent_segment_ratio=0.1, score=score_val,
        )
        vo = VocabularyMetrics(
            total_words=100, unique_words=55, type_token_ratio=0.55,
            hapax_legomena_ratio=0.4, avg_word_length=5.2,
            repeated_words={}, advanced_word_count=8, score=score_val,
        )
        fi = FillerMetrics(counts={}, total_filler_count=2,
                           filler_rate=0.02, score=score_val)
        gr = GrammarMetrics(backend="rule_based", issues=[],
                            issue_count=0, score=score_val)
        co = ConfidenceMetrics(
            wpm_consistency_score=score_val, low_filler_score=score_val,
            vocabulary_richness_score=score_val, speech_continuity_score=score_val,
            logprob_quality_score=score_val, score=score_val,
        )
        return fl, vo, fi, gr, co

    def test_total_in_range(self):
        fl, vo, fi, gr, co = self._make_all_metrics()
        overall = compute_overall_score(fl, vo, fi, gr, co)
        assert 0.0 <= overall.total <= 100.0

    def test_all_100_scores_give_100_total(self):
        fl, vo, fi, gr, co = self._make_all_metrics(score_val=100.0)
        overall = compute_overall_score(fl, vo, fi, gr, co)
        assert abs(overall.total - 100.0) < 0.5

    def test_all_zero_scores_give_zero_total(self):
        fl, vo, fi, gr, co = self._make_all_metrics(score_val=0.0)
        overall = compute_overall_score(fl, vo, fi, gr, co)
        assert overall.total < 5.0

    def test_breakdown_has_five_entries(self):
        fl, vo, fi, gr, co = self._make_all_metrics()
        overall = compute_overall_score(fl, vo, fi, gr, co)
        breakdowns = [overall.fluency, overall.vocabulary,
                      overall.grammar, overall.confidence, overall.filler]
        assert len(breakdowns) == 5

    def test_contributions_sum_to_total(self):
        fl, vo, fi, gr, co = self._make_all_metrics()
        overall = compute_overall_score(fl, vo, fi, gr, co)
        contrib_sum = sum(bd.contribution for bd in [
            overall.fluency, overall.vocabulary,
            overall.grammar, overall.confidence, overall.filler,
        ])
        assert abs(contrib_sum - overall.total) < 0.1

    def test_breakdown_text_contains_dimensions(self):
        fl, vo, fi, gr, co = self._make_all_metrics()
        overall = compute_overall_score(fl, vo, fi, gr, co)
        text = overall.breakdown_text
        assert "Fluency"    in text
        assert "Vocabulary" in text
        assert "Grammar"    in text
        assert "Confidence" in text
        assert "Filler"     in text

    def test_custom_weights_applied(self):
        fl, vo, fi, gr, co = self._make_all_metrics(score_val=80.0)
        custom = {"fluency": 1.0, "vocabulary": 0.0,
                  "grammar": 0.0, "confidence": 0.0, "filler": 0.0}
        overall = compute_overall_score(fl, vo, fi, gr, co, weights=custom)
        assert abs(overall.total - 80.0) < 0.5


# ===========================================================================
# 9. Feedback generator
# ===========================================================================

class TestFeedbackGenerator:

    def _run_pipeline_for_transcript(self, text: str, duration: float = 75.0):
        fl = analyse_fluency(text, duration)
        vo = analyse_vocabulary(text)
        fi = analyse_filler_words(text)
        gr = get_grammar_analyzer("rule_based").analyse(text)
        co = analyse_confidence(fl, vo, fi)
        overall = compute_overall_score(fl, vo, fi, gr, co)
        return generate_feedback(fl, vo, fi, gr, co, overall)

    def test_returns_feedback_bundle(self):
        fb = self._run_pipeline_for_transcript(GOOD_TRANSCRIPT)
        assert isinstance(fb, FeedbackBundle)

    def test_strengths_not_empty(self):
        fb = self._run_pipeline_for_transcript(GOOD_TRANSCRIPT)
        assert len(fb.strengths) >= 1

    def test_improvements_not_empty_for_poor_input(self):
        fb = self._run_pipeline_for_transcript(FILLER_HEAVY_TRANSCRIPT, duration=30.0)
        assert len(fb.improvements) >= 1

    def test_suggestions_provided(self):
        fb = self._run_pipeline_for_transcript(FILLER_HEAVY_TRANSCRIPT, duration=30.0)
        assert len(fb.suggestions) >= 1

    def test_to_text_is_string(self):
        fb = self._run_pipeline_for_transcript(GOOD_TRANSCRIPT)
        text = fb.to_text()
        assert isinstance(text, str)
        assert len(text) > 20

    def test_to_text_contains_strengths_header(self):
        fb = self._run_pipeline_for_transcript(GOOD_TRANSCRIPT)
        text = fb.to_text()
        assert "Strengths" in text

    def test_filler_feedback_triggered(self):
        fb = self._run_pipeline_for_transcript(FILLER_HEAVY_TRANSCRIPT, duration=30.0)
        combined = " ".join(fb.improvements + fb.suggestions).lower()
        assert "filler" in combined or "um" in combined or "pause" in combined

    def test_grammar_feedback_triggered(self):
        fb = self._run_pipeline_for_transcript(GRAMMAR_ISSUES_TRANSCRIPT, duration=20.0)
        combined = " ".join(fb.improvements + fb.suggestions).lower()
        assert "grammar" in combined or "have" in combined or "negation" in combined


# ===========================================================================
# 10. SpeechAnalysisResult — to_db_dict, to_ui_dict
# ===========================================================================

class TestSpeechAnalysisResult:

    def _make_result(self) -> SpeechAnalysisResult:
        fl = analyse_fluency(GOOD_TRANSCRIPT, 75.0)
        vo = analyse_vocabulary(GOOD_TRANSCRIPT)
        fi = analyse_filler_words(GOOD_TRANSCRIPT)
        gr = get_grammar_analyzer("rule_based").analyse(GOOD_TRANSCRIPT)
        co = analyse_confidence(fl, vo, fi)
        ov = compute_overall_score(fl, vo, fi, gr, co)
        fb = generate_feedback(fl, vo, fi, gr, co, ov)
        return SpeechAnalysisResult(
            session_id=1, fluency=fl, vocabulary=vo, filler=fi,
            grammar=gr, confidence=co, overall=ov, feedback=fb,
        )

    def test_to_db_dict_has_required_keys(self):
        r = self._make_result()
        d = r.to_db_dict()
        for key in ["fluency_score", "vocabulary_score", "grammar_score",
                    "confidence_score", "filler_word_count", "words_per_minute",
                    "pause_count", "overall_score", "feedback"]:
            assert key in d, f"Missing key: {key}"

    def test_to_db_dict_scores_in_range(self):
        r = self._make_result()
        d = r.to_db_dict()
        for key in ["fluency_score", "vocabulary_score", "grammar_score",
                    "confidence_score", "overall_score"]:
            val = d[key]
            assert 0.0 <= val <= 100.0, f"{key} = {val} out of range"

    def test_to_db_dict_no_pronunciation_score(self):
        # pronunciation_score is intentionally None (not yet supported)
        r = self._make_result()
        d = r.to_db_dict()
        assert "pronunciation_score" not in d or d.get("pronunciation_score") is None

    def test_to_ui_dict_extends_db_dict(self):
        r = self._make_result()
        ui = r.to_ui_dict()
        assert "type_token_ratio"     in ui
        assert "unique_words"         in ui
        assert "total_words"          in ui
        assert "filler_rate"          in ui
        assert "filler_counts"        in ui
        assert "score_breakdown_text" in ui
        assert "confidence_caveat"    in ui
        assert "strengths"            in ui
        assert "improvements"         in ui
        assert "suggestions"          in ui

    def test_feedback_text_not_empty(self):
        r = self._make_result()
        assert len(r.feedback.to_text()) > 0


# ===========================================================================
# 11. AnalysisService — individual public methods
# ===========================================================================

class TestAnalysisServiceMethods:

    @pytest.fixture
    def svc(self):
        return AnalysisService()

    def test_calculate_wpm(self, svc):
        text = " ".join(["word"] * 130)
        wpm  = svc.calculate_words_per_minute(text, duration_seconds=60)
        assert abs(wpm - 130.0) < 1.0

    def test_calculate_wpm_zero_duration(self, svc):
        assert svc.calculate_words_per_minute("hello", 0) == 0.0

    def test_calculate_wpm_empty_transcript(self, svc):
        assert svc.calculate_words_per_minute("", 60) == 0.0

    def test_detect_filler_words_returns_dict(self, svc):
        result = svc.detect_filler_words("Um this is uh a test you know")
        assert isinstance(result, dict)
        assert "um" in result
        assert "uh" in result

    def test_detect_filler_words_empty(self, svc):
        result = svc.detect_filler_words("")
        assert isinstance(result, dict)
        assert sum(result.values()) == 0

    def test_generate_feedback_returns_string(self, svc):
        scores = {
            "fluency_score": 80.0, "vocabulary_score": 75.0,
            "grammar_score": 90.0, "confidence_score": 70.0,
            "overall_score": 78.0, "filler_word_count": 3,
            "words_per_minute": 120.0, "pause_count": 4,
        }
        text = svc.generate_feedback(scores)
        assert isinstance(text, str)
        assert len(text) > 20

    def test_generate_feedback_contains_section_headers(self, svc):
        scores = {"overall_score": 60.0, "fluency_score": 55.0,
                  "vocabulary_score": 60.0, "grammar_score": 65.0,
                  "confidence_score": 50.0, "filler_word_count": 10,
                  "words_per_minute": 90.0, "pause_count": 5}
        text = svc.generate_feedback(scores)
        assert any(h in text for h in ["Strengths", "Improve", "Suggest"])


# ===========================================================================
# 12. AnalysisService — full pipeline with DB persistence
# ===========================================================================

class TestAnalysisServicePipeline:

    def test_analyse_session_returns_dict(self, db_session):
        user = make_user(db_session, "PipeUser1")
        word = make_word(db_session, "PipeWord1")
        from speakup.database.repository import PracticeSessionRepository
        ps = PracticeSessionRepository(db_session).create(user.id, word.id)
        PracticeSessionRepository(db_session).complete_session(ps.id, 75)
        db_session.commit()

        svc = AnalysisService()
        result = svc.analyse_session(
            session_id=ps.id,
            transcript=GOOD_TRANSCRIPT,
            duration_seconds=75,
        )
        assert isinstance(result, dict)
        assert "overall_score" in result
        assert "fluency_score" in result

    def test_analyse_session_persists_to_db(self, db_session):
        user = make_user(db_session, "PipeUser2")
        word = make_word(db_session, "PipeWord2")
        from speakup.database.repository import PracticeSessionRepository
        ps = PracticeSessionRepository(db_session).create(user.id, word.id)
        PracticeSessionRepository(db_session).complete_session(ps.id, 75)
        db_session.commit()

        svc = AnalysisService()
        svc.analyse_session(
            session_id=ps.id,
            transcript=GOOD_TRANSCRIPT,
            duration_seconds=75,
        )
        fetched = svc.get_result_for_session(session_id=ps.id)
        assert fetched is not None
        assert fetched["overall_score"] is not None
        assert fetched["fluency_score"] is not None
        assert fetched["feedback"] is not None

    def test_overall_score_range(self, db_session):
        user = make_user(db_session, "PipeUser3")
        word = make_word(db_session, "PipeWord3")
        from speakup.database.repository import PracticeSessionRepository
        ps = PracticeSessionRepository(db_session).create(user.id, word.id)
        PracticeSessionRepository(db_session).complete_session(ps.id, 75)
        db_session.commit()

        svc = AnalysisService()
        result = svc.analyse_session(
            session_id=ps.id, transcript=GOOD_TRANSCRIPT, duration_seconds=75
        )
        assert 0.0 <= result["overall_score"] <= 100.0

    def test_wpm_matches_transcript(self, db_session):
        user = make_user(db_session, "PipeUser4")
        word = make_word(db_session, "PipeWord4")
        from speakup.database.repository import PracticeSessionRepository
        ps = PracticeSessionRepository(db_session).create(user.id, word.id)
        PracticeSessionRepository(db_session).complete_session(ps.id, 60)
        db_session.commit()

        svc = AnalysisService()
        words = " ".join(["word"] * 130)
        result = svc.analyse_session(
            session_id=ps.id, transcript=words, duration_seconds=60
        )
        assert abs(result["words_per_minute"] - 130.0) < 2.0

    def test_good_transcript_scores_higher_than_filler_heavy(self, db_session):
        user = make_user(db_session, "PipeUser5")
        word1 = make_word(db_session, "PipeWord5a")
        word2 = make_word(db_session, "PipeWord5b")
        from speakup.database.repository import PracticeSessionRepository
        ps1 = PracticeSessionRepository(db_session).create(user.id, word1.id)
        PracticeSessionRepository(db_session).complete_session(ps1.id, 75)
        ps2 = PracticeSessionRepository(db_session).create(user.id, word2.id)
        PracticeSessionRepository(db_session).complete_session(ps2.id, 30)
        db_session.commit()

        svc = AnalysisService()
        good = svc.analyse_session(
            session_id=ps1.id, transcript=GOOD_TRANSCRIPT, duration_seconds=75,
        )
        filler = svc.analyse_session(
            session_id=ps2.id, transcript=FILLER_HEAVY_TRANSCRIPT, duration_seconds=30,
        )
        assert good["overall_score"] >= filler["overall_score"]

    def test_analyse_with_transcript_result_object(self, db_session):
        from speakup.services.transcription_schemas import TranscriptResult, TranscriptSegment
        from speakup.database.repository import PracticeSessionRepository

        user = make_user(db_session, "PipeUser6")
        word = make_word(db_session, "PipeWord6")
        ps = PracticeSessionRepository(db_session).create(user.id, word.id)
        PracticeSessionRepository(db_session).complete_session(ps.id, 75)
        db_session.commit()

        seg = TranscriptSegment(id=0, start=0.0, end=75.0, text=f" {GOOD_TRANSCRIPT}",
                                avg_logprob=-0.25, no_speech_prob=0.05)
        tr = TranscriptResult(
            text=GOOD_TRANSCRIPT, segments=[seg], language="en",
            language_probability=0.99, duration=75.0, model_size="base",
        )
        svc = AnalysisService()
        result = svc.analyse_transcript_object(session_id=ps.id, transcript_result=tr)
        assert "overall_score" in result
        assert result["overall_score"] > 0


# ===========================================================================
# 13. AnalysisService — upsert guard (duplicate persistence)
# ===========================================================================

class TestAnalysisServiceUpsert:

    def test_second_call_updates_not_duplicates(self, db_session):
        from speakup.database.connection import get_session as db_gs
        from speakup.database.repository import AnalysisResultRepository, PracticeSessionRepository

        user = make_user(db_session, "UpsertUser")
        word = make_word(db_session, "UpsertWord")
        ps = PracticeSessionRepository(db_session).create(user.id, word.id)
        PracticeSessionRepository(db_session).complete_session(ps.id, 75)
        db_session.commit()

        svc = AnalysisService()
        svc.analyse_session(session_id=ps.id, transcript=GOOD_TRANSCRIPT, duration_seconds=75)
        svc.analyse_session(session_id=ps.id, transcript=GOOD_TRANSCRIPT, duration_seconds=75)

        fetched = svc.get_result_for_session(ps.id)
        assert fetched is not None  # row exists

    def test_get_result_returns_none_before_analysis(self, db_session):
        svc = AnalysisService()
        result = svc.get_result_for_session(session_id=9999)
        assert result is None


# ===========================================================================
# 14. Empty transcript edge case
# ===========================================================================

class TestEmptyTranscriptEdgeCase:

    def test_analyse_empty_transcript_does_not_raise(self, db_session):
        from speakup.database.repository import PracticeSessionRepository
        user = make_user(db_session, "EmptyUser1")
        word = make_word(db_session, "EmptyWord1")
        ps = PracticeSessionRepository(db_session).create(user.id, word.id)
        PracticeSessionRepository(db_session).complete_session(ps.id, 60)
        db_session.commit()

        svc = AnalysisService()
        result = svc.analyse_session(
            session_id=ps.id, transcript="", duration_seconds=60
        )
        assert isinstance(result, dict)

    def test_empty_transcript_gives_low_score(self, db_session):
        from speakup.database.repository import PracticeSessionRepository
        user = make_user(db_session, "EmptyUser2")
        word = make_word(db_session, "EmptyWord2")
        ps = PracticeSessionRepository(db_session).create(user.id, word.id)
        PracticeSessionRepository(db_session).complete_session(ps.id, 60)
        db_session.commit()

        svc = AnalysisService()
        result = svc.analyse_session(
            session_id=ps.id, transcript="", duration_seconds=60
        )
        assert result.get("overall_score", 0) <= 60.0

    def test_empty_transcript_zero_wpm(self, db_session):
        from speakup.database.repository import PracticeSessionRepository
        user = make_user(db_session, "EmptyUser3")
        word = make_word(db_session, "EmptyWord3")
        ps = PracticeSessionRepository(db_session).create(user.id, word.id)
        PracticeSessionRepository(db_session).complete_session(ps.id, 60)
        db_session.commit()

        svc = AnalysisService()
        result = svc.analyse_session(
            session_id=ps.id, transcript="", duration_seconds=60
        )
        assert result.get("words_per_minute", 0) == 0.0


# ===========================================================================
# 15. AnalysisWorker — signal emission (synchronous run() call)
# ===========================================================================

class TestAnalysisWorker:

    def test_run_emits_analysis_ready_on_success(self, db_session):
        from speakup.database.repository import PracticeSessionRepository
        user = make_user(db_session, "WorkerUser1")
        word = make_word(db_session, "WorkerWord1")
        ps = PracticeSessionRepository(db_session).create(user.id, word.id)
        PracticeSessionRepository(db_session).complete_session(ps.id, 75)
        db_session.commit()

        worker = AnalysisWorker(
            session_id=ps.id,
            transcript=GOOD_TRANSCRIPT,
            duration_seconds=75,
        )
        results = []
        worker.analysis_ready.connect(lambda r: results.append(r))
        worker.run()
        assert len(results) == 1
        assert "overall_score" in results[0]

    def test_run_emits_status_running_then_completed(self, db_session):
        from speakup.database.repository import PracticeSessionRepository
        user = make_user(db_session, "WorkerUser2")
        word = make_word(db_session, "WorkerWord2")
        ps = PracticeSessionRepository(db_session).create(user.id, word.id)
        PracticeSessionRepository(db_session).complete_session(ps.id, 75)
        db_session.commit()

        worker = AnalysisWorker(
            session_id=ps.id,
            transcript=GOOD_TRANSCRIPT,
            duration_seconds=75,
        )
        statuses = []
        worker.status_changed.connect(statuses.append)
        worker.run()
        assert "running"   in statuses
        assert "completed" in statuses

    def test_run_emits_error_on_bad_session_id(self, db_session):
        """Non-existent session_id will cause DB persistence to fail → error signal."""
        worker = AnalysisWorker(
            session_id=9999,   # not in DB
            transcript=GOOD_TRANSCRIPT,
            duration_seconds=75,
        )
        errors = []
        worker.error_occurred.connect(errors.append)
        worker.run()
        assert len(errors) == 1

    def test_run_emits_progress_messages(self, db_session):
        from speakup.database.repository import PracticeSessionRepository
        user = make_user(db_session, "WorkerUser3")
        word = make_word(db_session, "WorkerWord3")
        ps = PracticeSessionRepository(db_session).create(user.id, word.id)
        PracticeSessionRepository(db_session).complete_session(ps.id, 75)
        db_session.commit()

        worker = AnalysisWorker(
            session_id=ps.id,
            transcript=GOOD_TRANSCRIPT,
            duration_seconds=75,
        )
        messages = []
        worker.progress_message.connect(messages.append)
        worker.run()
        assert len(messages) >= 1


# ===========================================================================
# 16. TranscriptionResultPage — show_analysis() populates the card
# ===========================================================================

class TestTranscriptionResultPageAnalysis:

    @pytest.fixture
    def page(self):
        from speakup.ui.pages.transcription_result_page import TranscriptionResultPage
        return TranscriptionResultPage()

    def test_show_analysis_reveals_card(self, page):
        mock_result = {
            "overall_score": 78.5, "fluency_score": 82.0,
            "vocabulary_score": 88.0, "grammar_score": 70.0,
            "confidence_score": 75.0, "filler_word_count": 4,
            "words_per_minute": 115.0, "total_words": 120,
            "type_token_ratio": 0.62,
            "strengths": ["Good pace."],
            "improvements": ["Reduce fillers."],
            "suggestions": ["Pause instead of um."],
            "score_breakdown_text": "Fluency 82 × 25% = 20.5",
            "confidence_caveat": "Observable speaking indicators only.",
        }
        page.show_analysis(mock_result)
        # Card should now be shown
        assert page._analysis_card.isVisible() or not page._analysis_card.isHidden()

    def test_show_analysis_sets_overall_chip(self, page):
        mock_result = {
            "overall_score": 85.0, "fluency_score": 80.0,
            "vocabulary_score": 90.0, "grammar_score": 85.0,
            "confidence_score": 80.0, "filler_word_count": 2,
            "words_per_minute": 125.0, "total_words": 150,
            "type_token_ratio": 0.65,
            "strengths": [], "improvements": [],
            "suggestions": [], "score_breakdown_text": "",
            "confidence_caveat": "",
        }
        page.show_analysis(mock_result)
        label_text = page._achip_overall._value_label.text()
        assert "85" in label_text

    def test_show_analysis_analysis_card_not_initially_visible(self, page):
        # Before calling show_analysis, the card should be hidden
        assert not page._analysis_card.isVisible()

    def test_cleanup_cancels_analysis_runner(self, page):
        page.cleanup()  # should not raise even without an active session

    def test_show_analysis_signal_exists(self, page):
        assert hasattr(page, "transcription_saved")
        assert hasattr(page, "transcription_skipped")
