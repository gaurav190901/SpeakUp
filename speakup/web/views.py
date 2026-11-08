"""Pages and JSON endpoints for the SpeakUp web app."""

from __future__ import annotations

import logging
from datetime import datetime

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from speakup.config.settings import settings
from speakup.data.words.loader import (
    CATEGORIES,
    DIFFICULTY_DISPLAY,
    DIFFICULTY_HINTS,
    DIFFICULTY_ICONS,
    DIFFICULTY_LABELS,
    category_with_icon,
    get_topic_hints,
)
from speakup.database.connection import get_session
from speakup.database.repository import PracticeSessionRepository
from speakup.services.auth_service import AuthenticationService
from speakup.services.dashboard_mapper import map_chart_series, map_stat_cards
from speakup.services.gamification_service import GamificationService
from speakup.services.progress_service import ProgressService
from speakup.services.word_service import POOL_EXHAUSTED_MESSAGE, WordService
from speakup.utils.helpers import format_duration
from speakup.web import processing
from speakup.web.auth import login_required, sign_in, sign_out
from speakup.web.results import load_session_result, score_band

logger = logging.getLogger(__name__)

bp = Blueprint("web", __name__)

_DATE_RANGES = (("7d", "7 days"), ("30d", "30 days"), ("90d", "90 days"), ("all", "All time"))
_GOAL_OPTIONS = (("sessions", "1 session per day"),
                 ("minutes_5", "5 minutes per day"),
                 ("minutes_10", "10 minutes per day"))
_AUDIO_EXTENSIONS = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a",
                     "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav"}
