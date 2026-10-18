"""What a running VM is doing right now, read from its QEMU process: CPU, memory, disk traffic.

The dashboard's activity meter (`GET /api/stats`): is an install still working, or has it been
sitting on a dialog for ten minutes? Nothing touches the guest. Everything comes from the host's
/proc for the QEMU process: `stat` (user + system CPU time, every vCPU thread included), `status`
(VmRSS: the guest RAM QEMU has actually touched, plus its own) and `io` (bytes the process made the
block layer read and write: the guest's disk, written through the page cache, shows up there).

CPU and disk are rates, so they need two readings: the `Sampler` keeps the previous one per PID and
answers with the difference. A VM is *idle* while its CPU stays under `IDLE_CPU_PERCENT` and its disk
under `IDLE_IO_BPS`; `idle_seconds` says for how long, which is the "is it stuck?" the meter shows.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IDLE_CPU_PERCENT = 3.0      # of the VM's own vCPUs: a guest waiting on a dialog stays below this
IDLE_IO_BPS = 64 * 1024     # read + write: background noise of an idle guest stays below this
MIN_INTERVAL_SEC = 0.5      # two readings closer than this give no meaningful rate: keep the old one


@dataclass(frozen=True)
class Reading:
    at: float          # time.monotonic() of the reading
    cpu_sec: float     # user + system time of the whole process
    rss_bytes: int
    read_bytes: int | None   # None when /proc/<pid>/io is not readable (another user's process)
    write_bytes: int | None


def _ticks_per_second() -> int:
    try:
        return int(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, ValueError, OSError):
        return 100


CLK_TCK = _ticks_per_second()


def read(pid: int, proc: Path = Path("/proc")) -> Reading | None:
    """One reading of the process, or None when it is gone."""
    base = proc / str(pid)
    try:
        stat = (base / "stat").read_text()
        status = (base / "status").read_text()
    except OSError:
        return None
    # The command name sits in parentheses and may hold spaces: the fields start after the last ')'.
    fields = stat[stat.rfind(")") + 2:].split()
    try:
        cpu_sec = (int(fields[11]) + int(fields[12])) / CLK_TCK   # utime, stime (fields 14, 15)
    except (IndexError, ValueError):
        return None
    rss = 0
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            rss = int(parts[1]) * 1024 if len(parts) > 1 and parts[1].isdigit() else 0
            break
    read_bytes = write_bytes = None
    try:
        io = dict(line.split(":", 1) for line in (base / "io").read_text().splitlines() if ":" in line)
        read_bytes, write_bytes = int(io["read_bytes"]), int(io["write_bytes"])
    except (OSError, KeyError, ValueError):
        pass
    return Reading(time.monotonic(), cpu_sec, rss, read_bytes, write_bytes)


class Sampler:
    """The previous reading per PID, so every answer can carry rates; shared by every request."""

    def __init__(self, proc: Path = Path("/proc")) -> None:
        self.proc = proc
        self.lock = threading.Lock()
        self.previous: dict[int, Reading] = {}
        self.rates: dict[int, dict[str, float | None]] = {}
        self.busy_at: dict[int, float] = {}

    def measure(self, pid: int, vcpus: int = 1, memory_mb: int = 0) -> dict[str, Any] | None:
        now = self.read(pid)
        if now is None:
            with self.lock:
                self.previous.pop(pid, None)
                self.rates.pop(pid, None)
                self.busy_at.pop(pid, None)
            return None
        with self.lock:
            before = self.previous.get(pid)
            if before is None or now.at - before.at >= MIN_INTERVAL_SEC:
                if before is not None:
                    span = now.at - before.at
                    self.rates[pid] = {
                        "cpu": max(0.0, now.cpu_sec - before.cpu_sec) / span * 100 / max(1, vcpus),
                        "read": _rate(before.read_bytes, now.read_bytes, span),
                        "write": _rate(before.write_bytes, now.write_bytes, span),
                    }
                self.previous[pid] = now
            rates = self.rates.get(pid)
            if rates is None:
                self.busy_at.setdefault(pid, now.at)
                idle_seconds = 0.0
            else:
                io = (rates["read"] or 0.0) + (rates["write"] or 0.0)
                if rates["cpu"] is None or rates["cpu"] >= IDLE_CPU_PERCENT or io >= IDLE_IO_BPS:
                    self.busy_at[pid] = now.at
                idle_seconds = now.at - self.busy_at.setdefault(pid, now.at)
        return {
            "pid": pid,
            "cpu_percent": None if rates is None else round(min(100.0, rates["cpu"] or 0.0), 1),
            "rss_bytes": now.rss_bytes,
            "memory_bytes": memory_mb * 1024 * 1024 if memory_mb else None,
            "read_bps": None if rates is None or rates["read"] is None else round(rates["read"]),
            "write_bps": None if rates is None or rates["write"] is None else round(rates["write"]),
            "idle_seconds": round(idle_seconds),
        }

    def read(self, pid: int) -> Reading | None:
        return read(pid, self.proc)

    def forget_except(self, pids: set[int]) -> None:
        """Drop the readings of processes no longer asked about (VMs that stopped)."""
        with self.lock:
            for table in (self.previous, self.rates, self.busy_at):
                for pid in [p for p in table if p not in pids]:
                    del table[pid]


def _rate(before: int | None, now: int | None, span: float) -> float | None:
    if before is None or now is None:
        return None
    return max(0, now - before) / span


def pid_of(row: dict[str, Any]) -> int | None:
    """The QEMU PID of a dashboard row: `tracked:<pid>` / `running:<pid>` in its `runtime` fact, or the
    `pid=<pid>` note of `hostfwd:<port>` (lifecycle.vm_runtime_status)."""
    kind, _, value = str(row.get("runtime") or "").partition(":")
    if kind == "hostfwd":
        value = str(row.get("runtime_note") or "").removeprefix("pid=")
    elif kind not in {"tracked", "running"}:
        return None
    value = value.strip()
    return int(value) if value.isdigit() else None


def for_rows(sampler: Sampler, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Activity of every running row of the dashboard snapshot, by VM name."""
    out: dict[str, dict[str, Any]] = {}
    asked: set[int] = set()
    for row in rows:
        pid = pid_of(row) if row.get("running") else None
        if pid is None:
            continue
        asked.add(pid)
        try:
            vcpus = int(row.get("cpus") or 1)
            memory_mb = int(row.get("memory_mb") or 0)
        except (TypeError, ValueError):
            vcpus, memory_mb = 1, 0
        stats = sampler.measure(pid, vcpus, memory_mb)
        if stats is not None:
            out[str(row["name"])] = stats
    sampler.forget_except(asked)
    return out
