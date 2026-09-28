"""Bounded file exchange over the guest's SFTP subsystem, using existing SSH keys.

SFTP v3 packets keep filenames out of shell commands and work without Python or
shell utilities in the guest. Protocol: openssh.org/txt/draft-ietf-secsh-filexfer-02.txt.
"""
from __future__ import annotations

import contextlib
import os
import posixpath
import secrets
import select
import stat
import struct
import subprocess
import tempfile
import time
from collections.abc import Iterator
from types import TracebackType
from typing import Any, BinaryIO, TypedDict

from vmctl import ssh
from vmctl.errors import VMError

MAX_FILE_SIZE = 256 * 1024 * 1024
MAX_ENTRIES = 2000
CHUNK_SIZE = 32768
MAX_PACKET = 1024 * 1024


class FileEntry(TypedDict):
    name: str
    kind: str
    size: int


def uint(value: int) -> bytes:
    return struct.pack(">I", value)


def string(value: str | bytes) -> bytes:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return uint(len(data)) + data


def validate_path(path: str) -> str:
    if not isinstance(path, str) or not path or len(path.encode("utf-8")) > 4096 or "\0" in path:
        raise VMError("Invalid guest path")
    return path


def validate_name(name: str) -> str:
    validate_path(name)
    if name in (".", "..") or any(c in name for c in "/\\\r\n") or len(name.encode("utf-8")) > 240:
        raise VMError("Choose a filename without directory separators (at most 240 bytes)")
    return name


class Packet:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.data):
            raise VMError("Truncated SFTP response")
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def number(self) -> int:
        return int.from_bytes(self.take(4), "big")

    def string(self) -> bytes:
        return self.take(self.number())

    def text(self) -> str:
        return self.string().decode("utf-8", errors="strict")

    def attrs(self) -> dict[str, int]:
        flags = self.number()
        attrs: dict[str, int] = {}
        if flags & 1:
            attrs["size"] = struct.unpack(">Q", self.take(8))[0]
        if flags & 2:
            self.take(8)  # uid, gid
        if flags & 4:
            attrs["mode"] = self.number()
        if flags & 8:
            self.take(8)  # atime, mtime
        if flags & 0x80000000:
            for _ in range(self.number()):
                self.string()
                self.string()
        return attrs


class SFTPError(VMError):
    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(message or f"SFTP error {code}")


