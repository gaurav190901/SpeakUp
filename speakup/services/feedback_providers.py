"""
speakup/services/feedback_providers.py
──────────────────────────────────────────
Provider abstraction for the AI feedback engine.

Architecture
────────────
BaseFeedbackProvider  (ABC)
    └── LocalFeedbackProvider   — always available, rule-based, no external API
    └── LLMFeedbackProvider     — calls OpenAI API; requires OPENAI_API_KEY

Design principles
─────────────────
1. Never fabricate mistakes.  LocalFeedbackProvider only reports issues that
   are directly evidenced by the transcript text.
2. Grammar corrections must cite the actual phrase from the transcript.
3. The LLM prompt explicitly instructs the model not to invent errors.
4. API keys are read exclusively from the environment — never from source code.
5. If the LLM call fails for any reason, the service falls back to local analysis.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from collections import Counter
from typing import Optional

from speakup.config.settings import settings
from speakup.services.feedback_schemas import (
    AIFeedbackResult,
    FeedbackRequest,
    GrammarCorrection,
    VocabularySuggestion,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Abstract base
# ============================================================================

class BaseFeedbackProvider(ABC):
    """
    Contract every feedback backend must fulfil.

    A provider is stateless at the call level — create once, call ``analyze()``
    multiple times.
    """

    @abstractmethod
    def analyze(self, request: FeedbackRequest) -> AIFeedbackResult:
        """
        Analyse the practice session and return structured feedback.

        Must never fabricate errors.  Only report issues that are directly
        evidenced by the transcript or metrics.

        Args:
            request: All available session data.

        Returns:
            :class:`AIFeedbackResult` — may be empty if nothing noteworthy found.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Return True when this provider can actually run (API key set, etc.)."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Short identifier, e.g. ``'local'`` or ``'llm'``."""


class FeedbackProviderError(RuntimeError):
    """Raised when a provider encounters a non-recoverable error."""


# ============================================================================
# LocalFeedbackProvider
# ============================================================================

# Words worth suggesting synonyms for (high-frequency content words)
_SYNONYM_MAP: dict[str, list[str]] = {
    "important":   ["crucial", "significant", "essential", "vital"],
    "good":        ["effective", "beneficial", "valuable", "positive"],
    "bad":         ["harmful", "problematic", "detrimental", "negative"],
    "big":         ["substantial", "considerable", "significant", "major"],
    "small":       ["minor", "modest", "limited", "marginal"],
    "show":        ["demonstrate", "illustrate", "reveal", "highlight"],
    "use":         ["utilise", "employ", "apply", "leverage"],
    "get":         ["obtain", "acquire", "achieve", "gain"],
    "make":        ["create", "develop", "produce", "establish"],
    "think":       ["believe", "consider", "argue", "suggest"],
    "things":      ["aspects", "elements", "factors", "components"],
    "people":      ["individuals", "professionals", "practitioners"],
    "very":        ["particularly", "extremely", "notably", "considerably"],
    "really":      ["genuinely", "truly", "significantly", "substantially"],
    "a lot":       ["considerably", "significantly", "substantially"],
    "because":     ["since", "as", "given that", "due to the fact that"],
    "problem":     ["challenge", "issue", "concern", "obstacle"],
    "help":        ["assist", "support", "facilitate", "enable"],
    "change":      ["transform", "evolve", "adapt", "shift"],
    "different":   ["distinct", "varied", "diverse", "alternative"],
}

# Minimum count before a word is flagged as "repeated"
_REPEAT_THRESHOLD = 3

# Minimum word length to include in vocabulary analysis (skip function words)
_MIN_CONTENT_LEN = 4

_STOP_WORDS: frozenset[str] = frozenset({
    "the", "and", "that", "this", "with", "they", "have", "from",
    "about", "there", "their", "what", "when", "your", "also",
    "just", "more", "then", "than", "some", "been", "were", "into",
    "each", "well", "like", "will", "would", "could", "should",
    "which", "where", "while", "these", "those", "such", "very",
    "much", "many", "most", "over", "even", "only", "both", "back",
    "them", "being", "every", "need", "said", "does", "dont",
    "here", "make", "come", "going", "really", "think", "know",
})


def _tokenise(text: str) -> list[str]:
    """Lowercase alphabetic tokens only."""
    return re.findall(r"[a-z]+", text.lower())


