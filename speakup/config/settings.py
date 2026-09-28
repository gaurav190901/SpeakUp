"""
Application settings loaded from environment variables / .env file.
All configuration is centralised here — import Settings wherever needed.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Resolve project root (two levels up from this file: config/ -> speakup/ -> project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Load .env if present (silently ignored when missing)
_env_path = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=_env_path, override=False)


class Settings:
    """Central configuration object."""

    # ------------------------------------------------------------------ #
    #  Application                                                          #
    # ------------------------------------------------------------------ #
    APP_NAME: str = os.getenv("APP_NAME", "SpeakUp")
    APP_VERSION: str = os.getenv("APP_VERSION", "0.1.0")
    APP_ENV: str = os.getenv("APP_ENV", "development")

    # ------------------------------------------------------------------ #
    #  Paths                                                                #
    # ------------------------------------------------------------------ #
    PROJECT_ROOT: Path = PROJECT_ROOT
    DATA_DIR: Path = PROJECT_ROOT / "speakup" / "data"
    RECORDINGS_DIR: Path = PROJECT_ROOT / os.getenv("RECORDINGS_DIR", "recordings")
    ASSETS_DIR: Path = PROJECT_ROOT / "speakup" / "assets"
    WORDS_DIR: Path = PROJECT_ROOT / "speakup" / "data" / "words"

    # ------------------------------------------------------------------ #
    #  Database                                                             #
    # ------------------------------------------------------------------ #
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{PROJECT_ROOT / 'speakup.db'}",
    )

    # ------------------------------------------------------------------ #
    #  Logging                                                              #
    # ------------------------------------------------------------------ #
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: str = os.getenv("LOG_FILE", str(PROJECT_ROOT / "speakup.log"))

    # ------------------------------------------------------------------ #
    #  Default User                                                         #
    # ------------------------------------------------------------------ #
    DEFAULT_USER_NAME: str = os.getenv("DEFAULT_USER_NAME", "Local User")

    # ------------------------------------------------------------------ #
    #  Practice Thresholds                                                  #
    # ------------------------------------------------------------------ #
    MIN_SPEAKING_DURATION: int = int(os.getenv("MIN_SPEAKING_DURATION", "60"))
    TARGET_WORDS_PER_MINUTE: int = int(os.getenv("TARGET_WORDS_PER_MINUTE", "130"))

    # ------------------------------------------------------------------ #
    #  Transcription                                                        #
    # ------------------------------------------------------------------ #
    # Backend: "faster_whisper" (default, local CPU) | "mock" (tests only)
    TRANSCRIPTION_BACKEND: str = os.getenv("TRANSCRIPTION_BACKEND", "faster_whisper")

    # Whisper model size — tradeoff: accuracy vs speed on CPU
    # Options: "tiny", "base", "small", "medium", "large-v2", "large-v3"
    # "base" is a good default: ~150 MB, ~4-6× real-time on modern CPU
    WHISPER_MODEL_SIZE: str = os.getenv("WHISPER_MODEL_SIZE", "base")

    # Optional: path to a locally downloaded model directory.
    # When empty the model is downloaded from HuggingFace Hub on first use.
    WHISPER_MODEL_PATH: str = os.getenv("WHISPER_MODEL_PATH", "")

    # Inference device: "cpu" or "cuda" (GPU, if available)
    WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "cpu")

    # CTranslate2 compute type — affects speed/accuracy on CPU
    # "int8" is fastest on CPU; "float16" for GPU; "float32" for highest accuracy
    WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

    # Language hint — empty string means auto-detect
    WHISPER_LANGUAGE: str = os.getenv("WHISPER_LANGUAGE", "en")

    # Number of CTranslate2 worker threads
    WHISPER_CPU_THREADS: int = int(os.getenv("WHISPER_CPU_THREADS", "4"))

    # Beam size for decoding (higher = more accurate but slower)
    WHISPER_BEAM_SIZE: int = int(os.getenv("WHISPER_BEAM_SIZE", "5"))

    # ------------------------------------------------------------------ #
    #  Analysis                                                             #
    # ------------------------------------------------------------------ #

    # Grammar backend: "rule_based" (default, no external API) | "llm" (future)
    ANALYSIS_GRAMMAR_BACKEND: str = os.getenv("ANALYSIS_GRAMMAR_BACKEND", "rule_based")

    # Filler words to detect (comma-separated env override supported)
    _FILLER_WORDS_DEFAULT: str = (
        "um,uh,like,you know,so,basically,actually,literally,right,okay,"
        "i mean,kind of,sort of,you see,well,anyway,whatever,seriously"
    )
    ANALYSIS_FILLER_WORDS_CSV: str = os.getenv(
        "ANALYSIS_FILLER_WORDS", _FILLER_WORDS_DEFAULT
    )

    @classmethod
    def get_filler_words(cls) -> list[str]:
        """Return the configured filler-word list (lowercased, stripped)."""
        return [w.strip().lower() for w in cls.ANALYSIS_FILLER_WORDS_CSV.split(",") if w.strip()]

    # Pause detection — gap between Whisper segments (seconds) counted as a pause
    ANALYSIS_PAUSE_THRESHOLD_S: float = float(
        os.getenv("ANALYSIS_PAUSE_THRESHOLD_S", "1.5")
    )

    # Target speaking rate (WPM) used when scoring fluency
    # TARGET_WORDS_PER_MINUTE already defined above; this alias keeps analysis
    # config self-contained.
    ANALYSIS_TARGET_WPM: int = int(os.getenv("ANALYSIS_TARGET_WPM", "130"))

    # Minimum unique-word ratio considered "rich" vocabulary (0–1)
    ANALYSIS_VOCAB_RICHNESS_TARGET: float = float(
        os.getenv("ANALYSIS_VOCAB_RICHNESS_TARGET", "0.55")
    )

    # Score weights for the overall weighted average (must sum to 1.0)
    # These can be overridden individually via env vars.
    ANALYSIS_WEIGHT_FLUENCY:    float = float(os.getenv("ANALYSIS_WEIGHT_FLUENCY",    "0.25"))
    ANALYSIS_WEIGHT_VOCABULARY: float = float(os.getenv("ANALYSIS_WEIGHT_VOCABULARY", "0.20"))
    ANALYSIS_WEIGHT_GRAMMAR:    float = float(os.getenv("ANALYSIS_WEIGHT_GRAMMAR",    "0.20"))
    ANALYSIS_WEIGHT_CONFIDENCE: float = float(os.getenv("ANALYSIS_WEIGHT_CONFIDENCE", "0.20"))
    ANALYSIS_WEIGHT_FILLER:     float = float(os.getenv("ANALYSIS_WEIGHT_FILLER",     "0.15"))

    # Segment avg_logprob threshold: segments below this are considered disfluent
    ANALYSIS_LOGPROB_FLUENCY_THRESHOLD: float = float(
        os.getenv("ANALYSIS_LOGPROB_FLUENCY_THRESHOLD", "-0.8")
    )

    # ------------------------------------------------------------------ #
    #  Gamification                                                         #
    # ------------------------------------------------------------------ #

    # Available daily goal types:
    #   "sessions"   — complete 1 session today
    #   "minutes_5"  — speak for 5 minutes today
    #   "minutes_10" — speak for 10 minutes today
    GAMIFICATION_DEFAULT_GOAL: str = os.getenv("GAMIFICATION_DEFAULT_GOAL", "sessions")

    # Default difficulty preference shown in PracticePage
    # Values: "all" | "easy" | "medium" | "hard"
    GAMIFICATION_DEFAULT_DIFFICULTY: str = os.getenv(
        "GAMIFICATION_DEFAULT_DIFFICULTY", "all"
    )

    # ------------------------------------------------------------------ #
    #  Test / CI mode                                                       #
    # ------------------------------------------------------------------ #

    # When truthy, the application skips camera, microphone, and model
    # downloads.  Recording and transcription use mock implementations.
    # No API keys, camera, or model weights required.
    # Set via environment: SPEAKUP_TEST_MODE=1
    TEST_MODE: bool = os.getenv("SPEAKUP_TEST_MODE", "").lower() in ("1", "true", "yes")

    # ------------------------------------------------------------------ #
    #  Window Geometry                                                      #
    # ------------------------------------------------------------------ #
    WINDOW_MIN_WIDTH: int = 1024
    WINDOW_MIN_HEIGHT: int = 680
    WINDOW_DEFAULT_WIDTH: int = 1280
    WINDOW_DEFAULT_HEIGHT: int = 800
    SIDEBAR_WIDTH: int = 220

    # ------------------------------------------------------------------ #
    #  AI Feedback Engine                                                   #
    # ------------------------------------------------------------------ #

    # Feedback provider backend:
    #   "local"  — always available, no external API, rule-based only
    #   "llm"    — calls OpenAI API if OPENAI_API_KEY is set; falls back to local
    AI_FEEDBACK_BACKEND: str = os.getenv("AI_FEEDBACK_BACKEND", "local")

    # OpenAI API key — read from env only, NEVER hard-coded in source.
    # Leave empty to disable LLM feedback; app always works without it.
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # OpenAI model used for feedback generation
    # gpt-4o-mini is fast and cheap; gpt-4o gives better quality
    AI_FEEDBACK_MODEL: str = os.getenv("AI_FEEDBACK_MODEL", "gpt-4o-mini")

    # Request timeout in seconds for the OpenAI API call
    AI_FEEDBACK_TIMEOUT_S: int = int(os.getenv("AI_FEEDBACK_TIMEOUT_S", "30"))

    # Maximum tokens in the LLM response (controls cost and length)
    AI_FEEDBACK_MAX_TOKENS: int = int(os.getenv("AI_FEEDBACK_MAX_TOKENS", "800"))

    # Minimum transcript word count before attempting LLM feedback.
    # Short transcripts don't have enough material for useful analysis.
    AI_FEEDBACK_MIN_WORDS: int = int(os.getenv("AI_FEEDBACK_MIN_WORDS", "20"))

    @classmethod
    def has_openai_key(cls) -> bool:
        """Return True when an OpenAI API key is configured in the environment."""
        return bool(cls.OPENAI_API_KEY.strip())

    @classmethod
    def llm_feedback_available(cls) -> bool:
        """Return True when LLM feedback can be attempted."""
        return cls.AI_FEEDBACK_BACKEND == "llm" and cls.has_openai_key()

    # ------------------------------------------------------------------ #
    #  UI Theme colours                                                     #
    # ------------------------------------------------------------------ #
    COLOR_BG_DARK: str = "#0F1117"
    COLOR_BG_CARD: str = "#1A1D27"
    COLOR_BG_SIDEBAR: str = "#13151F"
    COLOR_ACCENT: str = "#6C63FF"
    COLOR_ACCENT_HOVER: str = "#7C73FF"
    COLOR_TEXT_PRIMARY: str = "#EAEAEA"
    COLOR_TEXT_SECONDARY: str = "#8B8FA8"
    COLOR_TEXT_MUTED: str = "#555870"
    COLOR_SUCCESS: str = "#4CAF50"
    COLOR_WARNING: str = "#FF9800"
    COLOR_DANGER: str = "#F44336"
    COLOR_BORDER: str = "#252837"

    @classmethod
    def ensure_directories(cls) -> None:
        """Create required runtime directories if they don't already exist."""
        for directory in (cls.RECORDINGS_DIR, cls.DATA_DIR, cls.WORDS_DIR):
            directory.mkdir(parents=True, exist_ok=True)

    @classmethod
    def is_development(cls) -> bool:
        return cls.APP_ENV.lower() == "development"

    @classmethod
    def is_production(cls) -> bool:
        return cls.APP_ENV.lower() == "production"


# Convenience singleton
settings = Settings()
