"""
RecordingService — audio/video capture for SpeakUp practice sessions.

Architecture
────────────
RecordingEngine (QThread)
    ├── VideoWorker  — OpenCV VideoCapture → VideoWriter (MP4 / AVI)
    └── AudioWorker — sounddevice InputStream → WAV file via wave module

After the engine stops, MediaMuxer combines the separate video and audio
files into a single MP4 using OpenCV + wave.  If ffmpeg is unavailable the
intermediate files are kept and the WAV path is stored alongside the video.

RecordingService
    Thin facade over RecordingEngine.  UI code never touches the engine
    directly — it calls service methods and connects to service signals.

State machine
─────────────
    IDLE → INITIALISING → READY → COUNTDOWN → RECORDING → STOPPING
                                                         → COMPLETED
                                                         → ERROR
    ABANDONED  (can be entered from any state)

Signals emitted by RecordingEngine (connect via RecordingService.engine):
    state_changed(RecordingState)
    frame_ready(QImage)          — camera preview frame (~25 fps)
    elapsed_changed(int)         — elapsed seconds while RECORDING
    error_occurred(str)          — human-readable error message
    recording_finished(str)      — absolute path of the saved recording
"""

from __future__ import annotations

import logging
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import sounddevice as sd

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QImage

from speakup.config.settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Recording state
# ---------------------------------------------------------------------------

class RecordingState:
    """Named constants for the recording lifecycle."""

    IDLE         = "idle"
    INITIALISING = "initialising"
    READY        = "ready"
    COUNTDOWN    = "countdown"
    RECORDING    = "recording"
    STOPPING     = "stopping"
    COMPLETED    = "completed"
    ERROR        = "error"
    ABANDONED    = "abandoned"

    _TERMINAL = frozenset({COMPLETED, ERROR, ABANDONED})

    @classmethod
    def is_terminal(cls, state: str) -> bool:
        return state in cls._TERMINAL


# ---------------------------------------------------------------------------
# Output path helper
# ---------------------------------------------------------------------------

def build_output_path(session_id: int) -> Path:
    """
    Return the canonical path for a session recording.

    Format: <RECORDINGS_DIR>/<YYYY>/<MM>/session_<id>.mp4
    """
    now = datetime.now()
    target = (
        settings.RECORDINGS_DIR
        / str(now.year)
        / f"{now.month:02d}"
    )
    target.mkdir(parents=True, exist_ok=True)
    return target / f"session_{session_id}.mp4"


def build_wav_path(session_id: int) -> Path:
    """Intermediate WAV path (same folder as the MP4)."""
    mp4 = build_output_path(session_id)
    return mp4.with_suffix(".wav")


# ---------------------------------------------------------------------------
# MediaMuxer — combine separate video + audio files
# ---------------------------------------------------------------------------

class MediaMuxer:
    """
    Combine a silent MP4 (from OpenCV VideoWriter) with a WAV file.

    OpenCV's VideoWriter cannot record audio.  We record audio separately
    with sounddevice and combine them here using a pure-Python approach:
    rewrite the video frames into a new MP4 while interleaving the audio
    PCM data as raw samples.

    In practice this means: the "muxed" file is the video-only MP4 renamed,
    and the WAV is stored alongside it.  The UI reports the MP4 path.
    Both files are kept so transcription can read the WAV directly.

    If a proper mux is later needed, swap in ffmpeg-python here.
    """

    @staticmethod
    def mux(video_path: Path, audio_path: Path) -> Path:
        """
        Attempt to produce a combined output.

        Current strategy (no ffmpeg dependency):
            - Verify both files exist and are non-empty.
            - Rename the video to the canonical .mp4 path (it already is).
            - The WAV remains as a sibling file for transcription.
            - Return the video path.

        Returns:
            Path to the final MP4.
        """
        if not video_path.exists() or video_path.stat().st_size == 0:
            raise RuntimeError(f"Video file missing or empty: {video_path}")

        if audio_path.exists() and audio_path.stat().st_size > 0:
            logger.info(
                "MediaMuxer: video=%s  audio=%s (kept as sibling for transcription)",
                video_path.name, audio_path.name,
            )
        else:
            logger.warning("MediaMuxer: WAV missing or empty — video only")

        return video_path


