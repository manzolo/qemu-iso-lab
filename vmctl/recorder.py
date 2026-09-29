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
import sys
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
# Most installers work on the serial console and leave text or black on the screen for 10-20
# minutes: sampled evenly, 40 of the 58 clips of 2026-09-28 were more than three quarters dark
# text. Console frames now get at most CONSOLE_FRAMES of a GIF (4 s at 2 fps, enough to say
# "the installer works here"); the rest goes to the graphical screens. A clip with no graphical
# frame at all (a server) is SERVER_SHARE of the usual length.
CONSOLE_FRAMES = 8
SERVER_SHARE = 0.5
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


def capture(sock: Path, vnc: Path | None = None) -> bytes | None:
    """One screendump as PPM bytes, or None when QEMU is not there (socket gone or refused).
    QMP serves one client at a time: the lock is shared with the report's screenshots.

    With *vnc* (the VM's ``runtime/vnc.sock``) a failed screendump falls back to a frame read
    over VNC: on ``virtio-vga-gl`` screendump answers "no surface" once the guest driver takes
    over, so the Arch/CachyOS/niri clips of 2026-09-28 froze on the boot loader."""
    if not sock.exists():
        return None
    fd, temporary = tempfile.mkstemp(prefix="vmctl-record-", suffix=".ppm", dir="/tmp")
    os.close(fd)
    try:
        try:
            with qemu.QMP_LOCK:
                qemu.qmp_execute(sock, "screendump", arguments={"filename": temporary}, timeout=10.0)
        except VMError:
            if vnc is None or not vnc.exists():
                return None
            try:
                return qemu.vnc_frame(vnc, timeout=10.0)
            except OSError:
                return None
        for _ in range(20):  # QEMU writes the file after answering; a partial read is a black frame
            data = Path(temporary).read_bytes()
            header = parse_ppm_header(data)
            if header is not None and len(data) >= header[2] + header[0] * header[1] * 3:
                if vnc is not None and vnc.exists() and data.count(0, header[2]) == len(data) - header[2]:
                    # All black: QXL in native mode can leave the VGA surface black while the
                    # desktop runs (Kubuntu, Unity, Studio 24.04 on 2026-09-27); VNC may see it.
                    try:
                        other = qemu.vnc_frame(vnc, timeout=10.0)
                        start = parse_ppm_header(other)
                        if start is not None and other.count(0, start[2]) != len(other) - start[2]:
                            return other
                    except OSError:
                        pass
                return data
            time.sleep(0.05)
        return None
    finally:
        Path(temporary).unlink(missing_ok=True)