_OPEN_STATUSES = {"pending", "in_progress"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _greeting(name: str) -> str:
    hour = datetime.now().hour
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    first = name.split()[0] if name.strip() else ""
    return f"Good {part}, {first} 👋" if first else f"Good {part} 👋"


def _duration_label(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} seconds"
    minutes = round(seconds / 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def _owned_session(session_id: int, statuses: set[str] | None = None):
    """Return (status) for a session owned by the signed-in user, else 404."""
    with get_session() as db:
        ps = PracticeSessionRepository(db).get_by_id(session_id)
        if ps is None or ps.user_id != g.user_id:
            abort(404)
        if statuses is not None and ps.status not in statuses:
            abort(409, description="This practice session has already finished.")
        return ps.status


def _topic_for_session(session_id: int) -> dict | None:
    with get_session() as db:
        ps = PracticeSessionRepository(db).get_by_id(session_id)
        if ps is None or ps.user_id != g.user_id or ps.status not in _OPEN_STATUSES:
            return None
        word = ps.word
        return {
            "session_id": ps.id,
            "word": word.word,
            "category": word.category,
            "difficulty": word.difficulty,
            "difficulty_display": DIFFICULTY_DISPLAY.get(word.difficulty, word.difficulty),
            "description": word.description,
            "hints": get_topic_hints(word.word),
            "exhausted": False,
        }


def _practice_filters() -> tuple[str | None, str | None]:
    category = session.get("practice_category") or None
    level = session.get("practice_level")
    if level is None:
        # First visit: start from the saved difficulty preference.
        pref = GamificationService().get_preferences(g.user_id).get("difficulty_preference", "all")
        level = DIFFICULTY_DISPLAY.get(pref) if pref != "all" else ""
        session["practice_level"] = level
    return category, (level or None)


# ---------------------------------------------------------------------------
# Landing / accounts
# ---------------------------------------------------------------------------

@bp.get("/")
def landing():
    if g.user_id is not None:
        return redirect(url_for("web.dashboard"))
    mode = request.args.get("mode", "signin")
    return render_template("landing.html", mode=mode if mode in ("signin", "signup") else "signin",
                           form={}, error=None)


@bp.post("/login")
def login():
    email = request.form.get("email", "")
    password = request.form.get("password", "")
    if not email.strip() or not password:
        error = "Enter your email and password."
    else:
        account = AuthenticationService().login(email, password)
        if account:
            sign_in(account[0])
            return redirect(url_for("web.dashboard"))
        error = "That email and password don't match. Please try again."
    return render_template("landing.html", mode="signin", form={"email": email},
                           error=error), 400


@bp.post("/signup")
def signup():
    name = request.form.get("name", "")
    email = request.form.get("email", "")
    password = request.form.get("password", "")
    if password != request.form.get("confirm", ""):
        error = "The two passwords don't match."
    else:
        try:
            user_id, _ = AuthenticationService().register(name, email, password)
        except ValueError as exc:
            error = str(exc)
        else:
            sign_in(user_id)
            session["show_welcome"] = True
            return redirect(url_for("web.dashboard"))
    return render_template("landing.html", mode="signup",
                           form={"name": name, "email": email}, error=error), 400


@bp.post("/logout")
def logout():
    sign_out()
    return redirect(url_for("web.landing"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@bp.get("/dashboard")
@login_required
def dashboard():
    date_range = request.args.get("range", "all")
    if date_range not in dict(_DATE_RANGES):
        date_range = "all"
    progress = ProgressService()
    stats = progress.get_dashboard_stats(g.user_id, date_range)
    chart = map_chart_series(progress.get_chart_data(g.user_id, date_range))
    snapshot = GamificationService().get_snapshot(g.user_id)
    recent = progress.get_rich_session_history(g.user_id, limit=5)
    for row in recent:
        row["band"] = score_band(row.get("overall_score"))
        row["duration_text"] = format_duration(row.get("duration_seconds") or 0)
    return render_template(
        "dashboard.html",
        active="dashboard",
        greeting=_greeting(g.user_name or ""),
        total_sessions=int(stats.get("total_sessions") or 0),
        cards=map_stat_cards(stats),
        chart=chart,
        snapshot=snapshot,
        recent=recent,
        date_range=date_range,
        date_ranges=_DATE_RANGES,
        show_welcome=session.pop("show_welcome", False),
    )


# ---------------------------------------------------------------------------
# Practice + recording
# ---------------------------------------------------------------------------

@bp.get("/practice")
@login_required
def practice():
    words = WordService()
    category, level = _practice_filters()
    topic = None
    current = session.get("practice_session_id")
    if current:
        topic = _topic_for_session(int(current))
    if topic is None:
        topic = words.assign_topic(g.user_id, category=category, difficulty_label=level)
        session["practice_session_id"] = topic.get("session_id")
    remaining = words.get_unused_count_for_user(
        g.user_id, category=category, difficulty_label=level,
    )
    return render_template(
        "practice.html",
        active="practice",
        topic=topic,
        exhausted_message=POOL_EXHAUSTED_MESSAGE,
        categories=[(c, category_with_icon(c)) for c in CATEGORIES],
        levels=[(lbl, f"{DIFFICULTY_ICONS[lbl]}  {lbl}") for lbl in DIFFICULTY_LABELS],
        selected_category=category or "",
        selected_level=level or "",
        category_display=category_with_icon(topic.get("category")) if topic.get("category") else "—",
        level_icon=DIFFICULTY_ICONS.get(topic.get("difficulty_display") or "", ""),
        level_hint=DIFFICULTY_HINTS.get(topic.get("difficulty_display") or "", ""),
        remaining=remaining,
        min_seconds=settings.MIN_SPEAKING_DURATION,
        min_label=_duration_label(settings.MIN_SPEAKING_DURATION),
        max_seconds=settings.MAX_SPEAKING_DURATION,
    )


@bp.post("/practice/new")
@login_required
def new_topic():
    category = request.form.get("category", "")
    level = request.form.get("level", "")
    session["practice_category"] = category if category in CATEGORIES else ""
    session["practice_level"] = level if level in DIFFICULTY_LABELS else ""
    current = session.get("practice_session_id")
    topic = WordService().request_new_topic(
        g.user_id,
        int(current) if current else None,
        category=session["practice_category"] or None,
        difficulty_label=session["practice_level"] or None,
    )
    session["practice_session_id"] = topic.get("session_id")
    return redirect(url_for("web.practice"))


@bp.post("/api/sessions/<int:session_id>/start")
@login_required
def api_start(session_id: int):
    _owned_session(session_id, _OPEN_STATUSES)
    WordService().start_practice(session_id)
    return jsonify(ok=True)


@bp.post("/api/sessions/<int:session_id>/abandon")
@login_required
def api_abandon(session_id: int):
    _owned_session(session_id, _OPEN_STATUSES)
    WordService().abandon_practice(session_id)
    session.pop("practice_session_id", None)
    return jsonify(ok=True, redirect=url_for("web.practice"))


@bp.post("/api/sessions/<int:session_id>/recording")
@login_required
def api_upload(session_id: int):
    _owned_session(session_id, _OPEN_STATUSES)
    audio = request.files.get("audio")
    if audio is None or not audio.filename:
        abort(400, description="No recording was received.")
    mimetype = (audio.mimetype or "").split(";")[0].strip().lower()
    ext = _AUDIO_EXTENSIONS.get(mimetype, ".webm")
    try:
        client_duration = float(request.form.get("duration", "0") or 0)
    except ValueError:
        client_duration = 0.0

    target = processing.recording_dir() / f"session_{session_id}{ext}"
    audio.save(target)
    session.pop("practice_session_id", None)

    processing.submit(
        session_id, target, client_duration,
        background=current_app.config["PROCESS_RECORDINGS_IN_BACKGROUND"],
    )
    return jsonify(ok=True, redirect=url_for("web.processing_page", session_id=session_id))


@bp.get("/sessions/<int:session_id>/processing")
@login_required
def processing_page(session_id: int):
    _owned_session(session_id)
    return render_template("processing.html", active="practice", session_id=session_id)


@bp.get("/api/sessions/<int:session_id>/status")
@login_required
def api_status(session_id: int):
    status = _owned_session(session_id)
    job = processing.get_status(session_id)
    if job is None:
        # Not processed in this run of the server: fall back to what's stored.
        from speakup.services.analysis_service import AnalysisService  # noqa: PLC0415
        if AnalysisService().get_result_for_session(session_id):
            job = processing.JobStatus(processing.DONE, "Your results are ready!")
        elif status in _OPEN_STATUSES:
            job = processing.JobStatus(processing.ERROR, "This session hasn't been recorded yet.",
                                       error="not_recorded")
        else:
            job = processing.JobStatus(
                processing.ERROR,
                "This recording wasn't processed. Please try another session.",
                error="not_processed")
    payload = job.to_dict()
    if job.stage == processing.DONE:
        payload["redirect"] = url_for("web.session_result", session_id=session_id)
    return jsonify(payload)


@bp.get("/sessions/<int:session_id>")
@login_required
def session_result(session_id: int):
    result = load_session_result(session_id, g.user_id)
    if result is None:
        abort(404)
    return render_template("results.html", active="history", r=result)


# ---------------------------------------------------------------------------
# History & settings
# ---------------------------------------------------------------------------

@bp.get("/history")
@login_required
def history():
    rows = ProgressService().get_rich_session_history(g.user_id)
    for row in rows:
        row["band"] = score_band(row.get("overall_score"))
        row["duration_text"] = format_duration(row.get("duration_seconds") or 0)
        row["category_display"] = category_with_icon(row.get("category"))
    return render_template("history.html", active="history", rows=rows)


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    gam = GamificationService()
    if request.method == "POST":
        difficulty = request.form.get("difficulty", "all")
        goal = request.form.get("goal", "sessions")
        if difficulty not in ("all", "easy", "medium", "hard"):
            difficulty = "all"
        if goal not in dict(_GOAL_OPTIONS):
            goal = "sessions"
        gam.save_preferences(g.user_id, difficulty_preference=difficulty, daily_goal_type=goal)
        session["practice_level"] = DIFFICULTY_DISPLAY.get(difficulty, "") if difficulty != "all" else ""
        flash("Your preferences were saved.")
        return redirect(url_for("web.settings_page"))

    prefs = gam.get_preferences(g.user_id)
    uses_openai = (settings.TRANSCRIPTION_BACKEND == "openai_whisper"
                   and settings.has_openai_key())
    return render_template(
        "settings.html",
        active="settings",
        prefs=prefs,
        difficulty_options=[("all", "All levels")] + [
            (db, f"{DIFFICULTY_ICONS[lbl]}  {lbl}") for db, lbl in DIFFICULTY_DISPLAY.items()],
        goal_options=_GOAL_OPTIONS,
        uses_openai=uses_openai,
        data_dir=str(settings.APP_DATA_DIR),
        version=settings.APP_VERSION,
    )


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

@bp.app_errorhandler(400)
@bp.app_errorhandler(404)
@bp.app_errorhandler(409)
def _error(err):
    if request.path.startswith("/api/"):
        return jsonify(ok=False, error=err.description), err.code
    titles = {400: "Something went wrong", 404: "Page not found", 409: "Already finished"}
    return render_template("error.html", code=err.code, title=titles.get(err.code, "Error"),
                           message=err.description), err.code