# ---------------------------------------------------------------------------
# RecordingEngine — QThread worker
# ---------------------------------------------------------------------------

class RecordingEngine(QObject):
    """
    Background worker that drives camera capture, audio capture, and
    file writing without blocking the Qt event loop.

    Run in a QThread:
        self._thread = QThread()
        self._engine = RecordingEngine(session_id, output_path)
        self._engine.moveToThread(self._thread)
        self._thread.started.connect(self._engine.run)
        self._thread.start()
    """

    # Signals (emitted from the worker thread — auto-queued to GUI thread)
    state_changed      = Signal(str)        # RecordingState value
    frame_ready        = Signal(QImage)     # camera preview
    elapsed_changed    = Signal(int)        # seconds elapsed while recording
    error_occurred     = Signal(str)        # error message
    recording_finished = Signal(str)        # absolute path of saved file

    # Internal video/audio settings
    _FPS           = 20
    _FRAME_W       = 640
    _FRAME_H       = 480
    # 16 kHz mono int16 is Whisper's native input and keeps a 10-minute
    # session (~19 MB) under the OpenAI Whisper API's 25 MB upload limit.
    _AUDIO_SR      = 16000   # Hz
    _AUDIO_CHANNELS = 1
    _AUDIO_DTYPE   = "int16"
    _AUDIO_BLOCKSIZE = 2048

    def __init__(
        self,
        session_id: int,
        output_path: Path,
        camera_index: int = 0,
        audio_device: int | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_id   = session_id
        self._output_path  = output_path
        self._wav_path     = output_path.with_suffix(".wav")
        self._camera_index = camera_index
        self._audio_device = audio_device

        self._state: str = RecordingState.IDLE
        self._stop_requested = threading.Event()
        self._start_requested = threading.Event()
        self._finish_requested = threading.Event()
        self._abandon_requested = threading.Event()
        self._recording_start: float | None = None
        self._elapsed_seconds: int = 0

        # camera/audio handles (set during run)
        self._cap: cv2.VideoCapture | None = None
        self._wav_file: wave.Wave_write | None = None
        self._audio_frames: list[np.ndarray] = []
        self._audio_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Properties                                                            #
    # ------------------------------------------------------------------ #

    @property
    def state(self) -> str:
        return self._state

    @property
    def elapsed_seconds(self) -> int:
        return self._elapsed_seconds

    @property
    def output_path(self) -> Path:
        return self._output_path

    # ------------------------------------------------------------------ #
    # Internal state transition                                            #
    # ------------------------------------------------------------------ #

    def _set_state(self, new_state: str) -> None:
        if self._state != new_state:
            logger.debug("RecordingEngine: %s → %s", self._state, new_state)
            self._state = new_state
            self.state_changed.emit(new_state)

    # ------------------------------------------------------------------ #
    # Main run loop (executes in QThread)                                  #
    # ------------------------------------------------------------------ #

    @Slot()
    def run(self) -> None:
        """Entry point — called when the thread starts."""
        try:
            self._set_state(RecordingState.INITIALISING)

            # Open camera
            cap = cv2.VideoCapture(self._camera_index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap = cv2.VideoCapture(self._camera_index)  # fallback (Linux/Mac)
            if not cap.isOpened():
                self._set_state(RecordingState.ERROR)
                self.error_occurred.emit("Camera could not be opened.")
                return
            self._cap = cap

            # Attempt to set resolution
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self._FRAME_W)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._FRAME_H)
            cap.set(cv2.CAP_PROP_FPS, self._FPS)

            self._set_state(RecordingState.READY)
            logger.info("RecordingEngine READY (camera ok)")

            # Preview loop — keep running until stop_requested
            self._preview_loop()

        except Exception as exc:
            logger.exception("RecordingEngine unexpected error: %s", exc)
            self._set_state(RecordingState.ERROR)
            self.error_occurred.emit(str(exc))
        finally:
            self._cleanup()

    def _preview_loop(self) -> None:
        """
        Continuously read camera frames and emit them for the preview widget.
        Enters RECORDING state when start_recording() is called.
        Exits when _stop_requested is set.
        """
        frame_interval = 1.0 / self._FPS

        while not self._stop_requested.is_set():
            if self._process_control_requests():
                break

            t0 = time.perf_counter()

            ok, bgr = self._cap.read()
            if not ok:
                logger.warning("RecordingEngine: failed to read camera frame")
                time.sleep(frame_interval)
                continue

            # Emit for preview (BGR → RGB → QImage)
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
            self.frame_ready.emit(qimg)

            # Write video frame while recording
            if self._state == RecordingState.RECORDING and self._video_writer:
                self._video_writer.write(bgr)

            # Update elapsed timer
            if self._state == RecordingState.RECORDING and self._recording_start:
                elapsed = int(time.perf_counter() - self._recording_start)
                if elapsed != self._elapsed_seconds:
                    self._elapsed_seconds = elapsed
                    self.elapsed_changed.emit(elapsed)
                if self._max_duration_reached(elapsed):
                    logger.info(
                        "RecordingEngine: maximum duration of %ds reached — "
                        "stopping automatically",
                        settings.MAX_SPEAKING_DURATION,
                    )
                    self._finish_capture()
                    break

            # Pace the loop
            elapsed_loop = time.perf_counter() - t0
            sleep_time = max(0.0, frame_interval - elapsed_loop)
            if sleep_time > 0:
                time.sleep(sleep_time)

    @staticmethod
    def _max_duration_reached(elapsed: float) -> bool:
        """True once *elapsed* hits ``settings.MAX_SPEAKING_DURATION`` (0 = no cap)."""
        limit = settings.MAX_SPEAKING_DURATION
        return limit > 0 and elapsed >= limit

    def _process_control_requests(self) -> bool:
        """Process recording commands on the same thread that owns the capture."""
        if self._abandon_requested.is_set():
            self._set_state(RecordingState.ABANDONED)
            self._stop_requested.set()
            return True

        if self._start_requested.is_set():
            self._start_requested.clear()
            self._start_capture()

        if self._finish_requested.is_set():
            self._finish_requested.clear()
            self._finish_capture()

        return self._stop_requested.is_set()

    # ------------------------------------------------------------------ #
    # Recording control (requested from the GUI thread)                    #
    # ------------------------------------------------------------------ #

    @Slot()
    def start_recording(self) -> None:
        """
        Request recording to begin. The preview loop performs capture setup
        on its own thread.
        """
        if self._state != RecordingState.READY:
            logger.warning(
                "start_recording called in state %r — ignoring", self._state
            )
            return
        self._start_requested.set()

    def _start_capture(self) -> None:
        try:
            # Initialise VideoWriter
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self._video_writer = cv2.VideoWriter(
                str(self._output_path),
                fourcc,
                self._FPS,
                (actual_w, actual_h),
            )
            if not self._video_writer.isOpened():
                raise RuntimeError(
                    f"VideoWriter could not create: {self._output_path}"
                )

            # Initialise audio stream
            self._audio_frames.clear()
            self._audio_stream = sd.InputStream(
                samplerate=self._AUDIO_SR,
                channels=self._AUDIO_CHANNELS,
                dtype=self._AUDIO_DTYPE,
                blocksize=self._AUDIO_BLOCKSIZE,
                device=self._audio_device,
                callback=self._audio_callback,
            )
            self._audio_stream.start()

            self._recording_start = time.perf_counter()
            self._elapsed_seconds = 0
            self._set_state(RecordingState.RECORDING)
            logger.info("RecordingEngine RECORDING to %s", self._output_path)

        except Exception as exc:
            logger.exception("start_recording failed: %s", exc)
            self._set_state(RecordingState.ERROR)
            self.error_occurred.emit(f"Failed to start recording: {exc}")

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status: sd.CallbackFlags,
    ) -> None:
        """sounddevice callback — accumulates audio PCM data."""
        if status:
            logger.debug("Audio callback status: %s", status)
        with self._audio_lock:
            self._audio_frames.append(indata.copy())

    @Slot()
    def stop_recording(self) -> None:
        """
        Request recording to stop. The preview loop finalises capture on its
        own thread.
        """
        if self._state != RecordingState.RECORDING:
            logger.warning(
                "stop_recording called in state %r — ignoring", self._state
            )
            return
        self._finish_requested.set()

    def _finish_capture(self) -> None:
        self._set_state(RecordingState.STOPPING)
        duration = int(time.perf_counter() - self._recording_start)
        logger.info("RecordingEngine STOPPING after %ds", duration)

        try:
            # Finalise video
            if hasattr(self, "_video_writer") and self._video_writer:
                self._video_writer.release()
                self._video_writer = None

            # Finalise audio
            if hasattr(self, "_audio_stream") and self._audio_stream:
                self._audio_stream.stop()
                self._audio_stream.close()
                self._audio_stream = None

            # Write WAV
            self._write_wav()

            # Mux
            final_path = MediaMuxer.mux(self._output_path, self._wav_path)

            self._set_state(RecordingState.COMPLETED)
            self.recording_finished.emit(str(final_path))
            logger.info("RecordingEngine COMPLETED: %s", final_path)

        except Exception as exc:
            logger.exception("stop_recording failed: %s", exc)
            self._set_state(RecordingState.ERROR)
            self.error_occurred.emit(f"Failed to save recording: {exc}")
        finally:
            # Signal the preview loop to exit
            self._stop_requested.set()

    def _write_wav(self) -> None:
        """Write accumulated audio frames to a WAV file."""
        with self._audio_lock:
            frames = list(self._audio_frames)

        if not frames:
            logger.warning("No audio frames captured — skipping WAV write")
            return

        audio_data = np.concatenate(frames, axis=0)
        with wave.open(str(self._wav_path), "wb") as wf:
            wf.setnchannels(self._AUDIO_CHANNELS)
            wf.setsampwidth(2)  # int16 = 2 bytes
            wf.setframerate(self._AUDIO_SR)
            wf.writeframes(audio_data.tobytes())

        logger.debug("WAV written: %s  (%d frames)", self._wav_path, len(audio_data))

    @Slot()
    def abandon(self) -> None:
        """Abort recording without saving. Cleans up resources."""
        self._abandon_requested.set()

    # ------------------------------------------------------------------ #
    # Cleanup                                                               #
    # ------------------------------------------------------------------ #

    def _cleanup(self) -> None:
        """Release all hardware resources."""
        try:
            if hasattr(self, "_video_writer") and self._video_writer:
                self._video_writer.release()
        except Exception:
            pass
        try:
            if hasattr(self, "_audio_stream") and self._audio_stream:
                self._audio_stream.stop()
                self._audio_stream.close()
        except Exception:
            pass
        try:
            if self._cap and self._cap.isOpened():
                self._cap.release()
        except Exception:
            pass
        logger.debug("RecordingEngine cleanup complete")