class SFTP:
    def __init__(self, vm: dict[str, Any], timeout: float = 180):
        base = ssh.ssh_base_cmd(vm)
        self.command = base[:-1] + ["-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15",
                                   "-o", "ServerAliveCountMax=2", "-s", base[-1], "sftp"]
        self.timeout = timeout
        self.sequence = 0
        self.process: subprocess.Popen[bytes] | None = None
        self.errors: BinaryIO | None = None

    def __enter__(self) -> SFTP:
        self.deadline = time.monotonic() + self.timeout
        self.errors = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=self.errors, bufsize=0)
            assert self.process.stdin is not None
            assert self.process.stdout is not None
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
            self._send(b"\x01" + uint(3))
            packet = Packet(self._receive())
            if packet.take(1) != b"\x02" or packet.number() != 3:
                raise VMError("The guest does not support SFTP version 3")
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
                 traceback: TracebackType | None) -> None:
        if self.process is not None:
            if self.process.stdin is not None:
                self.process.stdin.close()
            if self.process.stdout is not None:
                self.process.stdout.close()
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
        if self.errors is not None:
            self.errors.close()

    def _wait(self, fd: int, writing: bool = False) -> None:
        remaining = min(15, self.deadline - time.monotonic())
        if remaining <= 0:
            raise VMError("File transfer timed out")
        ready = select.select([] if writing else [fd], [fd] if writing else [], [], remaining)
        if not (ready[0] or ready[1]):
            raise VMError("SFTP did not respond. Check SSH access to the guest.")

    def _send(self, data: bytes) -> None:
        assert self.process is not None and self.process.stdin is not None
        pending = memoryview(uint(len(data)) + data)
        fd = self.process.stdin.fileno()
        while pending:
            self._wait(fd, writing=True)
            try:
                count = os.write(fd, pending[:CHUNK_SIZE])
            except BlockingIOError:
                continue
            except BrokenPipeError as exc:
                raise self._connection_error() from exc
            pending = pending[count:]

    def _read(self, size: int) -> bytes:
        assert self.process is not None and self.process.stdout is not None
        result = bytearray()
        fd = self.process.stdout.fileno()
        while len(result) < size:
            self._wait(fd)
            try:
                data = os.read(fd, size - len(result))
            except BlockingIOError:
                continue
            if not data:
                raise self._connection_error()
            result.extend(data)
        return bytes(result)

    def _connection_error(self) -> VMError:
        assert self.errors is not None
        self.errors.seek(0, os.SEEK_END)
        self.errors.seek(max(0, self.errors.tell() - 1500))
        detail = self.errors.read().decode("utf-8", errors="replace").strip()
        return VMError("File exchange needs SFTP and SSH key access to the guest." + (f" {detail}" if detail else ""))

    def _receive(self) -> bytes:
        size = struct.unpack(">I", self._read(4))[0]
        if not 5 <= size <= MAX_PACKET:
            raise VMError("Invalid SFTP packet length")
        return self._read(size)

    def request(self, kind: int, data: bytes = b"", expected: int = 101) -> Packet:
        self.sequence += 1
        self._send(bytes([kind]) + uint(self.sequence) + data)
        reply = Packet(self._receive())
        response = reply.take(1)[0]
        if reply.number() != self.sequence:
            raise VMError("Unexpected SFTP request identifier")
        if response == 101:
            code = reply.number()
            message = reply.text()
            if code:
                raise SFTPError(code, message)
        if response != expected:
            raise VMError("Unexpected SFTP response")
        return reply

    def canonical(self, path: str) -> str:
        reply = self.request(16, string(validate_path(path)), 104)
        if reply.number() != 1:
            raise VMError("Invalid SFTP canonical path")
        return validate_path(reply.text())

    def attributes(self, path: str) -> dict[str, int]:
        return self.request(7, string(validate_path(path)), 105).attrs()

    @contextlib.contextmanager
    def handle(self, kind: int, data: bytes) -> Iterator[bytes]:
        handle = self.request(kind, data, 102).string()
        try:
            yield handle
        finally:
            self.request(4, string(handle))

    def listing(self, path: str) -> dict[str, Any]:
        home = self.canonical(".")
        directory = self.canonical(path)
        entries: list[FileEntry] = []
        truncated = False
        with self.handle(11, string(directory)) as handle:
            while True:
                try:
                    reply = self.request(12, string(handle), 104)
                except SFTPError as exc:
                    if exc.code == 1:  # EOF
                        break
                    raise
                count = reply.number()
                if not count:
                    break
                for _ in range(count):
                    name = reply.text()
                    reply.string()  # server-formatted longname is never parsed
                    attrs = reply.attrs()
                    if name in (".", "..") or "/" in name or "\0" in name:
                        continue
                    if len(entries) >= MAX_ENTRIES:
                        truncated = True
                        break
                    mode = attrs.get("mode", 0)
                    kind = "directory" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "other"
                    entries.append({"name": name, "kind": kind, "size": attrs.get("size", 0)})
                if truncated:
                    break
        entries.sort(key=lambda entry: (entry["kind"] != "directory", entry["name"].casefold()))
        return {"path": directory, "home": home, "parent": posixpath.dirname(directory.rstrip("/")) or "/",
                "entries": entries, "truncated": truncated, "max_file_size": MAX_FILE_SIZE}

    def download(self, path: str, target: BinaryIO) -> int:
        validate_path(path)
        attrs = self.attributes(path)
        if not stat.S_ISREG(attrs.get("mode", 0)):
            raise VMError("Choose a regular file to download")
        if attrs.get("size", 0) > MAX_FILE_SIZE:
            raise VMError("Files must be no larger than 256 MiB")
        offset = 0
        with self.handle(3, string(path) + uint(1) + uint(0)) as handle:
            # Recheck the opened object as well as its directory entry.
            opened = self.request(8, string(handle), 105).attrs()
            if not stat.S_ISREG(opened.get("mode", 0)) or opened.get("size", 0) > MAX_FILE_SIZE:
                raise VMError("File changed or is too large to download")
            while True:
                try:
                    data = self.request(5, string(handle) + struct.pack(">Q", offset) + uint(CHUNK_SIZE), 103).string()
                except SFTPError as exc:
                    if exc.code == 1:
                        break
                    raise
                if not data or len(data) > CHUNK_SIZE:
                    raise VMError("Invalid SFTP file data")
                offset += len(data)
                if offset > MAX_FILE_SIZE:
                    raise VMError("File grew beyond the 256 MiB transfer limit")
                target.write(data)
        return offset

    def upload(self, directory: str, name: str, source: BinaryIO, size: int) -> dict[str, Any]:
        validate_name(name)
        if not 0 <= size <= MAX_FILE_SIZE:
            raise VMError("Files must be no larger than 256 MiB")
        directory = self.canonical(directory)
        staged = posixpath.join(directory, ".vmctl-upload-" + secrets.token_hex(12) + ".part")
        created = False
        try:
            # WRITE|CREAT|EXCL; private permissions, no existing file is truncated.
            with self.handle(3, string(staged) + uint(2 | 8 | 32) + uint(4) + uint(0o600)) as handle:
                created = True
                offset = 0
                while offset < size:
                    chunk = source.read(min(CHUNK_SIZE, size - offset))
                    if not chunk:
                        raise VMError("Upload was interrupted before the file was complete")
                    self.request(6, string(handle) + struct.pack(">Q", offset) + string(chunk))
                    offset += len(chunk)
            stem, extension = posixpath.splitext(name)
            for attempt in range(100):
                saved = name if attempt == 0 else f"{stem} ({attempt + 1}){extension}"
                path = posixpath.join(directory, saved)
                try:
                    self.attributes(path)
                    continue
                except SFTPError as exc:
                    if exc.code != 2:  # No such file
                        raise
                try:
                    # Baseline v3 RENAME refuses an existing destination, even if
                    # another upload raced the check. Do not use posix-rename.
                    self.request(18, string(staged) + string(path))
                    created = False
                    return {"name": saved, "path": path, "size": size}
                except SFTPError as rename_error:
                    try:
                        self.attributes(path)  # retry only if the destination now exists
                    except SFTPError as lookup_error:
                        if lookup_error.code == 2:
                            raise rename_error
                        raise
            raise VMError("Too many files with this name. Rename the local file and try again.")
        finally:
            if created:
                with contextlib.suppress(VMError, OSError):
                    self.request(13, string(staged))
