"""Console recordings, independent of VM jobs; reuse the CLI's capture and encoder."""
from __future__ import annotations

import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from vmctl import recorder
from vmctl.errors import VMError


class ConsoleRecording:
    def __init__(self, vm: str, socket: Path, directory: Path, first: bytes, fps: int,
                 vnc: Path | None = None) -> None:
        self.vm = vm
        self.fps = fps
        self.period = 1 / fps
        self.socket = socket
        self.vnc = vnc
        self.recording = recorder.Recording(directory)
        self.started = time.monotonic()
        self.recording.add(first, self.started)
        self.bytes_stored = self.recording.frames[-1][0].stat().st_size
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.error = ""
        self.finished = False
        self.thread = threading.Thread(target=self._capture, daemon=True, name=f"console-record-{vm}")
        self.thread.start()

    def _capture(self) -> None:
        try:
            next_frame = self.started + self.period
            while not self.stop_event.wait(max(0, next_frame - time.monotonic())):
                next_frame = time.monotonic() + self.period
                frame = recorder.capture(self.socket, self.vnc)
                if frame is None:
                    break
                with self.lock:
                    if self.recording.add(frame, time.monotonic()):
                        self.bytes_stored += self.recording.frames[-1][0].stat().st_size
                if time.monotonic() - self.started >= 3600 or self.bytes_stored >= 512 * 1024 * 1024:
                    self.error = "Recording stopped at the 1 hour / 512 MiB limit. Captured frames are ready to download."
                    break
        except (OSError, VMError) as exc:
            self.error = str(exc)
        finally:
            with self.lock:
                # Account for the last still frame, including an immediate Stop.
                last = self.recording._last_at
                if self.recording.frames and last is not None:
                    path, held = self.recording.frames[-1]
                    self.recording.frames[-1] = (path, held + time.monotonic() - last)
                try:
                    self.recording.save_list(3600, self.period)
                except OSError as exc:
                    self.error = str(exc)
                finally:
                    self.finished = True

    def info(self) -> dict[str, Any]:
        with self.lock:
            return {"vm": self.vm, "status": "stopped" if self.finished else "recording",
                    "frames": self.recording.captures, "fps": self.fps, "error": self.error}

    def stop(self) -> dict[str, Any]:
        self.stop_event.set()
        self.thread.join(timeout=15)
        if self.thread.is_alive():
            raise VMError("Still finishing the last frame. Try Stop again.")
        return self.info()

    def export(self, kind: str) -> bytes:
        if kind not in ("gif", "mp4"):
            raise VMError("Choose GIF or MP4")
        if not self.finished:
            raise VMError("Stop the recording before downloading it")
        with self.lock:
            outputs = recorder.encode(self.recording, max_hold=3600, period=self.period,
                                      gif=kind == "gif", mp4=kind == "mp4", realtime_gif=True,
                                      gif_fps=1, mp4_fps=self.fps,
                                      gif_width=min(1280, self.recording.size[0]))
            return outputs[kind].read_bytes()


class Recordings:
    def __init__(self) -> None:
        self.sessions: dict[str, ConsoleRecording] = {}
        self.lock = threading.Lock()

    def start(self, vm: str, socket: Path, root: Path, fps: Any = 10, vnc: Path | None = None) -> dict[str, Any]:
        if type(fps) is not int or fps not in (1, 5, 10, 15):
            raise VMError("Recording frame rate must be 1, 5, 10 or 15 fps")
        if not shutil.which("ffmpeg"):
            raise VMError("Install ffmpeg on the host to record GIF or MP4")
        with self.lock:
            if any(s.vm == vm and not s.finished for s in self.sessions.values()):
                raise VMError("This VM is already being recorded")
            first = recorder.capture(socket, vnc)
            if first is None:
                raise VMError("No screen available: start the VM headless before recording")
            key = secrets.token_hex(16)
            session = ConsoleRecording(vm, socket, root / key, first, fps, vnc)
            self.sessions[key] = session
            return {"id": key, **session.info()}

    def get(self, key: str) -> ConsoleRecording:
        with self.lock:
            session = self.sessions.get(key)
        if session is None:
            raise VMError("Recording not found; the web server may have restarted")
        return session

    def close(self) -> None:
        with self.lock:
            sessions = list(self.sessions.values())
        for session in sessions:
            session.stop_event.set()
        for session in sessions:
            session.thread.join(timeout=15)
