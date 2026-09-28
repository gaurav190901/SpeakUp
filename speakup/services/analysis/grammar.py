"""
speakup/services/analysis/grammar.py
──────────────────────────────────────
Grammar analysis — modular interface.

Architecture
────────────
BaseGrammarAnalyzer (ABC)
    └── RuleBasedGrammarAnalyzer  — pure Python, no API, always available
    └── LLMGrammarAnalyzer        — stub, requires external API (future chunk)

The factory function ``get_grammar_analyzer()`` returns the right
implementation based on ``settings.ANALYSIS_GRAMMAR_BACKEND``.

Design rationale
────────────────
Full grammar checking requires NLP tools (LanguageTool, spaCy) which are
large dependencies not present in the current requirements.  The rule-based
analyser detects a pragmatic set of high-frequency speaking errors using
regex and simple heuristics — sufficient to give actionable feedback without
any external calls.

The LLM analyser is a clean hook point for Chunk 6+ integration.
"""

from __future__ import annotations

import re
import logging
from abc import ABC, abstractmethod
from typing import Optional

from speakup.config.settings import settings
from speakup.services.analysis.schemas import GrammarMetrics

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BaseGrammarAnalyzer(ABC):
    """Contract every grammar backend must fulfil."""

    @abstractmethod
    def analyse(self, text: str) -> GrammarMetrics:
        """
        Analyse *text* and return a :class:`GrammarMetrics` instance.

        Args:
            text: Full transcript text (as produced by Whisper).

        Returns:
            GrammarMetrics with issues list, count, score, and notes.
        """

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Short identifier, e.g. ``'rule_based'`` or ``'llm'``."""


# ---------------------------------------------------------------------------
# Rule-based implementation (always available, no external deps)
# ---------------------------------------------------------------------------

# Each rule is (description, regex_pattern, suggestion)
# Written for informal transcribed speech where punctuation is absent/sparse
_RULES: list[tuple[str, re.Pattern, str]] = [
    (
        "Double negation",
        re.compile(r"\b(don't|didn't|doesn't|can't|won't|isn't|wasn't)\b.{0,25}"
                   r"\b(nothing|nobody|nowhere|no one|never|neither)\b",
                   re.IGNORECASE),
        "Use a single negation (e.g. 'I don't know anything' not 'I don't know nothing').",
    ),
    (
        "Incorrect subject-verb agreement (I was/were)",
        re.compile(r"\bi\s+were\b", re.IGNORECASE),
        "Use 'I was' not 'I were' in indicative sentences.",
    ),
    (
        "Incorrect past tense 'seen'",
        re.compile(r"\b(i|we|he|she|they|you)\s+seen\b", re.IGNORECASE),
        "Use 'saw' as simple past (e.g. 'I saw') or 'have seen' as present perfect.",
    ),
    (
        "Incorrect past tense 'done'",
        re.compile(r"\b(i|we|he|she|they|you)\s+done\b(?!\s+well)", re.IGNORECASE),
        "Use 'did' as simple past (e.g. 'I did') or 'have done' as present perfect.",
    ),
    (
        "Incorrect past tense 'went'",
        re.compile(r"\bhave\s+went\b", re.IGNORECASE),
        "Use 'have gone' (present perfect) or 'went' (simple past) — not 'have went'.",
    ),
    (
        "Informal 'could of/should of/would of'",
        re.compile(r"\b(could|should|would|must|might|may)\s+of\b", re.IGNORECASE),
        "Use 'could have', 'should have', 'would have' — 'of' is not a verb here.",
    ),
    (
        "Non-standard plural (peoples/stuffs/informations)",
        re.compile(r"\b(peoples|stuffs|informations|advices|furnitures|equipments|evidences)\b",
                   re.IGNORECASE),
        "These words are uncountable; avoid adding -s (use 'people', 'information', 'advice').",
    ),
    (
        "Misuse of 'less' with countable nouns",
        re.compile(r"\bless\s+(people|items|words|things|points|students|cars|problems|errors)\b",
                   re.IGNORECASE),
        "Use 'fewer' with countable nouns (e.g. 'fewer people', 'fewer items').",
    ),
    (
        "Sentences starting with conjunctions (structural note)",
        re.compile(
            r"(?:^|\.\s+)(But|And|Because|So)\s+(?:i\b|we\b|it\b|they\b|he\b|she\b)",
            re.IGNORECASE | re.MULTILINE,
        ),
        "Starting sentences with conjunctions can weaken structure in formal speech.",
    ),
    (
        "Repetition of 'basically/literally/actually'",
        re.compile(
            r"(?:(?:basically|literally|actually).*?){3}",
            re.IGNORECASE | re.DOTALL,
        ),
        "Over-using hedge words (basically, literally, actually) dilutes your message.",
    ),
]


class RuleBasedGrammarAnalyzer(BaseGrammarAnalyzer):
    """
    Heuristic grammar checker using regex patterns.

    Detects common spoken-English errors detectable without an NLP pipeline.
    Each issue is reported once with a plain-English description.
    """

    @property
    def backend_name(self) -> str:
        return "rule_based"

    def analyse(self, text: str) -> GrammarMetrics:
        if not text.strip():
            return GrammarMetrics(
                backend=self.backend_name,
                issues=[],
                issue_count=0,
                score=50.0,
                notes="No transcript available for grammar analysis.",
            )

        issues: list[str] = []
        seen: set[str] = set()

        for description, pattern, suggestion in _RULES:
            if pattern.search(text):
                key = description
                if key not in seen:
                    issues.append(f"{description}: {suggestion}")
                    seen.add(key)

        score = self._score(len(issues))

        logger.debug(
            "Grammar (rule_based): issues=%d  score=%.1f",
            len(issues), score,
        )

        return GrammarMetrics(
            backend=self.backend_name,
            issues=issues,
            issue_count=len(issues),
            score=score,
            notes=(
                "Rule-based analysis detects common spoken-English patterns. "
                "For deeper grammar feedback, configure ANALYSIS_GRAMMAR_BACKEND=llm."
            ),
        )

    @staticmethod
    def _score(issue_count: int) -> float:
        """
        Map issue count to a 0–100 score.

        0 issues → 90 (no detected errors; some grammar may still be imperfect)
        1 issue  → 75
        2 issues → 60
        3 issues → 45
        4+       → 25–40
        5+       → ≤25
        """
        table = {0: 90.0, 1: 75.0, 2: 60.0, 3: 45.0, 4: 35.0}
        if issue_count in table:
            return table[issue_count]
        return max(10.0, 30.0 - (issue_count - 4) * 5.0)


# ---------------------------------------------------------------------------
# LLM stub (future)
# ---------------------------------------------------------------------------

class LLMGrammarAnalyzer(BaseGrammarAnalyzer):
    """
    Grammar analysis via an external LLM API.

    Currently a stub — returns a polite "not configured" result.
    Will be implemented in a future chunk when API integration is added.
    """

    @property
    def backend_name(self) -> str:
        return "llm"

    def analyse(self, text: str) -> GrammarMetrics:
        logger.warning("LLMGrammarAnalyzer called but no API is configured.")
        return GrammarMetrics(
            backend=self.backend_name,
            issues=[],
            issue_count=0,
            score=50.0,  # neutral — no data
            notes=(
                "LLM grammar analysis is not yet configured. "
                "Set OPENAI_API_KEY and configure the grammar backend to enable it."
            ),
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_grammar_analyzer(
    backend: str | None = None,
) -> BaseGrammarAnalyzer:
    """
    Return the configured grammar analyser.

    Args:
        backend: Override ``settings.ANALYSIS_GRAMMAR_BACKEND``.
                 ``'rule_based'`` | ``'llm'``.
    """
    b = backend or settings.ANALYSIS_GRAMMAR_BACKEND
    if b == "rule_based":
        return RuleBasedGrammarAnalyzer()
    if b == "llm":
        return LLMGrammarAnalyzer()
    logger.warning("Unknown grammar backend %r — falling back to rule_based", b)
    return RuleBasedGrammarAnalyzer()
