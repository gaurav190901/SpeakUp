"""
speakup/services/analysis/feedback.py
───────────────────────────────────────
Rule-based feedback generator.

Produces:
    • Strengths  (2–3 things the speaker did well)
    • Areas to improve  (1–3 specific weaknesses)
    • Actionable suggestions  (concrete next-steps tied to each weakness)

All logic is deterministic — same inputs produce the same feedback.
No external API or ML model required.
"""

from __future__ import annotations

import logging

from speakup.services.analysis.schemas import (
    ConfidenceMetrics,
    FluencyMetrics,
    FillerMetrics,
    FeedbackBundle,
    GrammarMetrics,
    OverallScore,
    VocabularyMetrics,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds for feedback branches
# ---------------------------------------------------------------------------

_EXCELLENT = 80
_GOOD      = 65
_FAIR      = 45


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_feedback(
    fluency:    FluencyMetrics,
    vocabulary: VocabularyMetrics,
    filler:     FillerMetrics,
    grammar:    GrammarMetrics,
    confidence: ConfidenceMetrics,
    overall:    OverallScore,
) -> FeedbackBundle:
    """
    Generate structured feedback for the user.

    Returns:
        :class:`FeedbackBundle` with strengths, improvements, and suggestions.
    """
    strengths:    list[str] = []
    improvements: list[str] = []
    suggestions:  list[str] = []

    # ── Fluency ─────────────────────────────────────────────────────────
    wpm = fluency.words_per_minute
    if fluency.score >= _EXCELLENT:
        strengths.append(
            f"Excellent speaking pace ({wpm:.0f} words/min) — natural and easy to follow."
        )
    elif fluency.score >= _GOOD:
        strengths.append(
            f"Good speaking pace ({wpm:.0f} words/min) — within a comfortable range."
        )
    elif fluency.score < _FAIR:
        if wpm < 80:
            improvements.append(
                "Speaking pace is noticeably slow, which can reduce listener engagement."
            )
            suggestions.append(
                "Try reading aloud daily for 5 minutes to build speaking momentum. "
                "Aim for 100–130 words/min."
            )
        elif wpm > 180:
            improvements.append(
                "Speaking pace is quite fast, which may make it harder to understand."
            )
            suggestions.append(
                "Practise pausing after key points. Record yourself and review "
                "whether ideas are landing clearly."
            )

    if fluency.pause_count > 8:
        improvements.append(
            f"Frequent pauses detected ({fluency.pause_count}) — this can disrupt your flow."
        )
        suggestions.append(
            "Pause deliberately between ideas rather than mid-sentence. "
            "Silences are natural and give the listener time to absorb your point."
        )
    elif fluency.pause_count <= 2 and fluency.score >= _GOOD:
        strengths.append("Very smooth delivery with minimal disruptive pauses.")

    # ── Vocabulary ───────────────────────────────────────────────────────
    if vocabulary.score >= _EXCELLENT:
        strengths.append(
            f"Rich vocabulary — {vocabulary.unique_words} unique words, "
            f"impressive diversity ({vocabulary.type_token_ratio:.0%} type-token ratio)."
        )
    elif vocabulary.score >= _GOOD:
        strengths.append(
            f"Good range of vocabulary ({vocabulary.unique_words} unique words)."
        )
    else:
        improvements.append(
            f"Vocabulary diversity is limited "
            f"({vocabulary.unique_words} unique out of {vocabulary.total_words} words)."
        )
        if vocabulary.repeated_words:
            top = list(vocabulary.repeated_words.keys())[:3]
            suggestions.append(
                f"You repeated the word(s) '{', '.join(top)}' frequently. "
                "Try to vary your word choice or use synonyms."
            )
        else:
            suggestions.append(
                "Expand your vocabulary by reading English content daily — "
                "newspapers, books, or podcasts — and noting interesting words."
            )

    # ── Filler words ─────────────────────────────────────────────────────
    if filler.score >= _EXCELLENT:
        strengths.append(
            "Excellent control of filler words — very few hesitation markers detected."
        )
    elif filler.score < _FAIR:
        top_fillers = sorted(filler.counts.items(), key=lambda x: -x[1])[:3]
        filler_str = ", ".join(f'"{w}" ({c}×)' for w, c in top_fillers)
        improvements.append(
            f"Filler words appeared {filler.total_filler_count} times "
            f"(most common: {filler_str})."
        )
        suggestions.append(
            "When you feel a filler word coming, replace it with a brief silence. "
            "A 1-second pause sounds more confident than 'um' or 'like'."
        )
    elif filler.total_filler_count > 0:
        improvements.append(
            f"A few filler words detected ({filler.total_filler_count} total) — "
            "minor, but worth being aware of."
        )

    # ── Grammar ──────────────────────────────────────────────────────────
    if grammar.score >= _EXCELLENT:
        strengths.append("No significant grammar issues detected — clean and correct English.")
    elif grammar.score < _FAIR:
        improvements.append(
            f"{grammar.issue_count} grammar pattern(s) detected that may affect clarity."
        )
        for issue in grammar.issues[:2]:   # show at most 2 grammar suggestions
            suggestions.append(issue)
    elif grammar.issue_count > 0:
        suggestions.append(
            f"Minor grammar note: {grammar.issues[0].split(':')[0] if grammar.issues else 'review sentence structure'}."
        )

    # ── Confidence estimate ───────────────────────────────────────────────
    if confidence.score >= _EXCELLENT:
        strengths.append(
            "Strong speaking confidence indicators — consistent pace, varied vocabulary, "
            "and smooth delivery all contribute."
        )
    elif confidence.score >= _GOOD:
        pass  # covered by other dimensions
    else:
        improvements.append(
            "Speaking confidence indicators suggest some hesitation — "
            "this is completely normal and improves with practice."
        )
        suggestions.append(
            "Try practising in front of a mirror or recording yourself daily for 2 minutes. "
            "Familiarity with seeing and hearing yourself speaking greatly reduces anxiety."
        )

    # ── Overall framing ──────────────────────────────────────────────────
    if overall.total >= _EXCELLENT:
        suggestions.insert(0, "Keep up the great work! Challenge yourself with more advanced topics.")
    elif overall.total >= _GOOD:
        suggestions.append(
            "Consistent practice — even 1–2 minutes of speaking daily — "
            "produces measurable improvement within weeks."
        )
    else:
        suggestions.append(
            "Don't be discouraged — every session builds the habit of speaking. "
            "Focus on one dimension at a time rather than trying to fix everything at once."
        )

    # Ensure at least one entry in each category
    if not strengths:
        strengths.append(
            f"You completed a {int(fluency.words_per_minute * 1):0.0f} wpm "
            "practice session — completing is the first step to improving."
        )
    if not improvements:
        improvements.append(
            "Keep challenging yourself with varied topics to maintain your progress."
        )

    logger.debug(
        "Feedback generated: strengths=%d  improvements=%d  suggestions=%d",
        len(strengths), len(improvements), len(suggestions),
    )

    return FeedbackBundle(
        strengths=strengths,
        improvements=improvements,
        suggestions=suggestions,
    )
