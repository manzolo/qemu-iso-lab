"""``vmctl record``: a time-lapse of a VM's screen, from its QMP socket, as a GIF (and an MP4 on request).

One ``screendump`` per period (default 1 s) while ``artifacts/<vm>/runtime/qmp.sock`` answers,
so it works during a bootstrap and on any background VM. Identical consecutive frames are not
stored twice: a frame carries the time it stayed on screen. The GIF is the default and the
lightweight product: ``gif_seconds`` × ``gif_fps`` frames sampled evenly over the whole recording
(the first and the last always), 480 px wide, so a twenty-minute install is a 30 s loop of a few
hundred kilobytes that a README, the catalog site and a git repository can carry. The MP4
(``mp4=True``) keeps every stored frame, its hold capped at ``max_hold`` seconds, H.264 for a
player; it comes with a ``poster.png``. The recording survives the gap between an installer's
power-off and the first boot of the installed disk (``grace``), so one run covers install, first
boot and desktop; it ends when the VM has been gone for that long, at ``duration``, at Ctrl-C or
when the caller's ``stop`` event is set (``check-vms --record`` runs one recorder per row in a
thread, ``record_in_background``). Frames are PNG (written here, no dependency); the encodes need
``ffmpeg``. Output under ``artifacts/<vm>/recording/<stamp>/`` with ``latest`` pointing at the
newest; ``reencode()`` produces new outputs from the frames of an old recording.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import os
import re
import shutil
import struct
import tempfile
import threading
import time
import zlib
from pathlib import Path
from typing import Any, Iterator

from vmctl import qemu, runtime, ui
from vmctl.errors import VMError

DEFAULT_FPS = 1.0
DEFAULT_MAX_HOLD = 4.0
DEFAULT_GRACE = 30.0
GIF_WIDTH = 480
GIF_FPS = 2
GIF_SECONDS = 30.0
# How the GIF ends: on the richest screen of the last FINAL_WINDOW seconds (the desktop, not the
# power-off spinner the check-vms stop leaves at the very end), held FINAL_HOLD seconds, so the
# result stays readable even when most of the install was a scrolling text log.
FINAL_WINDOW = 90.0
FINAL_HOLD = 4.0
# What the repository and the catalog site carry per profile: above this the GIF is re-encoded
# with fewer frames (a scrolling installer log makes every frame different and heavy).
GIF_TARGET_KB = 900
FRAMES_LIST = "frames.ffconcat"


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
    """One screendump as PPM bytes, or None when QEMU is not there (socket gone or refused).
    QMP serves one client at a time: the lock is shared with the report's screenshots."""
    if not sock.exists():
        return None
    fd, temporary = tempfile.mkstemp(prefix="vmctl-record-", suffix=".ppm", dir="/tmp")
    os.close(fd)
    try:
        with qemu.QMP_LOCK:
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

    def sample(self, count: int) -> list[Path]:
        """Up to *count* frames spread evenly over the recording, the first and the last always."""
        if count <= 0 or not self.frames:
            return []
        if len(self.frames) <= count:
            return [path for path, _ in self.frames]
        last = len(self.frames) - 1
        picked = sorted({round(i * last / (count - 1)) for i in range(count)})
        return [self.frames[i][0] for i in picked]

    def final_index(self, window: float = FINAL_WINDOW) -> int:
        """The frame the GIF ends on: among the detailed screens of the last *window* seconds (PNG at
        least half the size of the largest there: a desktop, not a spinner), the one that stayed the
        longest, the latest on a tie: a settled desktop rather than the fade that led to it."""
        if not self.frames:
            return -1
        total = sum(held for _, held in self.frames)
        start, candidates = 0.0, []
        for i, (path, held) in enumerate(self.frames):
            if start >= total - window:
                candidates.append(i)
            start += held
        candidates = candidates or [len(self.frames) - 1]
        size = {i: self.frames[i][0].stat().st_size if self.frames[i][0].is_file() else 0 for i in candidates}
        rich = [i for i in candidates if size[i] * 2 >= max(size.values())]
        return max(rich, key=lambda i: (max(self.frames[i][1], 1.0), i))  # the last frame has no hold yet

    def gif_list(self, seconds: float, fps: int, final_hold: float = FINAL_HOLD) -> str:
        final = self.final_index()
        kept, self.frames = self.frames, self.frames[:final + 1]
        try:
            frames = self.sample(max(2, int(seconds * fps)))
        finally:
            self.frames = kept
        lines = ["ffconcat version 1.0"]
        for n, path in enumerate(frames):
            lines.append(f"file '{path.name}'")
            last = n == len(frames) - 1
            lines.append(f"duration {max(1 / fps, final_hold) if last else 1 / fps:.3f}")
        if frames:
            lines.append(f"file '{frames[-1].name}'")
        return "\n".join(lines) + "\n"

    def save_list(self, max_hold: float, period: float) -> Path:
        listing = self.frames_dir / FRAMES_LIST
        listing.write_text(self.concat_list(max_hold, period), encoding="utf-8")
        return listing

    @classmethod
    def load(cls, directory: Path) -> "Recording":
        """A recording from its frames directory (what ``save_list`` wrote): for re-encoding."""
        rec = cls(directory)
        listing = rec.frames_dir / FRAMES_LIST
        if not listing.is_file():
            raise VMError(f"no {FRAMES_LIST} under {ui.pretty_path(rec.frames_dir)}: not a recording")
        current: Path | None = None
        seen: list[tuple[Path, float]] = []
        for line in listing.read_text(encoding="utf-8").splitlines():
            if line.startswith("file '"):
                current = rec.frames_dir / line[6:-1]
            elif line.startswith("duration ") and current is not None:
                seen.append((current, float(line.split()[1])))
                current = None
        rec.frames = seen
        rec.captures = len(seen)
        return rec


