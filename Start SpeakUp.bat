@echo off
rem Start SpeakUp in your browser. Close this window (or press Ctrl+C) to stop it.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" webapp.py %*
) else (
    python webapp.py %*
)
if errorlevel 1 pause