def frame_kind(path: Path, step: int = 16) -> str:
    """"graphic" or "console" for a frame this module wrote (ppm_to_png: one IDAT, filter 0).

    A console is text or firmware on black: few distinct colours in a coarse grid of pixels and
    little of it lit. A desktop has gradients (Kali's black wallpaper still gives >100 colours),
    a graphical installer is mostly lit. Measured on the matrix of 2026-09-28: consoles 1-31
    colours and <4 % lit, desktops 126-3165 colours, Windows Setup 39 colours and 100 % lit.
    """
    try:
        data = path.read_bytes()
        width, height = struct.unpack(">II", data[16:24])
        idat, pos = b"", 8
        while pos + 8 <= len(data):
            (length,) = struct.unpack(">I", data[pos:pos + 4])
            if data[pos + 4:pos + 8] == b"IDAT":
                idat += data[pos + 8:pos + 8 + length]
            pos += 12 + length
        raw = zlib.decompress(idat)
    except (OSError, struct.error, zlib.error):
        return "graphic"
    stride = width * 3 + 1
    colours: set[tuple[int, int, int]] = set()
    lit = total = 0
    for y in range(0, height, step):
        row = raw[y * stride + 1:(y + 1) * stride]
        for pixel in zip(row[0::3 * step], row[1::3 * step], row[2::3 * step]):
            total += 1
            lit += max(pixel) > 48
            colours.add(pixel)
    return "graphic" if total and (lit / total > 0.15 or len(colours) > 64) else "console"


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
        self._kinds: dict[Path, str] = {}
        # The frame on screen when the row confirmed its desktop (``linger``): the clip never ends
        # after it. KDE answers the ACPI power button with a "Logging out in N seconds" countdown,
        # a new graphical frame every second, and kubuntu-10.04/14.04/16.04 ended on it (2026-09-29).
        self.settled: int | None = None

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

    def kind(self, index: int) -> str:
        path = self.frames[index][0]
        if path not in self._kinds:
            self._kinds[path] = frame_kind(path)
        return self._kinds[path]

    def final_index(self, window: float = FINAL_WINDOW) -> int:
        """The frame the GIF ends on.

        With graphical screens in the second half of the recording: in the last graphical stretch
        of the final *window* seconds, the latest frame held at least half as long as the longest
        there (a settled desktop, not the short fade that may follow it),
        or the last graphical one when the window holds only the power-off; never a console
        frame after the desktop. Otherwise (a server): among the detailed screens of the last
        *window* seconds (PNG at least half the size of the largest), the longest-held."""
        if not self.frames:
            return -1
        frames = self.frames
        if self.settled is not None and 0 <= self.settled < len(frames):
            if self.kind(self.settled) == "graphic":
                return self.settled  # the desktop the row confirmed (a KDE splash held longer came just before it)
            frames = frames[:self.settled + 1]
        total = sum(held for _, held in frames)
        start, candidates, late = 0.0, [], []
        for i, (path, held) in enumerate(frames):
            if start >= total - window:
                candidates.append(i)
            if start >= total / 2:
                late.append(i)
            start += held
        candidates = candidates or [len(frames) - 1]
        hold = lambda i: (max(self.frames[i][1], 1.0), i)  # the last frame has no hold yet
        graphic = [i for i in candidates if self.kind(i) == "graphic"]
        if graphic:
            # The last graphical stretch of the window (arch-noctalia: an earlier greeter frame
            # held longer than the desktop that followed), then its longest-held frame.
            run = [graphic[-1]]
            while run[0] - 1 in graphic:
                run.insert(0, run[0] - 1)
            longest = max(max(self.frames[i][1], 1.0) for i in run)
            return max(i for i in run if max(self.frames[i][1], 1.0) * 2 >= longest)
        late_graphic = [i for i in late if self.kind(i) == "graphic"]
        if late_graphic:
            return late_graphic[-1]
        size = {i: self.frames[i][0].stat().st_size if self.frames[i][0].is_file() else 0 for i in candidates}
        rich = [i for i in candidates if size[i] * 2 >= max(size.values())]
        return max(rich, key=hold)

    def gif_frames(self, count: int) -> list[Path]:
        """What the GIF shows, in order: up to *count* frames up to the final one, console
        stretches squeezed into CONSOLE_FRAMES, graphical screens spread over the rest; a
        recording without graphical frames gets SERVER_SHARE of *count*, evenly."""
        final = self.final_index()
        if final < 0 or count <= 0:
            return []
        indices = list(range(final + 1))
        graphic = [i for i in indices if self.kind(i) == "graphic"]
        console = [i for i in indices if self.kind(i) != "graphic"]

        def spread(items: list[int], n: int) -> list[int]:
            if n <= 0 or not items:
                return []
            if len(items) <= n:
                return items
            if n == 1:
                return [items[-1]]
            return sorted({items[round(k * (len(items) - 1) / (n - 1))] for k in range(n)})

        if not graphic:
            picked = spread(indices, max(2, round(count * SERVER_SHARE)))
        else:
            console_budget = min(len(console), CONSOLE_FRAMES, max(0, count - 1))
            picked = spread(console, console_budget) + spread(graphic, count - console_budget)
        picked = sorted(set(picked) | {final})
        return [self.frames[i][0] for i in picked]

    def gif_list(self, seconds: float, fps: int, final_hold: float = FINAL_HOLD) -> str:
        frames = self.gif_frames(max(2, int(seconds * fps)))
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
        if self.settled is not None:
            (self.frames_dir / SETTLED_FILE).write_text(f"{self.settled}\n", encoding="utf-8")
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
        settled = rec.frames_dir / SETTLED_FILE
        if settled.is_file():
            with contextlib.suppress(ValueError):
                rec.settled = int(settled.read_text(encoding="utf-8").strip())
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