def png_size(path: Path) -> tuple[int, int]:
    """Width and height from a PNG's IHDR (0, 0 when unreadable)."""
    try:
        head = path.read_bytes()[:24]
    except OSError:
        return (0, 0)
    if len(head) < 24 or not head.startswith(b"\x89PNG"):
        return (0, 0)
    width, height = struct.unpack(">II", head[16:24])
    return (int(width), int(height))


def canvas(recording: Recording, width: int | None = None) -> tuple[int, int]:
    """One output size for a whole clip, from the frame it ends on (even numbers for H.264).

    A guest changes resolution while it installs (ReactOS: 720x400 text Setup, 640x480, 800x600,
    1024x768), and every change made ffmpeg rebuild the filter graph: the palette was computed
    again from what followed and 55 of 61 GIF frames were lost. Each frame is fitted into this
    canvas instead, with -reinit_filter 0.
    """
    w, h = png_size(recording.frames[recording.final_index()][0]) if recording.frames else (0, 0)
    if not w or not h:
        w, h = recording.size if all(recording.size) else (640, 480)
    if width:
        w, h = width, round(width * h / w)
    return (max(2, w // 2 * 2), max(2, h // 2 * 2))


def fit(size: tuple[int, int]) -> str:
    w, h = size
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2")


def encode(recording: Recording, *, max_hold: float = DEFAULT_MAX_HOLD, period: float = 1.0, gif: bool = True,
           mp4: bool = False, gif_seconds: float = GIF_SECONDS, gif_fps: int = GIF_FPS, gif_width: int = GIF_WIDTH,
           dry_run: bool = False, realtime_gif: bool = False, mp4_fps: int | None = None,
           gif_target_kb: int | None = GIF_TARGET_KB) -> dict[str, Path]:
    """The GIF (sampled, small), the MP4 (every frame, H.264, plays everywhere) with its poster."""
    out: dict[str, Path] = {}
    if not recording.frames:
        raise VMError("nothing recorded: no frame came back from the VM")
    if not gif and not mp4:
        raise VMError("nothing to encode: choose the GIF, the MP4 or both")
    runtime.require_command("ffmpeg")
    if not dry_run:
        recording.save_list(max_hold, period)
    if gif:
        gif_list = recording.frames_dir / "gif.ffconcat"
        gif_path = recording.directory / "recording.gif"
        # stats_mode=full: the palette weighs every frame, so the held final screen keeps its colours
        # (diff let a long text log decide the palette and turned the Ubuntu desktop yellow).
        filters = (fit(canvas(recording, gif_width)) + ",split[a][b];"
                   "[a]palettegen=stats_mode=full[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
        if realtime_gif:
            filters = f"fps={gif_fps}:eof_action=pass," + filters
        seconds = gif_seconds
        for attempt in range(4):
            if not dry_run:
                gif_list.write_text(recording.concat_list(max_hold, period) if realtime_gif
                                    else recording.gif_list(seconds, gif_fps), encoding="utf-8")
            runtime.run(["ffmpeg", "-y", "-loglevel", "error", "-reinit_filter", "0", "-f", "concat", "-safe", "0",
                         "-i", str(gif_list), "-vf", filters, "-loop", "0", str(gif_path)], dry_run=dry_run, quiet=True)
            if (dry_run or realtime_gif or not gif_target_kb or not gif_path.is_file()
                    or gif_path.stat().st_size <= gif_target_kb * 1024):
                break
            seconds *= 0.7
        out["gif"] = gif_path
    if mp4:
        listing = recording.frames_dir / FRAMES_LIST
        mp4_path = recording.directory / "recording.mp4"
        filters = fit(canvas(recording)) + ",format=yuv420p"
        if mp4_fps:
            filters = f"fps={mp4_fps}," + filters
        runtime.run(["ffmpeg", "-y", "-loglevel", "error", "-reinit_filter", "0", "-f", "concat", "-safe", "0",
                     "-i", str(listing), "-vf", filters, "-fps_mode", "vfr",
                     "-c:v", "libx264", "-crf", "22", "-preset", "medium", "-movflags", "+faststart", str(mp4_path)],
                    dry_run=dry_run, quiet=True)
        out["mp4"] = mp4_path
        poster = recording.directory / "poster.png"
        if not dry_run:
            shutil.copy2(recording.frames[-1][0], poster)
        out["poster"] = poster
    return out


def _point_latest(directory: Path) -> None:
    latest = directory.parent / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(directory.name)
    except OSError:
        pass


def record(vm_name: str, vm: dict[str, Any], *, fps: float = DEFAULT_FPS, max_hold: float = DEFAULT_MAX_HOLD,
           grace: float = DEFAULT_GRACE, duration: float | None = None, gif: bool = True, mp4: bool = False,
           gif_seconds: float = GIF_SECONDS, out_dir: Path | None = None, dry_run: bool = False,
           stop: threading.Event | None = None, wait_for_socket: bool = False, quiet: bool = False) -> dict[str, Path]:
    """Capture until the VM is gone for *grace* seconds (or *duration*, Ctrl-C, *stop*), then encode.
    With *wait_for_socket* the recorder waits for the socket to appear first (a row of check-vms
    downloads its ISO before QEMU starts)."""
    if fps <= 0:
        raise VMError("--fps must be positive")
    period = 1.0 / fps
    sock = qemu.qmp_socket_path(vm)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    directory = out_dir or recording_dir(vm_name) / stamp
    say = (lambda text: None) if quiet else ui.print_note
    if dry_run:
        print(f"  would record {ui.pretty_path(sock)} every {period:g}s into {ui.pretty_path(directory)} "
              f"(ends {grace:g}s after the VM is gone" + (f" or after {duration:g}s" if duration else "")
              + f"; GIF of {gif_seconds:g}s" + (" and MP4" if mp4 else "") + ")")
        return {}
    if wait_for_socket:
        while not sock.exists():
            if stop is not None and stop.wait(period):
                raise VMError(f"{vm_name}: the VM never started, nothing recorded")
            if stop is None:
                time.sleep(period)
    elif not sock.exists():
        raise VMError(f"{vm_name} has no QMP socket ({ui.pretty_path(sock)}): start it headless, or a bootstrap, first")
    directory.mkdir(parents=True, exist_ok=True)
    recording = Recording(directory)
    if not quiet:
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
                        say(f"{stored} frames kept of {recording.captures} captures, {now - started:.0f}s")
            elif now - last_seen > grace:
                say(f"the VM has been gone for {grace:g}s: recording ends")
                break
            if duration is not None and now - started >= duration:
                say(f"{duration:g}s reached: recording ends")
                break
            if stop is not None and stop.is_set():
                break
            time.sleep(max(0.0, period - (time.monotonic() - now)))
    except KeyboardInterrupt:
        say("interrupted: encoding what was captured")
    if not quiet:
        ui.print_status("ok", f"{stored} frames kept of {recording.captures} captures ({recording.size[0]}x{recording.size[1]})")
    outputs = encode(recording, max_hold=max_hold, period=period, gif=gif, mp4=mp4, gif_seconds=gif_seconds)
    if out_dir is None:
        _point_latest(directory)
    if not quiet:
        for kind, path in outputs.items():
            ui.print_kv(kind, ui.pretty_path(path))
    return outputs


def reencode(directory: Path, *, max_hold: float = DEFAULT_MAX_HOLD, gif: bool = True, mp4: bool = False,
             gif_seconds: float = GIF_SECONDS, dry_run: bool = False) -> dict[str, Path]:
    """New outputs from the frames of an earlier recording (``artifacts/<vm>/recording/<stamp>``)."""
    recording = Recording.load(directory)
    outputs = encode(recording, max_hold=max_hold, gif=gif, mp4=mp4, gif_seconds=gif_seconds, dry_run=dry_run)
    for kind, path in outputs.items():
        ui.print_kv(kind, ui.pretty_path(path))
    return outputs


@contextlib.contextmanager
def record_in_background(vm_name: str, vm: dict[str, Any], enabled: bool = True, **options: Any) -> Iterator[None]:
    """A recorder thread for one check-vms row: waits for the row's QEMU, records until the row
    ends (or the VM is gone), encodes on the way out. A failure is a note, never the row's."""
    if not enabled:
        yield
        return
    stop = threading.Event()
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome.update(record(vm_name, vm, stop=stop, wait_for_socket=True, quiet=True, **options))
        except (VMError, OSError) as exc:  # pragma: no cover - depends on the live VM
            outcome["error"] = str(exc)

    thread = threading.Thread(target=run, name=f"record-{vm_name}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=600)
        if outcome.get("error"):
            ui.print_note(f"{vm_name}: recording skipped: {outcome['error']}")
        elif outcome:
            ui.print_kv("recording", ", ".join(ui.pretty_path(p) for p in outcome.values() if isinstance(p, Path)))
