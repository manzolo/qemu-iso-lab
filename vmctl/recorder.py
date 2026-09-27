"""``vmctl record``: a time-lapse of a VM's screen, from its QMP socket, as MP4 (and GIF).

One ``screendump`` per period (default 1 s) while ``artifacts/<vm>/runtime/qmp.sock`` answers,
so it works during a bootstrap and on any background VM. Identical consecutive frames are not
stored twice: a frame carries the time it stayed on screen, and the encoder caps that hold at
``max_hold`` seconds, which is what turns a twenty-minute install into a minute of video without
losing a screen. The recording survives the gap between an installer's power-off and the first
boot of the installed disk (``grace``), so one run covers install, first boot and desktop; it ends
when the VM has been gone for that long, at ``duration``, or at Ctrl-C. Frames are PNG (written
here, no dependency), the video needs ``ffmpeg``. Output under ``artifacts/<vm>/recording/<stamp>/``
with ``latest`` pointing at the newest.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
import zlib
from pathlib import Path
from typing import Any

from vmctl import qemu, runtime, ui
from vmctl.errors import VMError

DEFAULT_FPS = 1.0
DEFAULT_MAX_HOLD = 4.0
DEFAULT_GRACE = 30.0
GIF_WIDTH = 640
GIF_FPS = 4


def recording_dir(vm_name: str) -> Path:
    return runtime.vm_artifact_base(vm_name) / "recording"


_PPM_HEADER = re.compile(rb"P6\s+(\d+)\s+(\d+)\s+(\d+)\s")


def parse_ppm_header(ppm: bytes) -> tuple[int, int, int] | None:
    """(width, height, offset of the pixel bytes): the header ends with exactly one whitespace
    byte, and the first pixel byte may itself be one (a tab, a newline), so never split on it."""
    match = _PPM_HEADER.match(ppm)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), match.end()


def ppm_to_png(ppm: bytes) -> tuple[bytes, int, int]:
    """QEMU's screendump (binary PPM, P6) as a PNG, with the standard library only."""
    header = parse_ppm_header(ppm)
    if header is None:
        raise VMError("screendump did not produce a P6 PPM image")
    width, height, start = header
    raw = ppm[start:start + width * height * 3]
    rows = b"".join(b"\x00" + raw[y * width * 3:(y + 1) * width * 3] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))
    return png, width, height


def capture(sock: Path) -> bytes | None:
    """One screendump as PPM bytes, or None when QEMU is not there (socket gone or refused)."""
    if not sock.exists():
        return None
    fd, temporary = tempfile.mkstemp(prefix="vmctl-record-", suffix=".ppm", dir="/tmp")
    os.close(fd)
    try:
        qemu.qmp_execute(sock, "screendump", arguments={"filename": temporary}, timeout=10.0)
        for _ in range(20):  # QEMU writes the file after answering; a partial read is a black frame
            data = Path(temporary).read_bytes()
            header = parse_ppm_header(data)
            if header is not None and len(data) >= header[2] + header[0] * header[1] * 3:
                return data
            time.sleep(0.05)
        return None
    except VMError:
        return None
    finally:
        Path(temporary).unlink(missing_ok=True)


