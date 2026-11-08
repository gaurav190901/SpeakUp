"""
SpeakUp — web app entry point.

Starts SpeakUp in your browser on this computer:

    python webapp.py              # opens http://127.0.0.1:5000
    python webapp.py --port 8080  # use a different port
    python webapp.py --no-browser # don't open a browser tab

The server only listens on 127.0.0.1, so other devices on your network
cannot reach it.
"""

from __future__ import annotations

import argparse
import logging
import threading
import webbrowser

from speakup.config.settings import settings
from speakup.utils.logger import setup_logging

setup_logging(level=settings.LOG_LEVEL, log_file=settings.LOG_FILE)
logger = logging.getLogger("speakup.webapp")

HOST = "127.0.0.1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SpeakUp in your browser.")
    parser.add_argument("--port", type=int, default=5000, help="port to listen on (default 5000)")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args()

    from waitress import serve  # noqa: PLC0415

    from speakup.database.bootstrap import bootstrap_database  # noqa: PLC0415
    from speakup.web import create_app  # noqa: PLC0415

    bootstrap_database()
    app = create_app()

    url = f"http://{HOST}:{args.port}/"
    logger.info("Starting SpeakUp web app at %s", url)
    print(f"\n  SpeakUp is running at {url}\n  Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()

    serve(app, host=HOST, port=args.port, threads=8, ident="SpeakUp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