class LocalFeedbackProvider(BaseFeedbackProvider):
    """
    Rule-based feedback provider.  No external dependencies.

    Analyses:
        - Grammar patterns (reuses existing RuleBasedGrammarAnalyzer rules)
        - Vocabulary repetition and synonym suggestions
        - Filler-word reduction tips (with specific counts)
        - Repeated-word suggestions (word → alternative)

    Guarantees:
        - Only flags grammar issues that match specific regex patterns
        - Only suggests synonyms for words that actually appear in the transcript
        - Only flags repeated words that appear ≥ REPEAT_THRESHOLD times
        - Never invents issues
    """

    def is_available(self) -> bool:
        return True   # always available

    @property
    def backend_name(self) -> str:
        return "local"

    def analyze(self, request: FeedbackRequest) -> AIFeedbackResult:
        text = request.transcript.strip()
        if not text:
            return AIFeedbackResult(
                source="local",
                model="local/rule-based",
                warning="No transcript available for analysis.",
            )

        grammar_corrections     = self._check_grammar(text)
        vocabulary_suggestions  = self._vocabulary_suggestions(text)
        filler_tips             = self._filler_tips(request)
        repeated_tips           = self._repeated_word_tips(text, vocabulary_suggestions)

        return AIFeedbackResult(
            source="local",
            model="local/rule-based",
            grammar_corrections=grammar_corrections,
            vocabulary_suggestions=vocabulary_suggestions,
            filler_reduction_tips=filler_tips,
            repeated_word_tips=repeated_tips,
        )

    # ── Grammar ──────────────────────────────────────────────────────────

    def _check_grammar(self, text: str) -> list[GrammarCorrection]:
        """
        Reuse the RuleBasedGrammarAnalyzer rules but return GrammarCorrection
        objects with the actual phrase extracted from the transcript.
        """
        from speakup.services.analysis.grammar import _RULES  # noqa: PLC0415

        corrections: list[GrammarCorrection] = []
        seen: set[str] = set()

        for description, pattern, suggestion in _RULES:
            match = pattern.search(text)
            if match and description not in seen:
                # Extract the matched phrase (at most 60 chars)
                phrase = match.group(0)[:60].strip()
                corrections.append(GrammarCorrection(
                    original=phrase,
                    suggestion=suggestion.split(":")[0].strip(),
                    explanation=suggestion,
                ))
                seen.add(description)

        return corrections

    # ── Vocabulary ────────────────────────────────────────────────────────

    def _vocabulary_suggestions(self, text: str) -> list[VocabularySuggestion]:
        """
        Suggest richer alternatives for high-frequency content words that
        appear in the configured synonym map.
        """
        tokens = _tokenise(text)
        counts = Counter(tokens)
        suggestions: list[VocabularySuggestion] = []

        for word, alts in _SYNONYM_MAP.items():
            # Handle multi-word phrases (e.g. "a lot")
            if " " in word:
                phrase_count = len(re.findall(
                    rf"\b{re.escape(word)}\b", text.lower()
                ))
                if phrase_count >= 2:
                    suggestions.append(VocabularySuggestion(
                        word=word,
                        count=phrase_count,
                        alternatives=alts[:3],
                    ))
            else:
                if counts.get(word, 0) >= 2:
                    suggestions.append(VocabularySuggestion(
                        word=word,
                        count=counts[word],
                        alternatives=alts[:3],
                    ))

        # Sort by count descending — most repeated first
        suggestions.sort(key=lambda s: -s.count)
        return suggestions[:6]   # cap at 6 suggestions

    # ── Filler words ─────────────────────────────────────────────────────

    def _filler_tips(self, request: FeedbackRequest) -> list[str]:
        """
        Generate actionable filler-reduction tips based on actual counts.
        Only generated when fillers are actually present.
        """
        tips: list[str] = []
        total = request.filler_word_count

        if total == 0:
            return tips

        # Top 3 fillers by count
        top = sorted(request.filler_counts.items(), key=lambda x: -x[1])[:3]
        if top:
            examples = ", ".join(f'"{w}" ({c}×)' for w, c in top)
            tips.append(
                f"Your most frequent filler words were: {examples}. "
                "When you feel a filler coming, replace it with a deliberate pause — "
                "silence sounds more confident than hesitation markers."
            )

        if total >= 10:
            tips.append(
                f"You used {total} filler words in this session. "
                "Try recording yourself for 2 minutes each day without fillers — "
                "awareness alone reduces frequency significantly within a week."
            )
        elif total >= 5:
            tips.append(
                f"With {total} filler words, there is noticeable room to improve. "
                "Slow down slightly — most fillers appear when speaking faster "
                "than you can formulate thoughts."
            )

        return tips

    # ── Repeated words ────────────────────────────────────────────────────

    def _repeated_word_tips(
        self,
        text: str,
        vocab_suggestions: list[VocabularySuggestion],
    ) -> list[str]:
        """
        Tips for content words repeated ≥ REPEAT_THRESHOLD times that are
        NOT already covered by vocabulary_suggestions.
        """
        already_flagged = {s.word for s in vocab_suggestions}
        tokens = _tokenise(text)
        counts = Counter(
            t for t in tokens
            if len(t) >= _MIN_CONTENT_LEN
            and t not in _STOP_WORDS
            and t not in already_flagged
        )

        tips: list[str] = []
        for word, count in counts.most_common(4):
            if count >= _REPEAT_THRESHOLD:
                tips.append(
                    f"The word \"{word}\" appeared {count} times. "
                    "Consider using synonyms or restructuring sentences to vary your language."
                )
        return tips


