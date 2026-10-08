# SpeakUp

**SpeakUp** is a desktop application for English speaking practice and confidence improvement.
Record yourself speaking about a topic, get an AI transcript, and receive detailed feedback on your fluency, vocabulary, grammar, and confidence — all running locally on your machine.

---

## Features

| Feature | Status |
|---|---|
| Random topic assignment (140 topics, 14 categories) | ✅ |
| Local email/password accounts with separate progress | ✅ |
| Camera + microphone recording | ✅ |
| Local speech transcription (faster-whisper) | ✅ |
| Fluency, vocabulary, grammar, confidence scoring | ✅ |
| AI-powered detailed feedback (optional, OpenAI) | ✅ |
| Progress dashboard with charts | ✅ |
| Daily goals and practice streaks | ✅ |
| 13 achievements | ✅ |
| Session history with full results | ✅ |
| Data export (CSV / JSON) | ✅ |
| Database backup | ✅ |
| Keyboard shortcuts | ✅ |
| Fully offline (no online account or cloud required) | ✅ |

---

## Quick Start

### Prerequisites

- Python 3.11 or newer
- Windows 10/11 (macOS and Linux work but are not the primary target)
- A webcam and microphone

### Install

```powershell
# Clone or extract the project
cd SpeakUp

# Create a virtual environment (recommended)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements-prod.txt
```

### Run

```powershell
python app.py
```

On first launch, SpeakUp will:
1. Create a local SQLite database (`speakup.db`)
2. Seed 140 practice topics
3. Let you sign up with a display name, email, and password, or sign in
4. Show a short welcome guide

### Local Accounts

Accounts are stored on this device and work offline; they do not sync across devices. Each account has its own practice history, topic progress, achievements, and preferences. Use **Sign out / switch account** in the sidebar to change accounts. Passwords are stored as salted PBKDF2 hashes. When upgrading from an older version, sign up with the same display name to attach the account to that profile and keep its history.

---

## Configuration

Copy `.env.example` to `.env` and adjust as needed:

```
# Optional: enable AI feedback via OpenAI
# NEVER commit your API key — keep it in .env only
# OPENAI_API_KEY=sk-...
# AI_FEEDBACK_BACKEND=llm

# Transcription model size (tiny/base/small/medium/large-v2)
# Larger = more accurate, slower on CPU
# WHISPER_MODEL_SIZE=base

# Test mode — skips camera/mic/model downloads
# SPEAKUP_TEST_MODE=1
```

All settings have safe defaults. The application runs fully offline without any `.env` file.

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+D` | Go to Dashboard |
| `Ctrl+P` | Go to Practice |
| `Ctrl+H` | Go to History |
| `Ctrl+,` | Go to Settings |
| `Ctrl+N` | New topic (on Practice page) |
| `Escape` | Abandon recording / go back |

---

## Architecture

```
app.py                        Entry point
speakup/
├── config/settings.py        Centralised configuration
├── database/
│   ├── models.py             SQLAlchemy ORM (6 tables)
│   ├── connection.py         Engine + session factory
│   └── repository.py         All database queries
├── services/
│   ├── word_service.py       Topic assignment, no-repeat logic
│   ├── recording_service.py  Camera + microphone capture
│   ├── transcription_service.py  faster-whisper integration
│   ├── analysis_service.py   Speech scoring pipeline
│   ├── feedback_service.py   Local + LLM feedback providers
│   ├── progress_service.py   Dashboard data aggregation
│   └── gamification_service.py  Achievements, streaks, goals
└── ui/
    ├── main_window.py        Application shell + navigation
    └── pages/                One file per screen
```

---

## Test Mode

Run without camera, microphone, or model downloads:

```powershell
$env:SPEAKUP_TEST_MODE=1
python app.py
```

Or run the test suite (fully offline):

```powershell
python -m pytest tests/ -v
```

---

## Building the Windows Executable

```powershell
.\build.ps1
# Output: dist\SpeakUp\SpeakUp.exe
```

Requires PyInstaller: `pip install pyinstaller`

---

## Data & Privacy

- **Everything runs locally.** No data is sent to any server unless you configure `OPENAI_API_KEY` for optional AI feedback.
- Local accounts separate practice data but do not provide cloud sync or password recovery. Anyone with access to the database file can potentially access local data.
- The database is at `speakup.db` in the project root.
- Recordings are saved to `recordings/YYYY/MM/session_<id>.mp4`.
- Use **Settings → Data & Privacy** to export, back up the shared local database, delete the selected profile's recordings, or clear that profile's practice data.

---

## Offline Operation

SpeakUp is designed to work completely offline:

| Component | Offline? | Notes |
|---|---|---|
| Topic assignment | ✅ | Built-in JSON dataset |
| Recording | ✅ | Local camera/mic only |
| Transcription | ✅ | faster-whisper runs on CPU |
| Speech analysis | ✅ | Pure Python, no external calls |
| Local feedback | ✅ | Rule-based, no API |
| AI feedback | ⚠️ Optional | Requires `OPENAI_API_KEY` |
| Dashboard/History | ✅ | Local SQLite only |
| Achievements/Goals | ✅ | Local SQLite only |

---

## Chunks Implemented

| Chunk | Feature Area |
|---|---|
| 1 | Foundation, DB schema, navigation shell |
| 2 | Word/topic system, session state machine |
| 3 | Camera + microphone recording |
| 4 | Speech transcription (faster-whisper) |
| 5 | Speech analysis engine |
| 6 | Session results page |
| 7 | Progress dashboard with charts |
| 8 | AI feedback engine (local + optional LLM) |
| 9 | Gamification (streaks, achievements, goals) |
| 10 | Production hardening |

---

## License

Private project — all rights reserved.