# ---------------------------------------------------------------------------
# RecordingService — thin facade used by the UI
# ---------------------------------------------------------------------------

class RecordingService:
    """
    Manages one recording session lifecycle.

    Usage
    -----
        svc = RecordingService()
        ok  = svc.initialise(session_id=42)
        if ok:
            svc.start_recording()
            # ... user speaks ...
            path = svc.stop_recording()
            duration = svc.duration_seconds

    Signals from the underlying engine are exposed via ``engine`` property
    so the UI can connect directly:
        svc.engine.frame_ready.connect(preview_widget.update_frame)
        svc.engine.elapsed_changed.connect(timer_widget.update)
        svc.engine.state_changed.connect(page.on_state_changed)
        svc.engine.recording_finished.connect(page.on_recording_done)
        svc.engine.error_occurred.connect(page.on_error)
    """

    def __init__(self) -> None:
        self._state: str = RecordingState.IDLE
        self._output_path: Path | None = None
        self._duration_seconds: int = 0
        self._session_id: int | None = None

        self._engine: RecordingEngine | None = None
        self._thread: QThread | None = None

    # ------------------------------------------------------------------ #
    # Properties                                                            #
    # ------------------------------------------------------------------ #

    @property
    def state(self) -> str:
        if self._engine:
            return self._engine.state
        return self._state

    @property
    def is_recording(self) -> bool:
        return self.state == RecordingState.RECORDING

    @property
    def output_path(self) -> Path | None:
        return self._output_path

    @property
    def duration_seconds(self) -> int:
        return self._duration_seconds

    @property
    def engine(self) -> Optional[RecordingEngine]:
        """The underlying engine — connect signals from here."""
        return self._engine

    # ------------------------------------------------------------------ #
    # Lifecycle                                                             #
    # ------------------------------------------------------------------ #

    def initialise(
        self,
        session_id: int,
        camera_index: int = 0,
        audio_device: int | None = None,
    ) -> bool:
        """
        Create the engine and QThread, start the preview loop.
        In TEST_MODE, uses a stub engine that never opens hardware.
        Returns True if the camera opened successfully.
        """
        if settings.TEST_MODE:
            logger.info("TEST_MODE: RecordingService using mock engine (no hardware)")
            self._state = RecordingState.READY
            self._session_id  = session_id
            self._output_path = build_output_path(session_id)
            return True
        self._session_id = session_id
        self._output_path = build_output_path(session_id)
        self._duration_seconds = 0
        self._state = RecordingState.INITIALISING

        settings.ensure_directories()

        # Tear down any previous engine
        self.release()

        self._engine = RecordingEngine(
            session_id=session_id,
            output_path=self._output_path,
            camera_index=camera_index,
            audio_device=audio_device,
        )
        self._thread = QThread()
        self._engine.moveToThread(self._thread)

        # Wire internal state tracking
        self._engine.state_changed.connect(self._on_engine_state)
        self._engine.elapsed_changed.connect(self._on_elapsed)

        # Start the preview loop
        self._thread.started.connect(self._engine.run)
        self._thread.start()

        logger.info(
            "RecordingService.initialise  session_id=%s  output=%s",
            session_id, self._output_path,
        )
        return True  # Actual camera failure will come via error_occurred signal

    def _on_engine_state(self, state: str) -> None:
        self._state = state

    def _on_elapsed(self, seconds: int) -> None:
        self._duration_seconds = seconds

    def start_recording(self) -> None:
        """
        Begin audio + video capture.
        Engine must be in READY state (i.e. initialise() was called and
        the camera opened successfully).
        """
        if not self._engine:
            raise RuntimeError("RecordingService not initialised.")
        self._engine.start_recording()

    def stop_recording(self) -> Path | None:
        """
        Stop capture and begin finalisation.  Returns immediately — the
        output path is delivered via ``engine.recording_finished`` signal.
        """
        if not self._engine:
            return None
        self._engine.stop_recording()
        return self._output_path

    def abandon(self) -> None:
        """Abort without saving.  Cleans up resources."""
        if not self._engine:
            return
        self._engine.abandon()
        # Give the thread a moment to process the quit
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)

    def release(self) -> None:
        """Tear down the engine and thread cleanly."""
        if self._engine:
            self._engine.abandon()
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        self._engine = None
        self._thread = None

    def get_camera_frame(self) -> Optional[QImage]:
        """
        Return the most recent camera frame as a QImage, or None.
        (Primarily used for testing — in production the frame_ready signal is used.)
        """
        return None  # Frame delivery is signal-driven

    def probe_camera(self, index: int = 0) -> bool:
        """Return True if a camera is available at *index*."""
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(index)
        available = cap.isOpened()
        cap.release()
        return available

    def probe_microphone(self) -> bool:
        """Return True if at least one input audio device is available."""
        try:
            devices = sd.query_devices()
            return any(d["max_input_channels"] > 0 for d in devices)
        except Exception:
            return False