# ============================================================================
# LLMFeedbackProvider
# ============================================================================

_SYSTEM_PROMPT = """You are an expert English speaking coach reviewing a student's practice session transcript.

Your task: provide honest, evidence-based feedback to help the student improve.

CRITICAL RULES:
1. NEVER fabricate grammar errors. Only report errors that are clearly present in the transcript.
2. NEVER invent mistakes. If the grammar is fine, say so or omit that section.
3. Every grammar correction MUST cite the exact phrase from the transcript.
4. Only suggest alternatives for sentences that actually appear in the transcript.
5. Be encouraging and specific. Vague feedback is unhelpful.
6. Keep responses concise — the student will read this immediately after speaking.

Return a JSON object with exactly this structure:
{
  "grammar_corrections": [
    {"original": "...", "suggestion": "...", "explanation": "..."}
  ],
  "better_sentence_alternatives": [
    "Original: '...'  →  Better: '...'"
  ],
  "vocabulary_suggestions": [
    {"word": "...", "count": N, "alternatives": ["...", "...", "..."]}
  ],
  "topic_specific_feedback": [
    "..."
  ],
  "personalized_practice_advice": [
    "..."
  ]
}

Return ONLY the JSON object. No markdown, no explanation outside the JSON."""


def _build_user_prompt(request: FeedbackRequest) -> str:
    """Build the user message for the LLM from the FeedbackRequest."""
    parts: list[str] = [
        f"TOPIC: {request.topic}" + (f" ({request.category})" if request.category else ""),
        f"DURATION: {request.duration_seconds}s",
        f"METRICS: WPM={request.words_per_minute:.0f}, "
        f"Fluency={request.fluency_score:.0f}/100, "
        f"Vocabulary={request.vocabulary_score:.0f}/100, "
        f"Grammar={request.grammar_score:.0f}/100, "
        f"Confidence={request.confidence_score:.0f}/100",
    ]

    if request.filler_word_count > 0:
        top = sorted(request.filler_counts.items(), key=lambda x: -x[1])[:5]
        filler_str = ", ".join(f"{w}({c})" for w, c in top)
        parts.append(f"FILLER WORDS: {request.filler_word_count} total — {filler_str}")

    if request.prior_avg_overall is not None:
        parts.append(
            f"PREVIOUS AVERAGE: overall={request.prior_avg_overall:.0f}/100, "
            f"confidence={request.prior_avg_confidence:.0f}/100"
            if request.prior_avg_confidence else
            f"PREVIOUS AVERAGE: overall={request.prior_avg_overall:.0f}/100"
        )

    parts.append("")
    parts.append("TRANSCRIPT:")
    parts.append(request.transcript)

    return "\n".join(parts)