def fit(size: tuple[int, int], rate: float, before: str = "") -> str:
    """A filter_complex head: every frame scaled into *size* and centred on a black background.

    Not scale + pad: pad computes its offsets for the first frame's size, so a larger frame later
    (Ubuntu 8.04: 720x400 text, then 1920x1200 at the desktop) was written past the canvas and
    ffmpeg died with SIGSEGV, leaving an empty GIF in the matrix of 2026-09-27. overlay re-centres
    each frame (eval=frame). The background runs at *rate*, the output frame rate of the clip.
    """
    w, h = size
    return (f"color=c=black:s={w}x{h}:r={rate:g}[bg];"
            f"[0:v]{before}scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
            "[bg][fg]overlay=x=(W-w)/2:y=(H-h)/2:eval=frame:shortest=1")


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
        filters = (fit(canvas(recording, gif_width), gif_fps,
                       f"fps={gif_fps}:eof_action=pass," if realtime_gif else "") + ",split[a][b];"
                   "[a]palettegen=stats_mode=full[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
        seconds = gif_seconds
        for attempt in range(4):
            if not dry_run:
                gif_list.write_text(recording.concat_list(max_hold, period) if realtime_gif
                                    else recording.gif_list(seconds, gif_fps), encoding="utf-8")
            runtime.run(["ffmpeg", "-y", "-loglevel", "error", "-reinit_filter", "0", "-f", "concat", "-safe", "0",
                         "-i", str(gif_list), "-filter_complex", filters, "-loop", "0", str(gif_path)], dry_run=dry_run, quiet=True)
            if (dry_run or realtime_gif or not gif_target_kb or not gif_path.is_file()
                    or gif_path.stat().st_size <= gif_target_kb * 1024):
                break
            seconds *= 0.7
        out["gif"] = gif_path
    if mp4:
        listing = recording.frames_dir / FRAMES_LIST
        mp4_path = recording.directory / "recording.mp4"
        rate = mp4_fps or max(1, round(1 / period))
        filters = fit(canvas(recording), rate, f"fps={mp4_fps}," if mp4_fps else "") + ",format=yuv420p"
        runtime.run(["ffmpeg", "-y", "-loglevel", "error", "-reinit_filter", "0", "-f", "concat", "-safe", "0",
                     "-i", str(listing), "-filter_complex", filters, "-fps_mode", "vfr",
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


# check-vms --record: before a row powers its VM off it waits (``linger``) until the recorder has
# seen DESKTOP_LINGER_SEC of graphical screen in a row, at most LINGER_TIMEOUT_SEC: the rows used
# to stop the guest seconds after the desktop check, and a dozen desktop clips of 2026-09-27 had
# one frame of desktop or none.
DESKTOP_LINGER_SEC = 15.0
SETTLED_FILE = "settled"  # next to frames.ffconcat: the index of Recording.settled


def settled_index(frame_count: int, capture_added_a_frame: bool) -> int:
    """The frame the row settled on, marked at the first capture after the linger returned:
    the frame on screen while the linger counted, which is the previous one when this very
    capture changed the screen (the row powers the VM off right after the linger, and the
    first new frame is then X's root weave or the console: slackware-14.0, 2026-09-29)."""
    if capture_added_a_frame and frame_count > 1:
        return frame_count - 2
    return max(0, frame_count - 1)
LINGER_TIMEOUT_SEC = 60.0
_WATCHES: dict[str, dict[str, float]] = {}
_WATCH_LOCK = threading.Lock()


def _watch_update(name: str | None, graphic: bool, seconds: float) -> None:
    if name is None:
        return
    with _WATCH_LOCK:
        watch = _WATCHES.setdefault(name, {"graphic": 0.0})
        watch["graphic"] = watch["graphic"] + seconds if graphic else 0.0


def linger(vm_name: str, seconds: float = DESKTOP_LINGER_SEC, timeout: float = LINGER_TIMEOUT_SEC,
           poll: float = 0.5) -> str | None:
    """Wait until the running recorder of *vm_name* has seen *seconds* of graphical screen in a row
    (at most *timeout*). Nothing to do without a recorder, or while an exception is propagating
    (the row failed: its clip does not matter more than its time). Returns a note, or None."""
    if sys.exc_info()[0] is not None:
        return None
    with _WATCH_LOCK:
        if vm_name not in _WATCHES:
            return None
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    while True:
        with _WATCH_LOCK:
            seen = _WATCHES.get(vm_name, {}).get("graphic", 0.0)
        if seen >= seconds:
            with _WATCH_LOCK:
                if vm_name in _WATCHES:
                    _WATCHES[vm_name]["settled"] = 1.0  # the recorder marks the frame on screen now
            waited = time.monotonic() - started
            return f"{vm_name}: {seen:.0f}s of desktop recorded" + (f" (waited {waited:.0f}s)" if waited >= 1 else "")
        if time.monotonic() >= deadline:
            return f"{vm_name}: no {seconds:.0f}s of graphical screen within {timeout:.0f}s (recorded {seen:.0f}s): the clip may end without a desktop"
        time.sleep(poll)


def record(vm_name: str, vm: dict[str, Any], *, fps: float = DEFAULT_FPS, max_hold: float = DEFAULT_MAX_HOLD,
           grace: float = DEFAULT_GRACE, duration: float | None = None, gif: bool = True, mp4: bool = False,
           gif_seconds: float = GIF_SECONDS, out_dir: Path | None = None, dry_run: bool = False,
           stop: threading.Event | None = None, wait_for_socket: bool = False, quiet: bool = False,
           watch: str | None = None) -> dict[str, Path]:
    """Capture until the VM is gone for *grace* seconds (or *duration*, Ctrl-C, *stop*), then encode.
    With *wait_for_socket* the recorder waits for the socket to appear first (a row of check-vms
    downloads its ISO before QEMU starts)."""
    if fps <= 0:
        raise VMError("--fps must be positive")
    period = 1.0 / fps
    sock = qemu.qmp_socket_path(vm)
    vnc = qemu.vnc_socket_path(vm)
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
            ppm = capture(sock, vnc)
            if ppm is not None:
                last_seen = now
                added = recording.add(ppm, now)
                if added:
                    stored += 1
                    if stored % 10 == 0:
                        say(f"{stored} frames kept of {recording.captures} captures, {now - started:.0f}s")
                if watch is not None:
                    _watch_update(watch, recording.kind(len(recording.frames) - 1) == "graphic", period)
                    with _WATCH_LOCK:
                        if recording.settled is None and _WATCHES.get(watch, {}).get("settled"):
                            # The frame the linger counted is the one on screen when it returned. The row
                            # powers the VM off right after, so a capture that just changed the screen is
                            # the shutdown (X's root weave, the console): the frame before it settled
                            # (slackware-14.0's clip ended on the weave, 2026-09-29).
                            recording.settled = settled_index(len(recording.frames), added)
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
            outcome.update(record(vm_name, vm, stop=stop, wait_for_socket=True, quiet=True, watch=vm_name, **options))
        except (VMError, OSError) as exc:  # pragma: no cover - depends on the live VM
            outcome["error"] = str(exc)

    with _WATCH_LOCK:
        _WATCHES[vm_name] = {"graphic": 0.0}
    thread = threading.Thread(target=run, name=f"record-{vm_name}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        with _WATCH_LOCK:
            _WATCHES.pop(vm_name, None)
        thread.join(timeout=600)
        if outcome.get("error"):
            ui.print_note(f"{vm_name}: recording skipped: {outcome['error']}")
        elif outcome:
            ui.print_kv("recording", ", ".join(ui.pretty_path(p) for p in outcome.values() if isinstance(p, Path)))
