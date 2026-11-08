# SpeakUp

**SpeakUp** is a desktop application for English speaking practice and confidence improvement.
Record yourself speaking about a topic, get an accurate transcript using OpenAI Whisper, and receive detailed feedback on your fluency, vocabulary, grammar, and confidence. Recordings, transcripts, and practice history are stored on your machine. When OpenAI transcription is enabled (an `OPENAI_API_KEY` is set), each session's audio is sent to OpenAI for transcription; without a key, everything runs locally.

---

## Features

| Feature | Status |
|---|---|
| Random topic assignment (140 topics, 14 categories) | ✅ |
| Local email/password accounts with separate progress | ✅ |
| Camera + microphone recording | ✅ |
| Speech transcription (OpenAI Whisper API) | ✅ |
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
| Offline practice mode with local transcription | ✅ |
| Automatic fallback to local transcription if OpenAI is unavailable | ✅ |
| 10-minute recording cap with wrap-up warning (configurable) | ✅ |

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
# Required for the default OpenAI Whisper transcription backend.
# Keep your API key in .env only; never commit it.
# OPENAI_API_KEY=sk-...
# TRANSCRIPTION_BACKEND=openai_whisper
# OPENAI_WHISPER_MODEL=whisper-1

# Optional local/offline transcription instead
# TRANSCRIPTION_BACKEND=faster_whisper
# WHISPER_MODEL_SIZE=base

# Fall back to local transcription when no key is set or the API fails
# TRANSCRIPTION_LOCAL_FALLBACK=1

# Recordings stop automatically after this many seconds (0 = no cap)
# MAX_SPEAKING_DURATION=600

# Optional AI feedback via OpenAI
# AI_FEEDBACK_BACKEND=llm

# Test mode — skips camera/mic/model downloads
# SPEAKUP_TEST_MODE=1
```

OpenAI Whisper is the default transcription provider. It needs an API key and an internet connection, and it uploads each session's audio to OpenAI. If no key is set, SpeakUp uses local transcription (faster-whisper) instead; if the API call fails (network loss, outage, file over 25 MB), the session is retried locally. Set `TRANSCRIPTION_LOCAL_FALLBACK=0` to disable this. To keep all audio on the device, set `TRANSCRIPTION_BACKEND=faster_whisper`. The local model is downloaded from Hugging Face on first use (about 150 MB for `base`) unless `WHISPER_MODEL_PATH` points to a local copy.

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
│   ├── transcription_service.py  OpenAI Whisper + local provider integration
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

SpeakUp can work completely offline once the local transcription model is available:

| Component | Offline? | Notes |
|---|---|---|
| Topic assignment | ✅ | Built-in JSON dataset |
| Recording | ✅ | Local camera/mic only |
| Transcription | ⚠️ | OpenAI Whisper requires internet and an API key and uploads audio; faster-whisper runs locally and is used automatically as a fallback |
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
| 4 | Speech transcription (OpenAI Whisper, with faster-whisper local fallback) |
| 5 | Speech analysis engine |
| 6 | Session results page |
| 7 | Progress dashboard with charts |
| 8 | AI feedback engine (local + optional LLM) |
| 9 | Gamification (streaks, achievements, goals) |
| 10 | Production hardening |

---

## License

Private project — all rights reserved.
