"""Interactive SSH in a PTY, relayed to an authenticated browser WebSocket."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import pty
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
from pathlib import Path
from typing import Any


def resize(fd: int, columns: int, rows: int) -> None:
    columns, rows = max(2, min(500, columns)), max(1, min(200, rows))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))


def bridge(handler: Any, command: list[str]) -> None:
    from vmctl.webui import ws_frame, ws_read_frame

    master, slave = pty.openpty()
    resize(master, 100, 30)
    try:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), *command],
                                   stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                                   env={**os.environ, "TERM": "xterm-256color"})
    except BaseException:
        os.close(master)
        raise
    finally:
        os.close(slave)
    lock = threading.Lock()
    stopped = threading.Event()

    def send(opcode: int, payload: bytes) -> None:
        with lock:
            handler.wfile.write(ws_frame(opcode, payload))
            handler.wfile.flush()

    def output() -> None:
        try:
            while not stopped.is_set():
                data = os.read(master, 32768)
                if not data:
                    break
                send(0x2, data)
        except OSError:
            pass
        finally:
            with contextlib.suppress(OSError):
                send(0x8, b"")
            with contextlib.suppress(OSError):
                handler.connection.shutdown(socket.SHUT_RD)

    pump = threading.Thread(target=output, daemon=True)
    pump.start()
    try:
        while True:
            frame = ws_read_frame(handler.rfile)
            if frame is None or frame[0] == 0x8:
                break
            opcode, payload = frame
            if opcode == 0x9:
                send(0xA, payload)
            elif opcode == 0x1:
                message = json.loads(payload)
                if message.get("type") == "input" and isinstance(message.get("data"), str):
                    data = message["data"].encode()
                    while data:
                        data = data[os.write(master, data):]
                elif message.get("type") == "resize":
                    resize(master, int(message["cols"]), int(message["rows"]))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, struct.error):
        pass
    finally:
        stopped.set()
        # Closing a tab ends only its SSH client, never the VM or an unrelated job.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        with contextlib.suppress(OSError):
            handler.connection.shutdown(socket.SHUT_RDWR)
        pump.join(timeout=2)
        os.close(master)


if __name__ == "__main__":
    # Popen starts a session; claim its slave PTY so SSH can read password prompts from /dev/tty.
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
