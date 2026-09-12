"""Resource-aware scheduling for the validation matrix (``check-vms --parallel auto``).

A fixed ``--parallel N`` treats a 1 GB Ubuntu 8.04 and a 4 GB Windows 11 alike. The dynamic
scheduler instead gives every VM a cost (guest RAM plus the QEMU overhead, vCPUs) and starts
the next pending VM whenever the host budget still has room for it: four old Ubuntus run
together, a Windows waits until enough memory is back. The budget is the memory available
when the run starts minus a reserve for the host, and the CPUs with a mild oversubscription
(installers sleep on I/O far more than they compute).
"""
from __future__ import annotations

import concurrent.futures
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from vmctl import ui

T = TypeVar("T")

# What QEMU itself (page tables, the framebuffer, virtiofsd, the installer's ISO cache) adds on
# top of the guest's RAM, and what stays untouched for the host and the disk caches.
QEMU_OVERHEAD_MB = 512
HOST_RESERVE_MB = 2048
CPU_OVERSUBSCRIPTION = 2.0
DEFAULT_MAX_WORKERS = 8


@dataclass(frozen=True)
class HostResources:
    mem_total_mb: int
    mem_available_mb: int
    cpus: int


@dataclass(frozen=True)
class VmCost:
    mem_mb: int
    cpus: int


def host_resources(meminfo: Path = Path("/proc/meminfo")) -> HostResources:
    """RAM and CPUs of this host from /proc/meminfo and the scheduler's CPU count."""
    values: dict[str, int] = {}
    try:
        for line in meminfo.read_text(encoding="ascii", errors="replace").splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                values[key.strip()] = int(parts[0]) // 1024  # kB -> MB
    except OSError:
        pass
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", total)
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    return HostResources(mem_total_mb=total, mem_available_mb=available, cpus=max(1, cpus))


def vm_cost(vm: dict[str, Any]) -> VmCost:
    return VmCost(mem_mb=int(vm.get("memory_mb", 1024)) + QEMU_OVERHEAD_MB, cpus=max(1, int(vm.get("cpus", 1))))


def parse_parallel(value: Any) -> int | None:
    """``--parallel``: a positive count, or ``auto`` (None) for the dynamic scheduler."""
    text = str(value).strip().lower()
    if text in ("auto", "dynamic", "0"):
        return None
    try:
        count = int(text)
    except ValueError as exc:
        raise ValueError(f"--parallel expects a number or 'auto', not {value!r}") from exc
    if count < 1:
        raise ValueError("--parallel must be at least 1 (or 'auto')")
    return count


class DynamicScheduler:
    """Start pending jobs as long as their summed cost fits the host budget."""

    def __init__(self, resources: HostResources, *, reserve_mb: int = HOST_RESERVE_MB,
                 cpu_oversubscription: float = CPU_OVERSUBSCRIPTION, max_workers: int = DEFAULT_MAX_WORKERS) -> None:
        self.resources = resources
        self.mem_budget_mb = max(0, resources.mem_available_mb - reserve_mb)
        self.cpu_budget = max(1.0, resources.cpus * cpu_oversubscription)
        self.max_workers = max(1, max_workers)
        self.peak_running = 0

    def describe(self) -> list[str]:
        return [
            f"host: {self.resources.mem_total_mb} MB RAM, {self.resources.mem_available_mb} MB available, {self.resources.cpus} CPUs",
            f"budget: {self.mem_budget_mb} MB for guests (+{QEMU_OVERHEAD_MB} MB QEMU overhead each), "
            f"{self.cpu_budget:g} vCPUs ({CPU_OVERSUBSCRIPTION:g}x oversubscription), at most {self.max_workers} VMs at once",
        ]

    def fits(self, running: Iterable[VmCost], candidate: VmCost) -> bool:
        used_mem = sum(cost.mem_mb for cost in running)
        used_cpu = sum(cost.cpus for cost in running)
        return used_mem + candidate.mem_mb <= self.mem_budget_mb and used_cpu + candidate.cpus <= self.cpu_budget

    def run(self, jobs: list[tuple[str, VmCost]], worker: Callable[[str], T],
            on_start: Callable[[str, VmCost, int], None] | None = None) -> list[tuple[str, T | BaseException]]:
        """Run ``worker(name)`` for every job, in the given order of preference, never over budget.

        Returns ``(name, result-or-exception)`` in completion order. A job that does not fit even
        an idle host still runs, alone, so the matrix never stalls on an oversized profile.
        """
        pending = list(jobs)
        running: dict[concurrent.futures.Future[T], tuple[str, VmCost]] = {}
        results: list[tuple[str, T | BaseException]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while pending or running:
                started = False
                if len(running) < self.max_workers:
                    active = [cost for _, cost in running.values()]
                    for index, (name, cost) in enumerate(pending):
                        alone = not running and index == 0
                        if self.fits(active, cost) or alone:
                            future = executor.submit(worker, name)
                            running[future] = (name, cost)
                            pending.pop(index)
                            self.peak_running = max(self.peak_running, len(running))
                            if on_start is not None:
                                on_start(name, cost, len(running))
                            started = True
                            break
                if started:
                    continue
                done, _ = concurrent.futures.wait(list(running), return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    name, _ = running.pop(future)
                    try:
                        results.append((name, future.result()))
                    except BaseException as exc:  # the caller decides how a failed worker is reported
                        results.append((name, exc))
        return results


def print_plan(scheduler: DynamicScheduler, jobs: list[tuple[str, VmCost]]) -> None:
    for line in scheduler.describe():
        ui.print_note(line)
    for name, cost in jobs:
        ui.print_kv(name, f"{cost.mem_mb} MB, {cost.cpus} vCPU")
