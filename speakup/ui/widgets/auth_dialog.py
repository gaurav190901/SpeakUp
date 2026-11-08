"""Welcome screen: local sign-in and sign-up for SpeakUp accounts.

Layout
------
┌──────────────────────────┬──────────────────────────────────┐
│  Brand panel             │  [ Sign in | Create account ]    │
│  SpeakUp                 │                                  │
│  Speak English with      │  Email                           │
│  confidence              │  [....................]          │
│                          │  Password                        │
│  🎯 A fresh topic …      │  [....................]          │
│  📝 Instant transcript … │  ☐ Show password                 │
│  📈 Track your progress  │                                  │
│                          │  [        Sign in         ]      │
│  🔒 Stays on this PC     │  New here? Create an account     │
└──────────────────────────┴──────────────────────────────────┘
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from speakup.config.settings import settings
from speakup.services.auth_service import AuthenticationService

_FEATURES = (
    ("🎯", "A fresh topic every time", "140 everyday topics, from easy to advanced."),
    ("📝", "Instant transcript & feedback", "See what you said and how to improve."),
    ("📈", "Watch yourself improve", "Streaks, goals and progress charts."),
)

_SIGN_IN = 0
_SIGN_UP = 1


class AuthDialog(QDialog):
    """Authenticate an existing local account or create a new one."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._auth_service = AuthenticationService()
        self._authenticated_account: tuple[int, str] | None = None
        self.setWindowTitle(f"Welcome to {settings.APP_NAME}")
        self.setObjectName("AuthDialog")
        self.setModal(True)
        self.setFixedSize(900, 600)
        self._build_ui()
        self._apply_style()
        self._show_mode(_SIGN_IN)

    @property
    def authenticated_account(self) -> tuple[int, str] | None:
        return self._authenticated_account

    # ------------------------------------------------------------------ #
    # Layout                                                               #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_brand_panel(), stretch=5)
        root.addWidget(self._build_form_panel(), stretch=6)

    def _build_brand_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("BrandPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(44, 48, 44, 40)
        layout.setSpacing(0)

        logo_row = QHBoxLayout()
        logo_row.setSpacing(12)
        badge = QLabel("🎙️")
        badge.setObjectName("BrandBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(48, 48)
        name = QLabel(settings.APP_NAME)
        name.setObjectName("BrandName")
        logo_row.addWidget(badge)
        logo_row.addWidget(name)
        logo_row.addStretch()
        layout.addLayout(logo_row)

        layout.addSpacing(40)
        headline = QLabel("Speak English\nwith confidence.")
        headline.setObjectName("BrandHeadline")
        layout.addWidget(headline)

        layout.addSpacing(12)
        pitch = QLabel("Practise speaking for a minute a day and get friendly, "
                       "useful feedback.")
        pitch.setObjectName("BrandPitch")
        pitch.setWordWrap(True)
        layout.addWidget(pitch)

        layout.addSpacing(32)
        for icon, title, text in _FEATURES:
            layout.addWidget(self._feature_row(icon, title, text))
            layout.addSpacing(16)

        layout.addStretch()
        privacy = QLabel("🔒  Your account and recordings stay on this computer.")
        privacy.setObjectName("BrandPrivacy")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        return panel

    @staticmethod
    def _feature_row(icon: str, title: str, text: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        icon_lbl = QLabel(icon)
        icon_lbl.setObjectName("FeatureIcon")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setFixedSize(40, 40)
        layout.addWidget(icon_lbl, alignment=Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(2)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("FeatureTitle")
        text_lbl = QLabel(text)
        text_lbl.setObjectName("FeatureText")
        text_lbl.setWordWrap(True)
        col.addWidget(title_lbl)
        col.addWidget(text_lbl)
        layout.addLayout(col, stretch=1)
        return row

    def _build_form_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("FormPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(56, 48, 56, 40)
        layout.setSpacing(0)

        # Segmented switch: Sign in | Create account
        switch = QFrame()
        switch.setObjectName("ModeSwitch")
        switch_layout = QHBoxLayout(switch)
        switch_layout.setContentsMargins(4, 4, 4, 4)
        switch_layout.setSpacing(4)
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        for mode, text in ((_SIGN_IN, "Sign in"), (_SIGN_UP, "Create account")):
            btn = QPushButton(text)
            btn.setObjectName("ModeButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(36)
            self._mode_group.addButton(btn, mode)
            switch_layout.addWidget(btn)
        self._mode_group.idClicked.connect(self._show_mode)
        layout.addWidget(switch)

        layout.addSpacing(28)
        self._form_title = QLabel("")
        self._form_title.setObjectName("FormTitle")
        layout.addWidget(self._form_title)
        layout.addSpacing(6)
        self._form_subtitle = QLabel("")
        self._form_subtitle.setObjectName("FormSubtitle")
        self._form_subtitle.setWordWrap(True)
        layout.addWidget(self._form_subtitle)
        layout.addSpacing(22)

        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_sign_in_page())
        self._pages.addWidget(self._build_sign_up_page())
        layout.addWidget(self._pages, stretch=1)
        return panel

    @staticmethod
    def _field(label: str, edit: QLineEdit, layout: QVBoxLayout) -> None:
        lbl = QLabel(label)
        lbl.setObjectName("FieldLabel")
        lbl.setBuddy(edit)
        layout.addWidget(lbl)
        layout.addSpacing(6)
        edit.setFixedHeight(42)
        layout.addWidget(edit)
        layout.addSpacing(14)

    @staticmethod
    def _show_password_toggle(*edits: QLineEdit) -> QCheckBox:
        box = QCheckBox("Show password")
        box.setObjectName("ShowPassword")
        box.setCursor(Qt.CursorShape.PointingHandCursor)

        def _toggle(checked: bool) -> None:
            mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            for edit in edits:
                edit.setEchoMode(mode)

        box.toggled.connect(_toggle)
        return box

    def _switch_link(self, prompt: str, action: str, mode: int) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addStretch()
        text = QLabel(prompt)
        text.setObjectName("SwitchPrompt")
        link = QPushButton(action)
        link.setObjectName("LinkButton")
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.clicked.connect(lambda: self._show_mode(mode))
        layout.addWidget(text)
        layout.addWidget(link)
        layout.addStretch()
        return row

    def _build_sign_in_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._login_email = QLineEdit()
        self._login_email.setObjectName("LoginEmail")
        self._login_email.setPlaceholderText("you@example.com")
        self._login_email.setMaxLength(254)
        self._field("Email", self._login_email, layout)

        self._login_password = QLineEdit()
        self._login_password.setObjectName("LoginPassword")
        self._login_password.setPlaceholderText("Your password")
        self._login_password.setMaxLength(128)
        self._login_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._field("Password", self._login_password, layout)

        self._login_email.returnPressed.connect(self._login_password.setFocus)
        self._login_password.returnPressed.connect(self._sign_in)

        layout.addWidget(self._show_password_toggle(self._login_password))
        layout.addSpacing(10)

        self._login_error = QLabel("")
        self._login_error.setObjectName("AuthError")
        self._login_error.setWordWrap(True)
        self._login_error.setVisible(False)
        layout.addWidget(self._login_error)

        layout.addStretch()
        self._sign_in_button = QPushButton("Sign in")
        self._sign_in_button.setObjectName("AuthPrimaryButton")
        self._sign_in_button.setFixedHeight(46)
        self._sign_in_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sign_in_button.setDefault(True)
        self._sign_in_button.clicked.connect(self._sign_in)
        layout.addWidget(self._sign_in_button)
        layout.addSpacing(14)
        layout.addWidget(self._switch_link("New here?", "Create an account", _SIGN_UP))
        return page

    def _build_sign_up_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._signup_name = QLineEdit()
        self._signup_name.setObjectName("SignupName")
        self._signup_name.setPlaceholderText("Your name")
        self._signup_name.setMaxLength(100)

        self._signup_email = QLineEdit()
        self._signup_email.setObjectName("SignupEmail")
        self._signup_email.setPlaceholderText("you@example.com")
        self._signup_email.setMaxLength(254)

        name_email = QHBoxLayout()
        name_email.setSpacing(14)
        for label, edit in (("Name", self._signup_name), ("Email", self._signup_email)):
            col = QVBoxLayout()
            col.setSpacing(0)
            self._field(label, edit, col)
            name_email.addLayout(col)
        layout.addLayout(name_email)

        self._signup_password = QLineEdit()
        self._signup_password.setObjectName("SignupPassword")
        self._signup_password.setPlaceholderText("At least 10 characters")
        self._signup_password.setMaxLength(128)
        self._signup_password.setEchoMode(QLineEdit.EchoMode.Password)

        self._signup_confirmation = QLineEdit()
        self._signup_confirmation.setObjectName("SignupConfirmation")
        self._signup_confirmation.setPlaceholderText("Type it again")
        self._signup_confirmation.setMaxLength(128)
        self._signup_confirmation.setEchoMode(QLineEdit.EchoMode.Password)

        passwords = QHBoxLayout()
        passwords.setSpacing(14)
        for label, edit in (("Password", self._signup_password),
                            ("Confirm password", self._signup_confirmation)):
            col = QVBoxLayout()
            col.setSpacing(0)
            self._field(label, edit, col)
            passwords.addLayout(col)
        layout.addLayout(passwords)

        self._signup_name.returnPressed.connect(self._signup_email.setFocus)
        self._signup_email.returnPressed.connect(self._signup_password.setFocus)
        self._signup_password.returnPressed.connect(self._signup_confirmation.setFocus)
        self._signup_confirmation.returnPressed.connect(self._sign_up)

        layout.addWidget(self._show_password_toggle(
            self._signup_password, self._signup_confirmation))
        layout.addSpacing(10)

        legacy_note = QLabel(
            "Used SpeakUp before accounts existed? Sign up with the same name "
            "to keep your practice history."
        )
        legacy_note.setObjectName("AuthNote")
        legacy_note.setWordWrap(True)
        layout.addWidget(legacy_note)
        layout.addSpacing(8)

        self._signup_error = QLabel("")
        self._signup_error.setObjectName("AuthError")
        self._signup_error.setWordWrap(True)
        self._signup_error.setVisible(False)
        layout.addWidget(self._signup_error)

        layout.addStretch()
        self._sign_up_button = QPushButton("Create account")
        self._sign_up_button.setObjectName("AuthPrimaryButton")
        self._sign_up_button.setFixedHeight(46)
        self._sign_up_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sign_up_button.clicked.connect(self._sign_up)
        layout.addWidget(self._sign_up_button)
        layout.addSpacing(14)
        layout.addWidget(self._switch_link("Already have an account?", "Sign in", _SIGN_IN))
        return page

    # ------------------------------------------------------------------ #
    # Behaviour                                                            #
    # ------------------------------------------------------------------ #

    def _show_mode(self, mode: int) -> None:
        self._pages.setCurrentIndex(mode)
        button = self._mode_group.button(mode)
        if button is not None:
            button.setChecked(True)
        if mode == _SIGN_IN:
            self._form_title.setText("Welcome back 👋")
            self._form_subtitle.setText("Sign in to continue your practice.")
            self._sign_in_button.setDefault(True)
            self._login_email.setFocus()
        else:
            self._form_title.setText("Create your account")
            self._form_subtitle.setText("It takes 30 seconds and works offline.")
            self._sign_up_button.setDefault(True)
            self._signup_name.setFocus()

    @staticmethod
    def _set_error(label: QLabel, message: str) -> None:
        label.setText(f"⚠️  {message}" if message else "")
        label.setVisible(bool(message))

    def _sign_in(self) -> None:
        if not self._login_email.text().strip() or not self._login_password.text():
            self._set_error(self._login_error, "Enter your email and password.")
            return
        account = self._auth_service.login(
            self._login_email.text(), self._login_password.text()
        )
        if account is None:
            self._set_error(self._login_error,
                            "That email and password don't match. Please try again.")
            self._login_password.clear()
            self._login_password.setFocus()
            return

        self._authenticated_account = account
        self.accept()

    def _sign_up(self) -> None:
        password = self._signup_password.text()
        if password != self._signup_confirmation.text():
            self._set_error(self._signup_error, "The two passwords don't match.")
            return

        try:
            account = self._auth_service.register(
                self._signup_name.text(),
                self._signup_email.text(),
                password,
            )
        except ValueError as exc:
            self._set_error(self._signup_error, str(exc))
            return

        self._authenticated_account = account
        self.accept()

    def _apply_style(self) -> None:
        c = settings
        self.setStyleSheet(f"""
            #AuthDialog {{ background-color: {c.COLOR_BG_DARK}; }}

            #BrandPanel {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #2A2366, stop:0.55 #1B1840, stop:1 {c.COLOR_BG_SIDEBAR});
                border-right: 1px solid {c.COLOR_BORDER};
            }}
            #BrandBadge {{
                font-size: 24px;
                background-color: rgba(255, 255, 255, 0.10);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 12px;
            }}
            #BrandName {{ color: #FFFFFF; font-size: 22px; font-weight: 700; }}
            #BrandHeadline {{
                color: #FFFFFF; font-size: 30px; font-weight: 700;
            }}
            #BrandPitch {{ color: #C9C6F5; font-size: 14px; }}
            #FeatureIcon {{
                font-size: 18px;
                background-color: rgba(255, 255, 255, 0.08);
                border-radius: 10px;
            }}
            #FeatureTitle {{ color: #FFFFFF; font-size: 14px; font-weight: 600; }}
            #FeatureText {{ color: #A9A6D6; font-size: 12px; }}
            #BrandPrivacy {{ color: #A9A6D6; font-size: 12px; }}

            #FormPanel {{ background-color: {c.COLOR_BG_DARK}; }}
            #ModeSwitch {{
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 10px;
            }}
            #ModeButton {{
                color: {c.COLOR_TEXT_SECONDARY};
                background: transparent;
                border: none;
                border-radius: 7px;
                font-size: 13px;
                font-weight: 600;
            }}
            #ModeButton:hover {{ color: {c.COLOR_TEXT_PRIMARY}; }}
            #ModeButton:checked {{
                color: #FFFFFF;
                background-color: {c.COLOR_ACCENT};
            }}
            #FormTitle {{ color: {c.COLOR_TEXT_PRIMARY}; font-size: 24px; font-weight: 700; }}
            #FormSubtitle {{ color: {c.COLOR_TEXT_SECONDARY}; font-size: 13px; }}
            #FieldLabel {{ color: {c.COLOR_TEXT_PRIMARY}; font-size: 13px; font-weight: 600; }}
            QLineEdit {{
                color: {c.COLOR_TEXT_PRIMARY};
                background-color: {c.COLOR_BG_CARD};
                border: 1px solid {c.COLOR_BORDER};
                border-radius: 8px;
                padding: 0 12px;
                font-size: 14px;
                selection-background-color: {c.COLOR_ACCENT};
            }}
            QLineEdit:hover {{ border-color: #343850; }}
            QLineEdit:focus {{ border: 1px solid {c.COLOR_ACCENT}; }}
            #ShowPassword {{ color: {c.COLOR_TEXT_SECONDARY}; font-size: 12px; spacing: 8px; }}
            #ShowPassword::indicator {{
                width: 16px; height: 16px;
                border: 1px solid #3A3F58;
                border-radius: 4px;
                background-color: {c.COLOR_BG_CARD};
            }}
            #ShowPassword::indicator:hover {{ border-color: {c.COLOR_ACCENT}; }}
            #ShowPassword::indicator:checked {{
                background-color: {c.COLOR_ACCENT};
                border-color: {c.COLOR_ACCENT};
            }}
            #AuthNote {{ color: {c.COLOR_TEXT_SECONDARY}; font-size: 12px; }}
            #AuthError {{
                color: #FF8A80;
                background-color: rgba(244, 67, 54, 0.10);
                border: 1px solid rgba(244, 67, 54, 0.35);
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 12px;
            }}
            #AuthPrimaryButton {{
                color: #FFFFFF;
                background-color: {c.COLOR_ACCENT};
                border: none;
                border-radius: 10px;
                font-size: 15px;
                font-weight: 600;
            }}
            #AuthPrimaryButton:hover {{ background-color: {c.COLOR_ACCENT_HOVER}; }}
            #AuthPrimaryButton:pressed {{ background-color: #5a52e0; }}
            #SwitchPrompt {{ color: {c.COLOR_TEXT_SECONDARY}; font-size: 13px; }}
            #LinkButton {{
                color: {c.COLOR_ACCENT};
                background: transparent;
                border: none;
                font-size: 13px;
                font-weight: 600;
                padding: 0;
            }}
            #LinkButton:hover {{ color: {c.COLOR_ACCENT_HOVER}; text-decoration: underline; }}
        """)
