"""Self-contained validation reports and stdlib-only QMP screenshots."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import platform
import socket
import struct
import subprocess
import threading
import time
from typing import Any, Iterator
import zlib

from vmctl import qemu, runtime, state, ui
from vmctl.errors import VMError


def ppm_to_png(data: bytes) -> bytes:
    """Decode an 8-bit binary P6 PPM, preserving whitespace-valued pixels."""
    offset = 0
    tokens: list[bytes] = []
    while len(tokens) < 4:
        while offset < len(data) and data[offset] in b" \t\r\n":
            offset += 1
        if offset < len(data) and data[offset] == ord("#"):
            end = data.find(b"\n", offset)
            if end < 0:
                raise ValueError("Unterminated PPM comment")
            offset = end + 1
            continue
        start = offset
        while offset < len(data) and data[offset] not in b" \t\r\n":
            offset += 1
        if start == offset:
            raise ValueError("Truncated PPM header")
        tokens.append(data[start:offset])
    if tokens[0] != b"P6" or tokens[3] != b"255":
        raise ValueError("Expected an 8-bit P6 PPM")
    width, height = int(tokens[1]), int(tokens[2])
    if width <= 0 or height <= 0 or offset >= len(data):
        raise ValueError("Invalid PPM dimensions/header")
    offset += 1  # exactly one separator; subsequent whitespace is pixel data
    pixels = data[offset:]
    if len(pixels) != width * height * 3:
        raise ValueError("Invalid PPM raster length")

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack("!I", len(payload)) + kind + payload + struct.pack("!I", zlib.crc32(kind + payload))

    rows = b"".join(b"\0" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


CONSOLE_WAKE_DELAY_SEC = 2.0
DESKTOP_SIZE_ENCODING = -223
BLANK_RETRIES = 3
BLANK_PIXEL_RATIO = 0.001


def wake_console(vm: dict[str, Any]) -> None:
    """Make the guest paint its console right before a screendump.

    A text-mode guest paints tty1 only when it has something to say, and the screenshot can
    land in the gap between "VM started" and "login prompt printed": the capture is then an
    all-black rectangle that says nothing about the install (seen in the report for the Rocky
    row). Enter costs nothing on a login prompt (agetty prints it again) and also brings back
    a console the kernel has blanked after ten idle minutes.
    """
    qemu.qmp_command(
        qemu.qmp_socket_path(vm),
        "send-key",
        arguments={"keys": [{"type": "qcode", "data": "ret"}]},
    )
    time.sleep(CONSOLE_WAKE_DELAY_SEC)


def looks_blank(ppm_bytes: bytes) -> bool:
    """True when almost every pixel is black: a screenshot with nothing to show."""
    body = ppm_bytes[ppm_bytes.find(b"255\n") + 4:] if b"255\n" in ppm_bytes else ppm_bytes
    if not body:
        return True
    lit = sum(1 for index in range(0, len(body) - 2, 3) if body[index] or body[index + 1] or body[index + 2])
    return lit <= max(1, int(len(body) / 3 * BLANK_PIXEL_RATIO))


def _rfb_read(sock: socket.socket, count: int) -> bytes:
    chunks = []
    remaining = count
    while remaining > 0:
        chunk = sock.recv(min(remaining, 1 << 16))
        if not chunk:
            raise OSError("VNC connection closed early")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def capture_via_vnc(vm: dict[str, Any], timeout: float = 15.0) -> bytes:
    """Grab the framebuffer over the VM's own VNC socket and return it as a PNG.

    QMP ``screendump`` fails on the accelerated displays (``virtio-vga-gl``), so those rows
    used to reach the report with no image at all. The VNC server QEMU already exposes on
    ``artifacts/<vm>/runtime/vnc.sock`` renders the same screen; RFB 3.8 with RAW encoding is
    a few dozen lines and needs no dependency.
    """
    path = qemu.vnc_socket_path(vm)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(path))
        version = _rfb_read(sock, 12)
        if not version.startswith(b"RFB "):
            raise OSError(f"Unexpected VNC greeting: {version!r}")
        sock.sendall(b"RFB 003.008\n")
        count = _rfb_read(sock, 1)[0]
        if count == 0:
            reason_length = struct.unpack("!I", _rfb_read(sock, 4))[0]
            raise OSError(f"VNC refused the connection: {_rfb_read(sock, reason_length)!r}")
        types = _rfb_read(sock, count)
        if 1 not in types:
            raise OSError("VNC server requires authentication")
        sock.sendall(bytes([1]))
        if struct.unpack("!I", _rfb_read(sock, 4))[0] != 0:
            raise OSError("VNC authentication failed")
        sock.sendall(bytes([1]))  # share the session, do not disconnect other viewers
        width, height = struct.unpack("!HH", _rfb_read(sock, 4))
        _rfb_read(sock, 16)  # server pixel format (16 bytes), replaced below
        name_length = struct.unpack("!I", _rfb_read(sock, 4))[0]
        _rfb_read(sock, name_length)
        if width == 0 or height == 0:
            raise OSError("VNC reported an empty framebuffer")
        # 32bpp true colour, big-endian, so a pixel is 0x00RRGGBB in wire order.
        pixel_format = struct.pack("!BBBBHHHBBBxxx", 32, 24, 1, 1, 255, 255, 255, 16, 8, 0)
        sock.sendall(b"\x00\x00\x00\x00" + pixel_format)
        # RAW plus DesktopSize: a guest that switches video mode after boot resizes the
        # framebuffer, and the size from ServerInit would no longer describe the rectangles.
        sock.sendall(struct.pack("!BBH", 2, 0, 2) + struct.pack("!ii", 0, DESKTOP_SIZE_ENCODING))
        sock.sendall(struct.pack("!BBHHHH", 3, 0, 0, 0, width, height))
        rows = [bytearray(width * 3) for _ in range(height)]
        painted = 0
        deadline = time.monotonic() + timeout
        while painted < width * height and time.monotonic() < deadline:
            message = _rfb_read(sock, 1)[0]
            if message != 0:  # only framebuffer updates are requested
                continue
            _rfb_read(sock, 1)
            rectangles = struct.unpack("!H", _rfb_read(sock, 2))[0]
            resized = False
            for _ in range(rectangles):
                rx, ry, rw, rh, encoding = struct.unpack("!HHHHi", _rfb_read(sock, 12))
                if encoding == DESKTOP_SIZE_ENCODING:
                    # Pseudo-encoding: no pixel data follows, so keep reading this message.
                    width, height = rw, rh
                    rows = [bytearray(width * 3) for _ in range(height)]
                    painted = 0
                    resized = True
                    continue
                if encoding != 0:
                    raise OSError(f"Unsupported VNC encoding: {encoding}")
                data = _rfb_read(sock, rw * rh * 4)
                if rx + rw > width or ry + rh > height:
                    raise OSError("VNC sent a rectangle outside the framebuffer")
                for row in range(rh):
                    target = rows[ry + row]
                    base = row * rw * 4
                    for column in range(rw):
                        pixel = base + column * 4
                        offset = (rx + column) * 3
                        target[offset:offset + 3] = data[pixel + 1:pixel + 4]
                painted += rw * rh
            if resized:
                sock.sendall(struct.pack("!BBHHHH", 3, 0, 0, 0, width, height))
        raster = b"".join(bytes(row) for row in rows)
        return ppm_to_png(f"P6 {width} {height} 255\n".encode() + raster)


def capture_screenshot(vm_name: str, vm: dict[str, Any], directory: Path, wake: bool = True) -> str | None:
    ppm = directory / "screens" / f"{vm_name}.ppm"
    png = ppm.with_suffix(".png")
    attempts = BLANK_RETRIES if wake else 1
    try:
        ppm.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(attempts):
            if wake:
                wake_console(vm)
            if not qemu.qmp_command(qemu.qmp_socket_path(vm), "screendump", arguments={"filename": str(ppm.resolve())}):
                # Accelerated displays refuse screendump: read the same screen over VNC.
                try:
                    png.write_bytes(capture_via_vnc(vm))
                    return None
                except (OSError, ValueError, struct.error) as exc:
                    return f"Screenshot unavailable: QMP screendump did not succeed and VNC capture failed ({exc})"
            raw = ppm.read_bytes()
            png.write_bytes(ppm_to_png(raw))
            # A guest that is genuinely dark stays dark: give it a few tries, then keep what
            # it showed rather than failing the row over a screenshot.
            if not looks_blank(raw) or attempt == attempts - 1:
                return None
        return None
    except (OSError, ValueError) as exc:
        return f"Screenshot unavailable: {exc}"
    finally:
        try:
            ppm.unlink(missing_ok=True)
        except OSError:
            pass


def phase(args: argparse.Namespace, name: str) -> None:
    parent = getattr(args, "_report_parent", args)
    parent._report_phase = name


def capture(vm_name: str, vm: dict[str, Any], args: argparse.Namespace, wake: bool = True) -> None:
    directory = getattr(args, "_report_dir", None)
    if not directory or args.dry_run:
        return
    error = capture_screenshot(vm_name, vm, Path(directory), wake=wake)
    args._screenshot_error = error


@contextmanager
def watch_boot(vm_name: str, vm: dict[str, Any], args: argparse.Namespace) -> Iterator[None]:
    """Keep the latest live framebuffer without changing token/poweroff handling."""
    stop = threading.Event()

    def watch() -> None:
        while not stop.is_set():
            # No wake-up here: this loop runs while the installer is driving the guest.
            capture(vm_name, vm, args, wake=False)
            stop.wait(0.5)

    thread = threading.Thread(target=watch, daemon=True)
    enabled = bool(getattr(args, "_report_dir", None)) and not args.dry_run
    if enabled:
        thread.start()
    try:
        yield
    finally:
        stop.set()
        if enabled:
            thread.join()


def init(args: argparse.Namespace) -> Path | None:
    requested = getattr(args, "report", None)
    if requested is None and not getattr(args, "open", False):
        return None
    directory = (runtime.resolve_path(requested) if requested else
                 state.ROOT / "artifacts" / "check-vms" / datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    directory = directory.resolve()
    for name in getattr(args, "vms", []) or []:
        if directory.is_relative_to(runtime.vm_artifact_base(name).resolve()):
            raise VMError("Report directory must be outside per-VM artifacts so --restore preserves it")
    if (directory / "results").exists():
        raise VMError(f"Report directory already contains results: {directory}; choose a new directory")
    args._report_dir = str(directory)
    if not args.dry_run:
        (directory / "results").mkdir(parents=True, exist_ok=True)
        (directory / "screens").mkdir(exist_ok=True)
    return directory


def record(vm_name: str, vm: dict[str, Any], args: argparse.Namespace, status: str, detail: str, seconds: float, flow: str) -> None:
    directory = getattr(args, "_report_dir", None)
    if not directory or args.dry_run:
        return
    base = Path(directory)
    screenshot = base / "screens" / f"{vm_name}.png"
    error = getattr(args, "_screenshot_error", None)
    outcome = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}.get(status, "WARN")
    if not screenshot.exists() and outcome == "PASS":
        outcome = "WARN"
    if not screenshot.exists():
        detail += "; " + (error or "Screenshot unavailable: no live framebuffer captured")
    result = {"id": vm_name, "name": vm.get("name", vm_name), "flow": flow,
              "profile_status": vm.get("meta", {}).get("status", "manual"),
              "profile_verified": vm.get("meta", {}).get("verified"),
              "status": outcome, "phase": getattr(args, "_report_phase", "validation"),
              "seconds": round(seconds, 3), "detail": detail,
              "screenshot": f"screens/{vm_name}.png" if screenshot.exists() else None}
    destination = base / "results" / f"{vm_name}.json"
    runtime.ensure_parent(destination)
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def render_html(results: list[dict[str, Any]], metadata: dict[str, str], directory: Path) -> str:
    def esc(value: object) -> str:
        return html.escape(str(value), quote=True)

    counts = {status: sum(r["status"] == status for r in results) for status in ("PASS", "WARN", "FAIL", "SKIP")}
    overall = "FAIL" if counts["FAIL"] else "WARN" if counts["WARN"] else "PASS" if counts["PASS"] else "SKIP"
    headline = {"PASS": "All checks passed", "WARN": "Completed with warnings", "FAIL": "Some checks failed", "SKIP": "No checks completed"}[overall]
    cards = (f'<button type="button" class="metric total" data-status="" title="Show every VM"><span>Total profiles</span>'
             f'<strong>{len(results)}</strong><small>Validation matrix · show all</small></button>')
    labels = {"PASS": "Passed", "WARN": "Warnings", "FAIL": "Failed", "SKIP": "Skipped"}
    for status, count in counts.items():
        cards += (f'<button type="button" class="metric {status}" data-status="{status}" title="Show only {status} results">'
                  f'<span>{labels[status]}</span><strong>{count}</strong><small>{status}: {count} · click to filter</small></button>')
    rows: list[str] = []
    lightboxes: list[str] = []
    for index, result in enumerate(results, 1):
        status = str(result.get("status", "SKIP"))
        if status not in counts:
            status = "SKIP"
        image = '<div class="no-screen"><span>Unavailable</span><small>No framebuffer captured</small></div>'
        screenshot = result.get("screenshot")
        if screenshot:
            path = (directory / screenshot).resolve()
            if path.is_relative_to(directory.resolve()) and path.is_file():
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                source = f"data:image/png;base64,{encoded}"
                image = (f'<a class="screen" href="#screen-{index}" aria-label="Enlarge screenshot of {esc(result.get("name", "VM"))}">'
                         f'<img alt="Final VM screen" src="{source}"><span>View screenshot <b>↗</b></span></a>')
                lightboxes.append(
                    f'<section class="lightbox" id="screen-{index}" role="dialog" aria-modal="true" aria-label="VM screenshot">'
                    '<a class="lightbox-backdrop" href="#results" aria-label="Close screenshot"></a>'
                    f'<div class="lightbox-content"><header><strong>{esc(result.get("name", "VM"))}</strong>'
                    f'<a href="#results">Close ×</a></header><img alt="Full-size VM screen" src="{source}"></div></section>')
        seconds = float(result.get("seconds", 0))
        minutes, remaining = divmod(round(seconds), 60)
        duration = f"{minutes}m {remaining:02d}s" if minutes else f"{remaining}s"
        rows.append(
            f'<tr data-vm="{esc(str(result.get("name", "")) + " " + str(result.get("id", "")))}" data-status="{status}"><td class="vm-cell"><span class="row-number">{index:02d}</span><div><strong>{esc(result.get("name", ""))}</strong>'
            f'<code>{esc(result.get("id", ""))}</code>'
            f'<small class="profile-meta">Profile status: {esc(result.get("profile_status") or "not recorded")}</small>'
            f'<small class="profile-meta">Last live PASS: {esc(result.get("profile_verified") or "not recorded")}</small></div></td>'
            f'<td><code class="flow">{esc(result.get("flow", ""))}</code></td>'
            f'<td><button type="button" class="badge {status}" data-status="{status}" title="Show only {status} results"><i></i>{status}</button></td>'
            f'<td><span class="phase">{esc(result.get("phase", ""))}</span></td>'
            f'<td class="duration"><strong>{duration}</strong><small>{seconds:g} s</small></td>'
            f'<td class="detail">{esc(result.get("detail", ""))}</td><td>{image}</td></tr>')
    meta_items = []
    for key, value in metadata.items():
        display = value
        if key == "commit":
            display = value[:12]
        elif key == "date":
            try:
                display = datetime.fromisoformat(value).strftime("%d %b %Y · %H:%M UTC")
            except ValueError:
                pass
        meta_items.append(f'<div title="{esc(value)}"><span>{esc(key)}</span><strong>{esc(display)}</strong></div>')
    css = """
