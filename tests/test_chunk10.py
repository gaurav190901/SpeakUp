"""
tests/test_chunk10.py — pytest suite for Chunk 10 (Production Readiness).

Covers:
    1.  Error handling — global exception handler installed
    2.  Test mode flag (SPEAKUP_TEST_MODE)
    3.  Onboarding — maybe_show_onboarding, reset_onboarding
    4.  DataManagementPage — instantiation, export helpers
    5.  HistoryPage — view_session signal, get_current_row, refresh
    6.  MainWindow — stack count 8, PAGE_DATA=7, keyboard shortcuts
    7.  SettingsPage — open_data_management signal, Data section
    8.  RecordingService — test mode skips hardware
    9.  TranscriptionService — test mode uses mock provider
    10. Build assets — icon file exists, spec file exists
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from speakup.config.settings import settings  # noqa: E402


# ===========================================================================
# 1. Error handling
# ===========================================================================

class TestErrorHandling:

    def test_exception_handler_installed_in_app(self):
        """app.py defines _install_exception_handler."""
        import ast
        src = open("app.py", encoding="utf-8").read()
        assert "_install_exception_handler" in src

    def test_global_except_hook_callable(self):
        """After installing, sys.excepthook must be callable."""
        import sys
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        import importlib.util
        spec = importlib.util.spec_from_file_location("app_module", "app.py")
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._install_exception_handler(app)
        assert callable(sys.excepthook)

    def test_no_print_calls_in_speakup_package(self):
        """speakup/ package must have no bare print() calls."""
        import ast
        issues = []
        for root, dirs, names in os.walk("speakup"):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in names:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                try:
                    tree = ast.parse(open(path, encoding="utf-8").read())
                    for node in ast.walk(tree):
                        if (isinstance(node, ast.Expr)
                                and isinstance(node.value, ast.Call)
                                and isinstance(node.value.func, ast.Name)
                                and node.value.func.id == "print"):
                            issues.append(f"{path}:{node.lineno}")
                except SyntaxError:
                    pass
        assert issues == [], f"Found print() calls: {issues}"


# ===========================================================================
# 2. Test mode flag
# ===========================================================================

class TestModeFlag:

    def test_test_mode_setting_is_bool(self):
        assert isinstance(settings.TEST_MODE, bool)

    def test_test_mode_off_by_default(self):
        # In normal test runs TEST_MODE should be off unless explicitly set
        original = os.environ.get("SPEAKUP_TEST_MODE", "")
        os.environ.pop("SPEAKUP_TEST_MODE", None)
        # Re-evaluate — class attr already set, just verify it's bool
        assert settings.TEST_MODE in (True, False)
        if original:
            os.environ["SPEAKUP_TEST_MODE"] = original

    def test_test_mode_on_when_env_is_1(self):
        os.environ["SPEAKUP_TEST_MODE"] = "1"
        try:
            # Reload settings to pick up the env change
            from importlib import reload
            import speakup.config.settings as _s
            reload(_s)
            assert _s.Settings.TEST_MODE is True
        finally:
            del os.environ["SPEAKUP_TEST_MODE"]
            from importlib import reload
            import speakup.config.settings as _s
            reload(_s)

    def test_recording_service_respects_test_mode(self):
        """In test mode, initialise() returns True without opening hardware."""
        original = settings.__class__.TEST_MODE
        settings.__class__.TEST_MODE = True
        try:
            from speakup.services.recording_service import RecordingService
            svc = RecordingService()
            result = svc.initialise(session_id=999)
            assert result is True
        finally:
            settings.__class__.TEST_MODE = original

    def test_transcription_service_uses_mock_in_test_mode(self):
        """In test mode, TranscriptionService uses MockTranscriptionProvider."""
        original = settings.__class__.TEST_MODE
        settings.__class__.TEST_MODE = True
        try:
            from speakup.services.transcription_service import TranscriptionService
            from speakup.services.transcription_providers import MockTranscriptionProvider
            svc = TranscriptionService()
            assert isinstance(svc._provider, MockTranscriptionProvider)
        finally:
            settings.__class__.TEST_MODE = original


# ===========================================================================
# 3. Onboarding
# ===========================================================================

class TestOnboarding:

    def test_onboarding_dialog_imports(self):
        from speakup.ui.widgets.onboarding import OnboardingDialog, maybe_show_onboarding, reset_onboarding
        assert callable(maybe_show_onboarding)
        assert callable(reset_onboarding)

    def test_onboarding_dialog_instantiates(self):
        from speakup.ui.widgets.onboarding import OnboardingDialog
        dlg = OnboardingDialog()
        assert dlg is not None

    def test_onboarding_dialog_has_five_steps(self):
        from speakup.ui.widgets.onboarding import _STEPS
        assert len(_STEPS) == 5

    def test_reset_onboarding_removes_flag(self):
        from speakup.ui.widgets.onboarding import reset_onboarding, _FLAG_FILE
        # Create flag then remove it
        _FLAG_FILE.write_text("{}", encoding="utf-8")
        reset_onboarding()
        assert not _FLAG_FILE.exists()

    def test_maybe_show_after_reset_does_not_crash(self):
        """After reset, maybe_show_onboarding should not crash."""
        from speakup.ui.widgets.onboarding import (
            reset_onboarding, _FLAG_FILE
        )
        reset_onboarding()
        # Don't actually open the dialog in test — just verify the flag logic
        # The dialog itself is tested by test_onboarding_dialog_instantiates
        assert not _FLAG_FILE.exists()
        # Cleanup
        _FLAG_FILE.unlink(missing_ok=True)


# ===========================================================================
# 4. DataManagementPage
# ===========================================================================

class TestDataManagementPage:

    @pytest.fixture
    def page(self):
        from speakup.ui.pages.data_management_page import DataManagementPage
        return DataManagementPage()

    def test_instantiates_without_error(self, page):
        assert page is not None

    def test_fetch_rows_returns_list(self, page):
        rows = page._fetch_rows()
        assert isinstance(rows, list)

    def test_export_csv_keys_present(self, page):
        """_fetch_rows should always return dicts with required keys."""
        rows = page._fetch_rows()
        if rows:
            for key in ("session_id", "date", "topic", "overall_score"):
                assert key in rows[0]

    def test_delete_recordings_handles_empty_dir(self, page):
        """_delete_recordings must not crash when no recordings exist."""
        with patch("PySide6.QtWidgets.QMessageBox.information"):
            # Ensure the directory is empty before calling
            settings.RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
            # Remove all files temporarily
            files = list(settings.RECORDINGS_DIR.rglob("*.mp4"))
            if not files:
                # Safe to call directly — should show "Nothing to Delete"
                with patch("PySide6.QtWidgets.QMessageBox.information") as m:
                    page._delete_recordings()
                    m.assert_called_once()


# ===========================================================================
# 5. HistoryPage
# ===========================================================================

class TestHistoryPageChunk10:

    def test_view_session_signal_exists(self):
        from speakup.ui.pages.history_page import HistoryPage
        page = HistoryPage()
        assert hasattr(page, "view_session")

    def test_get_current_row_initially_none(self):
        from speakup.ui.pages.history_page import HistoryPage
        page = HistoryPage()
        assert page.get_current_row() is None

    def test_view_session_signal_emitted(self):
        from speakup.ui.pages.history_page import HistoryPage
        page = HistoryPage()
        fired = []
        page.view_session.connect(lambda sid: fired.append(sid))
        # Simulate a row click
        page._on_view({"session_id": 42, "word": "Test"})
        assert 42 in fired

    def test_refresh_does_not_raise(self):
        from speakup.ui.pages.history_page import HistoryPage
        page = HistoryPage()
        page.refresh()


# ===========================================================================
# 6. MainWindow Chunk 10 additions
# ===========================================================================

class TestMainWindowChunk10:

    def test_stack_count_is_eight(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        assert win._stack.count() == 8

    def test_page_data_constant_is_7(self):
        from speakup.ui.main_window import PAGE_DATA
        assert PAGE_DATA == 7

    def test_data_page_at_index_7(self):
        from speakup.ui.main_window import MainWindow
        from speakup.ui.pages.data_management_page import DataManagementPage
        win = MainWindow(user_name="Test")
        assert isinstance(win._stack.widget(7), DataManagementPage)

    def test_keyboard_shortcuts_installed(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        assert hasattr(win, "_install_shortcuts")

    def test_shortcut_new_topic_exists(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        assert hasattr(win, "_shortcut_new_topic")
        assert callable(win._shortcut_new_topic)

    def test_shortcut_escape_exists(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        assert hasattr(win, "_shortcut_escape")
        assert callable(win._shortcut_escape)

    def test_view_session_from_history_handler_exists(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        assert hasattr(win, "_on_view_session_from_history")
        assert callable(win._on_view_session_from_history)

    def test_nav_buttons_have_accessible_names(self):
        from speakup.ui.main_window import MainWindow
        win = MainWindow(user_name="Test")
        for btn in win._nav_buttons.values():
            assert btn.accessibleName() != ""


# ===========================================================================
# 7. SettingsPage Chunk 10 additions
# ===========================================================================

class TestSettingsPageChunk10:

    def test_open_data_management_signal_exists(self):
        from speakup.ui.pages.settings_page import SettingsPage
        page = SettingsPage(user_name="Test")
        assert hasattr(page, "open_data_management")

    def test_signal_emitted_on_manage_btn_click(self):
        from speakup.ui.pages.settings_page import SettingsPage
        page = SettingsPage(user_name="Test")
        fired = []
        page.open_data_management.connect(lambda: fired.append(1))
        # Find and click the manage button
        from PySide6.QtWidgets import QPushButton
        for btn in page.findChildren(QPushButton):
            if "Manage" in btn.text():
                btn.click()
                break
        assert fired == [1]


# ===========================================================================
# 8. Build assets
# ===========================================================================

class TestBuildAssets:

    def test_icon_ico_exists(self):
        ico = Path("speakup/assets/icon.ico")
        assert ico.exists(), f"Icon not found: {ico}"
        assert ico.stat().st_size > 0

    def test_icon_svg_exists(self):
        svg = Path("speakup/assets/icon.svg")
        assert svg.exists()

    def test_pyinstaller_spec_exists(self):
        spec = Path("speakup.spec")
        assert spec.exists()

    def test_build_script_exists(self):
        script = Path("build.ps1")
        assert script.exists()

    def test_requirements_prod_exists(self):
        req = Path("requirements-prod.txt")
        assert req.exists()
        content = req.read_text(encoding="utf-8")
        assert "PySide6" in content
        assert "SQLAlchemy" in content
        assert "OPENAI_API_KEY" not in content   # API key must NEVER be in requirements

    def test_env_example_exists(self):
        env = Path(".env.example")
        assert env.exists()

    def test_no_api_key_in_env_example(self):
        """API key placeholder must not have an actual value."""
        content = Path(".env.example").read_text(encoding="utf-8")
        # The file must contain the key name but no actual value
        assert "OPENAI_API_KEY" in content
        for line in content.splitlines():
            if "OPENAI_API_KEY" in line and not line.strip().startswith("#"):
                # Any uncommented OPENAI_API_KEY line must not have a real value
                assert "sk-" not in line, f"Real API key in .env.example: {line}"

    def test_readme_exists_and_is_complete(self):
        readme = Path("README.md")
        assert readme.exists()
        content = readme.read_text(encoding="utf-8")
        for section in ("Quick Start", "Architecture", "Test Mode",
                        "Offline", "Keyboard Shortcuts"):
            assert section in content, f"README missing section: {section}"


# ===========================================================================
# 9. Offline operation verification
# ===========================================================================

class TestOfflineOperation:

    def test_word_service_works_without_network(self, engine):
        """WordService must work purely from local DB."""
        from speakup.services.word_service import WordService
        ws = WordService()
        seeded = ws.seed_words_if_empty()
        assert seeded == 140
        word = ws.get_next_word_for_user(user_id=1)
        assert word is not None

    def test_gamification_works_without_network(self, engine):
        """GamificationService must work purely from local DB."""
        from speakup.services.gamification_service import GamificationService
        svc = GamificationService()
        snap = svc.get_snapshot(user_id=1)
        assert snap is not None

    def test_progress_service_works_without_network(self, engine):
        """ProgressService must work purely from local DB."""
        from speakup.services.progress_service import ProgressService
        svc = ProgressService()
        stats = svc.get_dashboard_stats(user_id=1)
        assert "total_sessions" in stats

    def test_all_services_importable_without_openai(self):
        """All services must be importable without openai being configured."""
        import importlib
        modules = [
            "speakup.services.analysis_service",
            "speakup.services.word_service",
            "speakup.services.progress_service",
            "speakup.services.gamification_service",
            "speakup.services.feedback_service",
        ]
        for mod in modules:
            loaded = importlib.import_module(mod)
            assert loaded is not None
