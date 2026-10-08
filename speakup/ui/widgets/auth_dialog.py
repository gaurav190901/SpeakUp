"""Local sign-in and sign-up dialog for SpeakUp accounts."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from speakup.config.settings import settings
from speakup.services.auth_service import AuthenticationService


class AuthDialog(QDialog):
    """Authenticate an existing local account or create a new one."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._auth_service = AuthenticationService()
        self._authenticated_account: tuple[int, str] | None = None
        self.setWindowTitle(f"Sign in to {settings.APP_NAME}")
        self.setModal(True)
        self.setMinimumWidth(440)
        self._build_ui()
        self._apply_style()

    @property
    def authenticated_account(self) -> tuple[int, str] | None:
        return self._authenticated_account

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(16)

        title = QLabel(f"Welcome to {settings.APP_NAME}")
        title.setObjectName("AuthTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "Sign in to continue, or create an account on this device. "
            "Your practice data stays local."
        )
        subtitle.setObjectName("AuthSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("AuthTabs")
        self._tabs.addTab(self._build_sign_in_tab(), "Sign In")
        self._tabs.addTab(self._build_sign_up_tab(), "Sign Up")
        layout.addWidget(self._tabs)

        local_note = QLabel(
            "Local account only. Passwords are stored as salted hashes; "
            "they do not sync to other devices."
        )
        local_note.setObjectName("AuthNote")
        local_note.setWordWrap(True)
        layout.addWidget(local_note)

        footer = QHBoxLayout()
        footer.addStretch()
        cancel_button = QPushButton("Cancel")
        cancel_button.setObjectName("AuthCancelButton")
        cancel_button.setFixedHeight(40)
        cancel_button.clicked.connect(self.reject)
        footer.addWidget(cancel_button)
        layout.addLayout(footer)

    def _build_sign_in_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 16, 0, 4)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(12)
        self._login_email = QLineEdit()
        self._login_email.setObjectName("LoginEmail")
        self._login_email.setPlaceholderText("you@example.com")
        self._login_email.setMaxLength(254)
        form.addRow("Email", self._login_email)

        self._login_password = QLineEdit()
        self._login_password.setObjectName("LoginPassword")
        self._login_password.setPlaceholderText("Password")
        self._login_password.setMaxLength(128)
        self._login_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._login_password.returnPressed.connect(self._sign_in)
        form.addRow("Password", self._login_password)
        layout.addLayout(form)

        self._login_error = QLabel("")
        self._login_error.setObjectName("AuthError")
        self._login_error.setWordWrap(True)
        layout.addWidget(self._login_error)

        self._sign_in_button = QPushButton("Sign In")
        self._sign_in_button.setObjectName("AuthPrimaryButton")
        self._sign_in_button.setFixedHeight(42)
        self._sign_in_button.clicked.connect(self._sign_in)
        layout.addWidget(self._sign_in_button)
        return page

    def _build_sign_up_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 16, 0, 4)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(12)
        self._signup_name = QLineEdit()
        self._signup_name.setObjectName("SignupName")
        self._signup_name.setPlaceholderText("Display name")
        self._signup_name.setMaxLength(100)
        form.addRow("Name", self._signup_name)

        self._signup_email = QLineEdit()
        self._signup_email.setObjectName("SignupEmail")
        self._signup_email.setPlaceholderText("you@example.com")
        self._signup_email.setMaxLength(254)
        form.addRow("Email", self._signup_email)

        self._signup_password = QLineEdit()
        self._signup_password.setObjectName("SignupPassword")
        self._signup_password.setPlaceholderText("At least 10 characters")
        self._signup_password.setMaxLength(128)
        self._signup_password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Password", self._signup_password)

        self._signup_confirmation = QLineEdit()
        self._signup_confirmation.setObjectName("SignupConfirmation")
        self._signup_confirmation.setPlaceholderText("Enter password again")
        self._signup_confirmation.setMaxLength(128)
        self._signup_confirmation.setEchoMode(QLineEdit.EchoMode.Password)
        self._signup_confirmation.returnPressed.connect(self._sign_up)
        form.addRow("Confirm", self._signup_confirmation)
        layout.addLayout(form)

        legacy_note = QLabel(
            "Using an older local profile? Sign up with the same display name "
            "to keep its practice history."
        )
        legacy_note.setObjectName("AuthNote")
        legacy_note.setWordWrap(True)
        layout.addWidget(legacy_note)

        self._signup_error = QLabel("")
        self._signup_error.setObjectName("AuthError")
        self._signup_error.setWordWrap(True)
        layout.addWidget(self._signup_error)

        self._sign_up_button = QPushButton("Create Account")
        self._sign_up_button.setObjectName("AuthPrimaryButton")
        self._sign_up_button.setFixedHeight(42)
        self._sign_up_button.clicked.connect(self._sign_up)
        layout.addWidget(self._sign_up_button)
        return page

    def _sign_in(self) -> None:
        account = self._auth_service.login(
            self._login_email.text(), self._login_password.text()
        )
        if account is None:
            self._login_error.setText("Email or password is incorrect.")
            self._login_password.clear()
            return

        self._authenticated_account = account
        self.accept()

    def _sign_up(self) -> None:
        password = self._signup_password.text()
        if password != self._signup_confirmation.text():
            self._signup_error.setText("The passwords do not match.")
            return

        try:
            account = self._auth_service.register(
                self._signup_name.text(),
                self._signup_email.text(),
                password,
            )
        except ValueError as exc:
            self._signup_error.setText(str(exc))
            return

        self._authenticated_account = account
        self.accept()

    def _apply_style(self) -> None:
        self.setStyleSheet(f"""
            QDialog {{ background-color: {settings.COLOR_BG_CARD}; }}
            #AuthTitle {{
                color: {settings.COLOR_TEXT_PRIMARY};
                font-size: 21px;
                font-weight: 700;
            }}
            #AuthSubtitle, #AuthNote {{
                color: {settings.COLOR_TEXT_SECONDARY};
                font-size: 12px;
            }}
            #AuthError {{ color: {settings.COLOR_DANGER}; font-size: 12px; }}
            QTabWidget::pane {{
                border: 1px solid {settings.COLOR_BORDER};
                border-radius: 6px;
                top: -1px;
            }}
            QTabBar::tab {{
                color: {settings.COLOR_TEXT_SECONDARY};
                background: transparent;
                padding: 9px 18px;
                border-bottom: 2px solid transparent;
            }}
            QTabBar::tab:selected {{
                color: {settings.COLOR_ACCENT};
                border-bottom-color: {settings.COLOR_ACCENT};
            }}
            QLineEdit {{
                color: {settings.COLOR_TEXT_PRIMARY};
                background-color: {settings.COLOR_BG_DARK};
                border: 1px solid {settings.COLOR_BORDER};
                border-radius: 6px;
                padding: 8px 10px;
                min-height: 22px;
            }}
            QLineEdit:focus {{ border-color: {settings.COLOR_ACCENT}; }}
            QPushButton {{
                border-radius: 6px;
                padding: 0 16px;
                font-weight: 600;
            }}
            #AuthPrimaryButton {{
                color: #FFFFFF;
                background-color: {settings.COLOR_ACCENT};
                border: none;
            }}
            #AuthPrimaryButton:hover {{ background-color: {settings.COLOR_ACCENT_HOVER}; }}
            #AuthCancelButton {{
                color: {settings.COLOR_TEXT_PRIMARY};
                background-color: {settings.COLOR_BG_DARK};
                border: 1px solid {settings.COLOR_BORDER};
            }}
        """)