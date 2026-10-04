"""Cancellable file copies via the host, with bounded temporary storage and progress."""
from __future__ import annotations

import posixpath
import secrets
import tempfile
import threading
import time
from typing import Any, BinaryIO, Protocol

from vmctl import config, web_files
from vmctl.errors import VMError

FINISHED = {"completed", "cancelled", "failed"}


class Readable(Protocol):
    """What receive() needs of a request body: the server's rfile is a BufferedIOBase, not a BinaryIO."""
    def read(self, size: int = ..., /) -> bytes: ...


class Cancelled(VMError):
    pass


class Transfer:
    def __init__(self, body: dict[str, Any], cfg: dict[str, Any]):
        self.id = secrets.token_hex(24)
        # The request body is untyped JSON: every field is narrowed here, once.
        vm = body.get("vm")
        if not isinstance(vm, str) or not vm:
            raise VMError("Choose a destination VM")
        self.vm: str = vm
        self.path = web_files.validate_path(body.get("path", "."))
        self.target = config.get_vm(cfg, self.vm)
        web_files.SFTP(self.target)  # validate SSH configuration before accepting files
        source_vm = body.get("source_vm")
        self.source_vm: str | None = None
        self.source_path = ""
        self.size: int = 0
        if source_vm is not None:
            if not isinstance(source_vm, str) or not source_vm or source_vm == self.vm:
                raise VMError("Choose a different source and destination VM")
            source_path = body.get("source_path")
            if not isinstance(source_path, str):
                raise VMError("Choose a file to copy")
            self.source_vm = source_vm
            self.source_path = web_files.validate_path(source_path)
            self.source = config.get_vm(cfg, self.source_vm)
            web_files.SFTP(self.source)
            self.name = web_files.validate_name(posixpath.basename(self.source_path))
            # the source guest determines the actual size
        else:
            name = body.get("name")
            if not isinstance(name, str):
                raise VMError("Choose a file to upload")
            self.name = web_files.validate_name(name)
            size = body.get("size")
            if type(size) is not int or not 0 <= size <= web_files.MAX_FILE_SIZE:
                raise VMError("Files must be no larger than 256 MiB")
            self.size = size
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.status = "queued" if self.source_vm else "waiting"
        self.bytes = 0
        self.error = ""
        self.result: dict[str, Any] | None = None
        self.updated = time.monotonic()
        self.thread: threading.Thread | None = None

    def info(self) -> dict[str, Any]:
        with self.lock:
            return {"id": self.id, "vm": self.vm, "path": self.path, "name": self.name,
                    "source_vm": self.source_vm, "status": self.status, "bytes": self.bytes,
                    "size": self.size, "error": self.error, "result": self.result}

    def progress(self, count: int = 0, phase: str | None = None) -> None:
        with self.lock:
            if self.cancelled.is_set():
                raise Cancelled("Transfer cancelled")
            if phase:
                self.status = phase
            self.bytes = count
            self.updated = time.monotonic()

    def cancel(self) -> dict[str, Any]:
        with self.lock:
            if self.status not in FINISHED:
                self.cancelled.set()
                self.status = "cancelled" if self.status == "waiting" else "cancelling"
                self.updated = time.monotonic()
        return self.info()

    def fail(self, exc: Exception) -> None:
        with self.lock:
            self.status = "cancelled" if self.cancelled.is_set() else "failed"
            self.error = "Transfer cancelled" if self.cancelled.is_set() else str(exc)
            self.updated = time.monotonic()

    def start(self, staged: BinaryIO | None = None) -> None:
        self.thread = threading.Thread(target=self._copy, args=(staged,), daemon=True)
        self.thread.start()

    def receive(self, source: Readable, size: int) -> None:
        with self.lock:
            if self.source_vm or self.status != "waiting":
                raise VMError("This transfer is not waiting for an upload")
            if size != self.size:
                raise VMError("Upload size does not match the queued file")
            self.status = "receiving"
        staged = None
        try:
            staged = tempfile.TemporaryFile()
            remaining = size
            deadline = time.monotonic() + 180
            self.progress(0)
            while remaining:
                if time.monotonic() >= deadline:
                    raise VMError("Upload timed out")
                chunk = source.read(min(65536, remaining))
                if not chunk:
                    raise VMError("Upload interrupted before the file was complete")
                staged.write(chunk)
                remaining -= len(chunk)
                self.progress(size - remaining)
            self.progress(0, "queued")
            self.start(staged)  # worker owns and closes the temporary file
        except Exception as exc:
            if staged is not None:
                staged.close()
            self.fail(exc)
            raise

    def _copy(self, staged: BinaryIO | None) -> None:
        try:
            with (staged if staged is not None else tempfile.TemporaryFile()) as data:
                self.progress(0, "connecting")
                if self.source_vm:
                    with web_files.SFTP(self.source) as source:
                        # SFTP.download rechecks type and size on the opened handle.
                        size = source.attributes(self.source_path).get("size", 0)
                        if size > web_files.MAX_FILE_SIZE:
                            raise VMError("Files must be no larger than 256 MiB")
                        with self.lock:
                            self.size = size
                        self.progress(0, "downloading")
                        size = source.download(self.source_path, data, self.progress)
                        with self.lock:
                            self.size = size
                self.progress(0, "connecting")
                data.seek(0)
                with web_files.SFTP(self.target) as target:
                    self.progress(0, "copying")
                    result = target.upload(self.path, self.name, data, self.size, self.progress)
                # A cancel racing the final rename cannot undo a completed copy.
                with self.lock:
                    self.result = result
                    self.status = "completed"
                    self.updated = time.monotonic()
        except Exception as exc:
            self.fail(exc)


class Transfers:
    def __init__(self, limit: int = 8):
        self.lock = threading.Lock()
        self.entries: dict[str, Transfer] = {}
        self.limit = limit
        self.closed = False

    def create(self, body: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
        transfer = Transfer(body, cfg)
        with self.lock:
            if self.closed:
                raise VMError("File transfers are shutting down")
            for entry in self.entries.values():
                with entry.lock:
                    if entry.status == "waiting" and time.monotonic() - entry.updated > 300:
                        entry.cancelled.set()
                        entry.status = "cancelled"
            if sum(entry.info()["status"] not in FINISHED for entry in self.entries.values()) >= self.limit:
                raise VMError("Too many file transfers. Wait for a transfer to finish.")
            for key in list(self.entries):
                if len(self.entries) < 100:
                    break
                if self.entries[key].info()["status"] in FINISHED:
                    del self.entries[key]
            self.entries[transfer.id] = transfer
            if transfer.source_vm:
                transfer.start()
        return transfer.info()

    def get(self, key: str) -> Transfer:
        with self.lock:
            if key not in self.entries:
                raise VMError("File transfer not found")
            return self.entries[key]

    def close_all(self) -> None:
        with self.lock:
            self.closed = True
            entries = list(self.entries.values())
        for entry in entries:
            entry.cancel()
        # SFTP waits are bounded; workers clean partial guest files before exiting.
        deadline = time.monotonic() + 20
        for entry in entries:
            if entry.thread:
                entry.thread.join(timeout=max(0, deadline - time.monotonic()))