class LLMFeedbackProvider(BaseFeedbackProvider):
    """
    AI-powered feedback via the OpenAI Chat Completions API.

    The API key is read exclusively from the environment variable
    OPENAI_API_KEY.  It is NEVER stored in source code or settings files.

    Error handling:
        - Missing API key     → returns empty result with warning
        - Network timeout     → returns empty result with error
        - API error (4xx/5xx) → returns empty result with error
        - Malformed JSON      → falls back to local-only result
        - Short transcript    → skips LLM, uses local only
    """

    def is_available(self) -> bool:
        return settings.has_openai_key()

    @property
    def backend_name(self) -> str:
        return "llm"

    def analyze(self, request: FeedbackRequest) -> AIFeedbackResult:
        if not self.is_available():
            logger.info("LLMFeedbackProvider: no API key configured")
            return AIFeedbackResult.empty(
                error="OpenAI API key not configured.",
                source="llm",
            )

        word_count = len(request.transcript.split())
        if word_count < settings.AI_FEEDBACK_MIN_WORDS:
            logger.info(
                "LLMFeedbackProvider: transcript too short (%d words < min %d)",
                word_count, settings.AI_FEEDBACK_MIN_WORDS,
            )
            return AIFeedbackResult.empty(
                error=f"Transcript too short for AI analysis ({word_count} words).",
                source="llm",
            )

        try:
            return self._call_api(request)
        except FeedbackProviderError as exc:
            logger.error("LLMFeedbackProvider error: %s", exc)
            return AIFeedbackResult(
                source="llm",
                model=settings.AI_FEEDBACK_MODEL,
                error=str(exc),
            )

    def _call_api(self, request: FeedbackRequest) -> AIFeedbackResult:
        """
        Make the OpenAI API call and parse the structured JSON response.
        Lazy-imports ``openai`` so the dependency is optional at import time.
        """
        try:
            import openai  # noqa: PLC0415  (lazy — optional dependency)
        except ImportError as exc:
            raise FeedbackProviderError(
                "openai package not installed. "
                "Run: pip install openai"
            ) from exc

        api_key = settings.OPENAI_API_KEY
        client = openai.OpenAI(api_key=api_key)

        try:
            response = client.chat.completions.create(
                model=settings.AI_FEEDBACK_MODEL,
                messages=[
                    {"role": "system",  "content": _SYSTEM_PROMPT},
                    {"role": "user",    "content": _build_user_prompt(request)},
                ],
                max_tokens=settings.AI_FEEDBACK_MAX_TOKENS,
                temperature=0.3,   # low temp = more deterministic, fewer hallucinations
                timeout=settings.AI_FEEDBACK_TIMEOUT_S,
                response_format={"type": "json_object"},
            )
        except openai.AuthenticationError as exc:
            raise FeedbackProviderError(f"Invalid OpenAI API key: {exc}") from exc
        except openai.RateLimitError as exc:
            raise FeedbackProviderError(f"OpenAI rate limit exceeded: {exc}") from exc
        except openai.APITimeoutError as exc:
            raise FeedbackProviderError(
                f"OpenAI API timed out after {settings.AI_FEEDBACK_TIMEOUT_S}s"
            ) from exc
        except openai.APIConnectionError as exc:
            raise FeedbackProviderError(f"Could not connect to OpenAI: {exc}") from exc
        except openai.APIError as exc:
            raise FeedbackProviderError(f"OpenAI API error: {exc}") from exc

        raw = response.choices[0].message.content or "{}"
        return self._parse_response(raw)

    def _parse_response(self, raw_json: str) -> AIFeedbackResult:
        """Parse the LLM JSON response into an AIFeedbackResult."""
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            logger.warning("LLM response was not valid JSON: %s", exc)
            return AIFeedbackResult(
                source="llm",
                model=settings.AI_FEEDBACK_MODEL,
                error="LLM returned invalid JSON — could not parse response.",
            )

        grammar_corrections = [
            GrammarCorrection(
                original=g.get("original", ""),
                suggestion=g.get("suggestion", ""),
                explanation=g.get("explanation", ""),
            )
            for g in data.get("grammar_corrections", [])
            if g.get("original")   # skip entries without a cited phrase
        ]

        vocabulary_suggestions = [
            VocabularySuggestion(
                word=v.get("word", ""),
                count=int(v.get("count", 1)),
                alternatives=v.get("alternatives", [])[:4],
            )
            for v in data.get("vocabulary_suggestions", [])
            if v.get("word")
        ]

        return AIFeedbackResult(
            source="llm",
            model=settings.AI_FEEDBACK_MODEL,
            grammar_corrections=grammar_corrections,
            vocabulary_suggestions=vocabulary_suggestions,
            better_sentence_alternatives=data.get("better_sentence_alternatives", []),
            topic_specific_feedback=data.get("topic_specific_feedback", []),
            personalized_practice_advice=data.get("personalized_practice_advice", []),
        )


# ============================================================================
# Factory
# ============================================================================

def get_feedback_provider(
    backend: str | None = None,
) -> BaseFeedbackProvider:
    """
    Return the configured feedback provider.

    Args:
        backend: Override ``settings.AI_FEEDBACK_BACKEND``.
                 ``'local'`` | ``'llm'``.

    The LLM provider is only returned when the backend is ``'llm'`` AND
    ``settings.has_openai_key()`` is True.  Otherwise LocalFeedbackProvider
    is returned as the safe default.
    """
    b = backend or settings.AI_FEEDBACK_BACKEND

    if b == "llm":
        provider = LLMFeedbackProvider()
        if provider.is_available():
            return provider
        logger.info(
            "AI_FEEDBACK_BACKEND=llm but no API key found — using local provider"
        )
    return LocalFeedbackProvider()