:root{color-scheme:dark;--bg:#0b1019;--panel:#121b29;--line:#263347;--muted:#91a2b9;--text:#edf3fb;
--green:#76dfae;--amber:#efc778;--red:#f29cab;--blue:#9bbdff}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:inherit}main{max-width:1560px;margin:auto;padding:44px 40px}
.hero{display:flex;justify-content:space-between;align-items:center;gap:24px;margin-bottom:28px}.eyebrow{text-transform:uppercase;
letter-spacing:.18em;font-size:11px;font-weight:700;color:var(--blue);display:flex;align-items:center;gap:10px}.logo{width:26px;height:26px;
display:inline-grid;place-items:center;border:1px solid #45638c;border-radius:7px;background:#1b2a40;font-size:15px;letter-spacing:0}
h1{font-size:32px;line-height:1.2;letter-spacing:-.035em;margin:15px 0 10px;font-weight:650}.subtitle{margin:0;color:var(--muted)}
.run-state{padding:10px 16px;border:1px solid var(--line);border-radius:30px;font-weight:600;white-space:nowrap}
.profile-meta{display:block;color:var(--muted);font-size:11px;margin-top:6px}
.metadata{display:flex;flex-wrap:wrap;gap:28px;border-top:1px solid var(--line);padding-top:20px;margin-bottom:28px}
.metadata div{display:flex;gap:10px;align-items:center}.metadata span{text-transform:uppercase;font-size:10px;letter-spacing:.12em;color:var(--muted)}
.metadata strong{font-size:12px;font-weight:500;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.metrics{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px;margin:0 0 32px}.metric{padding:20px 22px;
border:1px solid var(--line);border-radius:12px;background:linear-gradient(135deg,#172235,#111a28);position:relative;overflow:hidden}
.metric:before{content:"";position:absolute;left:0;top:20px;bottom:20px;width:3px;background:currentColor;border-radius:4px}
.metric{font:inherit;color:inherit;text-align:left;cursor:pointer;width:100%;transition:border-color .15s,transform .15s}
.metric:hover{border-color:currentColor;transform:translateY(-1px)}.metric:focus-visible{outline:2px solid var(--blue);outline-offset:2px}
.metric.active{border-color:currentColor;box-shadow:0 0 0 1px currentColor inset,0 10px 30px #0004}
.metric span{display:block;font-size:12px;font-weight:600}.metric strong{display:block;font-size:34px;line-height:1.3;margin:8px 0;font-weight:650;
font-variant-numeric:tabular-nums;letter-spacing:-.04em}.metric small{color:var(--muted);font-size:11px}.total{color:var(--blue)}
.PASS{color:var(--green)}.WARN{color:var(--amber)}.FAIL{color:var(--red)}.SKIP{color:#9eabc0}
.results{background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:0 14px 45px #0002}
.section-heading{display:flex;align-items:center;justify-content:space-between;padding:20px 24px;border-bottom:1px solid var(--line);gap:16px}
h2{font-size:16px;margin:0;font-weight:600;letter-spacing:-.02em}.section-heading span{font-size:12px;color:var(--muted)}
.filters{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:16px 24px;border-bottom:1px solid var(--line);background:#10192580}
.filter-search{display:flex;align-items:center;gap:10px;flex:1;min-width:230px;padding:0 12px;background:#0b1421;border:1px solid #35465e;border-radius:7px;color:var(--muted)}
.filter-search:focus-within{border-color:var(--blue);box-shadow:0 0 0 2px #9bbdff18}.filter-search input{width:100%;padding:10px 0;border:0;outline:none;background:transparent;color:var(--text);font:inherit;font-size:12px}
.filter-search input::placeholder{color:#8194ae}.filter-status{display:flex;align-items:center;gap:9px;color:var(--muted);font-size:11px}
.filter-status select,.reset-filters{padding:9px 12px;border:1px solid #35465e;border-radius:7px;background:#142033;color:var(--text);font:inherit;font-size:12px}
.reset-filters{cursor:pointer;color:var(--blue)}.reset-filters:disabled{opacity:.4;cursor:default}.filter-count{font-size:11px;color:var(--muted);white-space:nowrap}
.empty-state{padding:40px 24px;text-align:center;color:var(--muted)}tr[hidden],.empty-state[hidden]{display:none!important}
.table-scroll{overflow-x:auto}table{width:100%;min-width:1130px;border-collapse:collapse;text-align:left;table-layout:fixed}
th{padding:13px 18px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);background:#101925}
th:first-child{width:23%}th:nth-child(2){width:15%}th:nth-child(3){width:9%}th:nth-child(4){width:12%}th:nth-child(5){width:8%}
th:nth-child(6){width:18%}th:nth-child(7){width:15%}td{padding:22px 18px;vertical-align:top;border-top:1px solid var(--line)}
tr:hover td{background:#17233580}.vm-cell{display:flex;gap:12px}.row-number{color:#6c809b;font-size:11px;margin-top:4px;font-variant-numeric:tabular-nums}
.vm-cell strong{display:block;font-size:14px;font-weight:600;margin-bottom:6px}.vm-cell code{font-size:11px;color:var(--muted);white-space:normal;
overflow-wrap:break-word}code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.flow{font-size:11px;white-space:nowrap;color:#bfd0e7}
.badge{display:inline-flex;align-items:center;gap:7px;padding:5px 9px;font-size:10px;font-weight:750;letter-spacing:.04em;border-radius:6px;
background:#ffffff06;border:1px solid #ffffff0d;white-space:nowrap;font-family:inherit;cursor:pointer}.badge:hover{border-color:currentColor}
.badge:focus-visible{outline:2px solid var(--blue);outline-offset:2px}.badge.PASS{background:#143c2b;border-color:#24523e}.badge.FAIL{background:#422332;border-color:#633546}
.badge.WARN{background:#3a3220;border-color:#584a2e}.badge i{width:5px;height:5px;background:currentColor;border-radius:50%}
.phase{font-size:11px;color:#b2c2d7;padding:5px 8px;border-radius:5px;background:#1e2a3b;display:inline-block;white-space:nowrap}
.duration strong{display:block;font-size:12px;font-weight:600;white-space:nowrap;font-variant-numeric:tabular-nums}.duration small{display:block;
font-size:10px;color:var(--muted);margin-top:5px}.detail{font-size:12px;line-height:1.7;color:#a8bad1;white-space:pre-wrap;overflow-wrap:break-word}
.screen{display:block;max-width:180px;border:1px solid #35465e;border-radius:7px;overflow:hidden;text-decoration:none;background:#080c12;
transition:border-color .15s,transform .15s}.screen:hover{border-color:var(--blue);transform:translateY(-2px)}.screen img{display:block;
width:100%;height:88px;object-fit:contain;background:#000}.screen span{display:flex;justify-content:space-between;padding:7px 9px;font-size:10px;
color:#b4c7e2;background:#1b283b}.screen b{font-size:12px}.no-screen{border:1px dashed #35465e;border-radius:7px;padding:15px 10px;color:var(--muted);
font-size:11px}.no-screen small{display:block;font-size:10px;margin-top:5px}.footer{display:flex;justify-content:space-between;gap:20px;
padding:18px 2px;color:#72849d;font-size:11px}.footer strong{font-weight:600;color:#9aabc1}
.lightbox{display:none;position:fixed;inset:0;z-index:10;align-items:center;justify-content:center;padding:30px}.lightbox:target{display:flex}
.lightbox-backdrop{position:absolute;inset:0;background:#030711e8;backdrop-filter:blur(8px)}.lightbox-content{position:relative;max-width:95vw;
background:#101925;border:1px solid #35465e;border-radius:10px;overflow:hidden;box-shadow:0 25px 100px #0009}.lightbox-content header{display:flex;
justify-content:space-between;align-items:center;gap:30px;padding:15px 20px}.lightbox-content header a{font-size:12px;color:var(--blue);text-decoration:none;
border:1px solid #35465e;border-radius:5px;padding:5px 10px}.lightbox-content img{display:block;max-width:90vw;max-height:80vh;object-fit:contain}
@media(max-width:1100px){table{min-width:0;display:block}thead{display:none}tbody{display:block}tr{display:grid;
grid-template-columns:minmax(0,1fr) 100px 170px;gap:0 14px;padding:20px;border-top:1px solid var(--line)}
td{border:0;padding:6px 0}.vm-cell{grid-column:1/3;grid-row:1}td:nth-child(2){grid-column:1;grid-row:2}
td:nth-child(3){grid-column:2;grid-row:2}td:nth-child(4){grid-column:1;grid-row:3}td:nth-child(5){grid-column:2;grid-row:3}
td:nth-child(6){grid-column:1/3;grid-row:4}td:nth-child(7){grid-column:3;grid-row:1/5;align-self:center}
tr:hover td{background:none}tr:hover{background:#17233580}.detail{padding-top:12px}.screen{max-width:170px}.screen img{height:106px}}
@media(max-width:560px){tr{grid-template-columns:minmax(0,1fr) 90px;gap:0 12px}td:nth-child(7){grid-column:1/3;grid-row:5;
margin-top:14px}.screen{max-width:100%}.screen img{height:150px}.flow{white-space:normal}.phase{white-space:normal}}
@media(max-width:800px){main{padding:26px 18px}.hero{align-items:flex-start;flex-direction:column}h1{font-size:28px}.metrics{grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.metric{padding:16px}.metric strong{font-size:28px}.metadata{gap:12px;flex-direction:column}.section-heading{padding:16px}.section-heading span{max-width:170px;text-align:right}
.footer{flex-direction:column;gap:6px}.lightbox{padding:12px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}.screen{transition:none}}
"""
    script = """
const search = document.getElementById('vm-filter');
const status = document.getElementById('status-filter');
const reset = document.getElementById('reset-filters');
const counter = document.getElementById('filter-count');
const empty = document.getElementById('empty-state');
const rows = Array.from(document.querySelectorAll('tr[data-vm]'));
function applyFilters() {
  const query = search.value.trim().toLocaleLowerCase();
  let shown = 0;
  for (const row of rows) {
    const matches = row.dataset.vm.toLocaleLowerCase().includes(query)
      && (!status.value || row.dataset.status === status.value);
    row.hidden = !matches;
    if (matches) shown += 1;
  }
  counter.textContent = `Showing ${shown} of ${rows.length} VMs`;
  empty.hidden = shown !== 0;
  reset.disabled = !search.value && !status.value;
  for (const card of document.querySelectorAll('.metric[data-status]')) {
    card.classList.toggle('active', card.dataset.status === status.value);
  }
}
function setStatus(value) {
  // a card or a badge filters on its status; clicking the active one again shows every result
  status.value = status.value === value ? '' : value;
  applyFilters();
  document.getElementById('results').scrollIntoView({ block: 'nearest' });
}
for (const trigger of document.querySelectorAll('.metric[data-status], .badge[data-status]')) {
  trigger.addEventListener('click', () => setStatus(trigger.dataset.status));
}
// The screenshot dialog is a :target section and the sections live at the end of the
// document, so a bare hash navigation scrolls the table away under the overlay: both
// opening and closing keep the reader where they were.
function goToHash(hash, event) {
  if (event) event.preventDefault();
  const y = window.scrollY;
  window.location.hash = hash;
  window.scrollTo({ top: y, behavior: 'instant' });
}
function closeLightbox(event) {
  if (!document.querySelector('.lightbox:target')) return;
  goToHash('results', event);
}
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeLightbox(event);
});
for (const opener of document.querySelectorAll('a.screen[href^="#screen-"]')) {
  opener.addEventListener('click', (event) => goToHash(opener.getAttribute('href').slice(1), event));
}
for (const closer of document.querySelectorAll('.lightbox-backdrop, .lightbox-content header a')) {
  closer.addEventListener('click', closeLightbox);
}
search.addEventListener('input', applyFilters);
status.addEventListener('change', applyFilters);
reset.addEventListener('click', () => {
  search.value = '';
  status.value = '';
  applyFilters();
  search.focus();
});
applyFilters();
"""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>VM validation report</title><style>{css}</style></head><body><main>'
            '<header class="hero"><div><div class="eyebrow"><span class="logo">›_</span> QEMU ISO LAB / VALIDATION</div>'
            '<h1>VM validation report</h1><p class="subtitle">Boot checks, provisioning results and final guest screens.</p></div>'
            f'<div class="run-state {overall}">● &nbsp;{headline}</div></header>'
            f'<div class="metadata">{"".join(meta_items)}</div><div class="metrics">{cards}</div>'
            '<section class="results" id="results"><div class="section-heading"><h2>Test results</h2>'
            f'<span>Total: {len(results)} · Click a screen to inspect the guest, Esc closes it</span></div>'
            '<div class="filters" role="search" aria-label="Filter VM results">'
            '<label class="filter-search"><span aria-hidden="true">⌕</span><input id="vm-filter" type="search" autocomplete="off" '
            'aria-label="Filter by VM name or profile ID" placeholder="Filter by VM name or profile ID…"></label>'
            '<label class="filter-status">Result <select id="status-filter"><option value="">All results</option>'
            '<option value="PASS">PASS</option><option value="WARN">WARN</option><option value="FAIL">FAIL</option><option value="SKIP">SKIP</option></select></label>'
            '<button class="reset-filters" id="reset-filters" type="button" disabled>Clear filters</button>'
            f'<span class="filter-count" id="filter-count" aria-live="polite">Showing {len(results)} of {len(results)} VMs</span></div><div class="table-scroll">'
            '<table><thead><tr><th>VM / Profile ID</th><th>Flow</th><th>Result</th><th>Phase</th><th>Duration</th><th>Detail</th><th>Screenshot</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div><p class="empty-state" id="empty-state" hidden>No VMs match these filters. Try another name or clear the filters.</p></section><footer class="footer"><span>Generated by <strong>vmctl check-vms</strong></span>'
            '<span>Self-contained report · Screenshots embedded · No external resources</span></footer></main>'
            f'{"".join(lightboxes)}<script type="module">{script}</script></body></html>\n')


def finish(directory: Path, args: argparse.Namespace, results: list[tuple[str, str, str]], cfg: dict[str, Any]) -> None:
    destination = directory / "report.html"
    if args.dry_run:
        ui.print_note(f"Would write report: {destination}")
    else:
        collected = []
        for name, status, detail in results:
            path = directory / "results" / f"{name}.json"
            if path.exists():
                collected.append(json.loads(path.read_text()))
            else:
                # A worker may fail before it can write its result.
                fallback = {"id": name, "name": cfg["vms"][name].get("name", name), "flow": "unknown",
                            "status": "FAIL" if status == "failed" else "SKIP", "phase": "worker", "seconds": 0, "detail": detail}
                path.write_text(json.dumps(fallback, indent=2) + "\n")
                collected.append(fallback)
        try:
            commit = runtime.run_output(["git", "-C", str(state.ROOT), "rev-parse", "HEAD"]).strip()
        except (OSError, subprocess.CalledProcessError):
            commit = "unknown"
        metadata = {"host": platform.node(), "date": datetime.now(timezone.utc).isoformat(), "commit": commit}
        destination.write_text(render_html(collected, metadata, directory), encoding="utf-8")
        ui.print_kv("report", str(destination))
    if getattr(args, "open", False):
        runtime.run(["xdg-open", str(destination)], dry_run=args.dry_run)