class Recording:
    """Frames on disk plus how long each stayed on screen."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.frames_dir = directory / "frames"
        self.frames: list[tuple[Path, float]] = []
        self._last_hash: str | None = None
        self._last_at: float | None = None
        self.captures = 0
        self.size = (0, 0)

    def add(self, ppm: bytes, at: float) -> bool:
        """Store the frame unless it repeats the previous one; either way, account for the time."""
        self.captures += 1
        digest = hashlib.sha1(ppm).hexdigest()
        if self._last_at is not None and self.frames:
            path, held = self.frames[-1]
            self.frames[-1] = (path, held + (at - self._last_at))
        self._last_at = at
        if digest == self._last_hash:
            return False
        self._last_hash = digest
        png, width, height = ppm_to_png(ppm)
        self.size = (width, height)
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        path = self.frames_dir / f"{len(self.frames):05d}.png"
        path.write_bytes(png)
        self.frames.append((path, 0.0))
        return True

    def concat_list(self, max_hold: float, period: float) -> str:
        lines = ["ffconcat version 1.0"]
        for path, held in self.frames:
            duration = max(period, min(held if held > 0 else period, max_hold))
            lines.append(f"file '{path.name}'")
            lines.append(f"duration {duration:.3f}")
        if self.frames:  # the concat demuxer drops the last duration unless the file repeats
            lines.append(f"file '{self.frames[-1][0].name}'")
        return "\n".join(lines) + "\n"


def encode(recording: Recording, max_hold: float, period: float, gif: bool, dry_run: bool = False) -> dict[str, Path]:
    """The MP4 (H.264, plays everywhere), optionally the GIF, and a poster (the last frame)."""
    out: dict[str, Path] = {}
    if not recording.frames:
        raise VMError("nothing recorded: no frame came back from the VM")
    runtime.require_command("ffmpeg")
    listing = recording.frames_dir / "frames.ffconcat"
    if not dry_run:
        listing.write_text(recording.concat_list(max_hold, period), encoding="utf-8")
    mp4 = recording.directory / "recording.mp4"
    runtime.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                 "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p", "-fps_mode", "vfr",
                 "-c:v", "libx264", "-crf", "22", "-preset", "medium", "-movflags", "+faststart", str(mp4)],
                dry_run=dry_run, quiet=True)
    out["mp4"] = mp4
    poster = recording.directory / "poster.png"
    if not dry_run:
        shutil.copy2(recording.frames[-1][0], poster)
    out["poster"] = poster
    if gif:
        gif_path = recording.directory / "recording.gif"
        filters = (f"fps={GIF_FPS},scale={GIF_WIDTH}:-1:flags=lanczos,split[a][b];"
                   "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
        runtime.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-vf", filters, "-loop", "0", str(gif_path)],
                    dry_run=dry_run, quiet=True)
        out["gif"] = gif_path
    return out


def record(vm_name: str, vm: dict[str, Any], *, fps: float = DEFAULT_FPS, max_hold: float = DEFAULT_MAX_HOLD,
           grace: float = DEFAULT_GRACE, duration: float | None = None, gif: bool = False,
           out_dir: Path | None = None, dry_run: bool = False) -> dict[str, Path]:
    """Capture until the VM is gone for *grace* seconds (or *duration*, or Ctrl-C), then encode."""
    if fps <= 0:
        raise VMError("--fps must be positive")
    period = 1.0 / fps
    sock = qemu.qmp_socket_path(vm)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    directory = out_dir or recording_dir(vm_name) / stamp
    if dry_run:
        print(f"  would record {ui.pretty_path(sock)} every {period:g}s into {ui.pretty_path(directory)} "
              f"(holds capped at {max_hold:g}s, ends {grace:g}s after the VM is gone"
              + (f" or after {duration:g}s" if duration else "") + (", then a GIF too" if gif else "") + ")")
        return {}
    if not sock.exists():
        raise VMError(f"{vm_name} has no QMP socket ({ui.pretty_path(sock)}): start it headless, or a bootstrap, first")
    directory.mkdir(parents=True, exist_ok=True)
    recording = Recording(directory)
    ui.print_header(f"Recording {vm_name}")
    ui.print_kv("frames", ui.pretty_path(recording.frames_dir))
    ui.print_note(f"one screendump every {period:g}s; Ctrl-C ends the recording and encodes what was captured")
    started = time.monotonic()
    last_seen = started
    stored = 0
    try:
        while True:
            now = time.monotonic()
            ppm = capture(sock)
            if ppm is not None:
                last_seen = now
                if recording.add(ppm, now):
                    stored += 1
                    if stored % 10 == 0:
                        ui.print_note(f"{stored} frames kept of {recording.captures} captures, {now - started:.0f}s")
            elif now - last_seen > grace:
                ui.print_note(f"the VM has been gone for {grace:g}s: recording ends")
                break
            if duration is not None and now - started >= duration:
                ui.print_note(f"{duration:g}s reached: recording ends")
                break
            time.sleep(max(0.0, period - (time.monotonic() - now)))
    except KeyboardInterrupt:
        ui.print_note("interrupted: encoding what was captured")
    ui.print_status("ok", f"{stored} frames kept of {recording.captures} captures ({recording.size[0]}x{recording.size[1]})")
    outputs = encode(recording, max_hold, period, gif)
    latest = directory.parent / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(directory.name)
    except OSError:
        pass
    for kind, path in outputs.items():
        ui.print_kv(kind, ui.pretty_path(path))
    return outputs
