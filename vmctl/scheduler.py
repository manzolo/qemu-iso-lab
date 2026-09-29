"""Resource-aware scheduling for the validation matrix (``check-vms --parallel auto``).

A fixed ``--parallel N`` treats a 1 GB Ubuntu 8.04 and a 4 GB Windows 11 alike. The dynamic
scheduler instead gives every VM a cost (guest RAM plus the QEMU overhead, vCPUs) and starts
the next pending VM whenever the host budget still has room for it: four old Ubuntus run
together, a Windows waits until enough memory is back. The memory budget follows live
availability, reserving unresident RAM during startup while keeping the initial static cap.
CPUs allow mild oversubscription (installers sleep on I/O far more than they compute).
Both modes serialize jobs sharing host TCP ports; fixed parallelism disables only the
RAM and CPU budgets.
"""
from __future__ import annotations

import concurrent.futures
import os
import shutil
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from vmctl import qemu, state, ui

T = TypeVar("T")

# What QEMU itself (page tables, the framebuffer, virtiofsd, the installer's ISO cache) adds on
# top of the guest's RAM, and what stays untouched for the host and the disk caches.
QEMU_OVERHEAD_MB = 512
HOST_RESERVE_MB = 2048
DISK_RESERVE_GB = 30  # never let the rows take the filesystem below this
CPU_OVERSUBSCRIPTION = 2.0
DEFAULT_MAX_WORKERS = 8
# Give installers five minutes to touch their guest RAM. Charging older guests against
# MemAvailable again would suppress useful concurrency once that RAM is resident.
# This is a startup estimate; the static cap still covers every guest after it expires.
GRACE_SEC = 300


@dataclass(frozen=True)
class HostResources:
    mem_total_mb: int
    mem_available_mb: int
    cpus: int


@dataclass(frozen=True)
class VmCost:
    mem_mb: int
    cpus: int
    # Host TCP ports the VM forwards. Unlike RAM and CPUs these are not a budget but a
    # traffic light: QEMU refuses to start on a port another guest already holds, so two
    # VMs sharing one can only run one at a time, however much memory is free.
    ports: frozenset[int] = frozenset()
    # The disk the row may fill (virtual size of the disk and its extra disks, GB): admitted only
    # while the artifacts filesystem has room for it plus the running rows' disks and a reserve.
    # The matrix of 2026-09-29 ran out of space after 96 minutes and wrote 85 empty results.
    disk_gb: int = 0


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


def size_gb(text: Any) -> int:
    """A qemu-img size (``32G``, ``512M``, ``1T``, bytes) in whole GB, rounded up; 0 if unreadable."""
    value = str(text or "").strip().upper()
    factor = {"K": 1 / 1024 ** 2, "M": 1 / 1024, "G": 1.0, "T": 1024.0}.get(value[-1:] if value else "", None)
    try:
        number = float(value[:-1]) * factor if factor is not None else float(value) / 1024 ** 3
    except ValueError:
        return 0
    return max(0, int(-(-number // 1)))


def vm_cost(vm: dict[str, Any]) -> VmCost:
    disks = [vm.get("disk") or {}, *(qemu.extra_disks(vm))]
    return VmCost(mem_mb=int(vm.get("memory_mb", 1024)) + QEMU_OVERHEAD_MB, cpus=max(1, int(vm.get("cpus", 1))),
                  ports=qemu.host_ports(vm), disk_gb=sum(size_gb(disk.get("size")) for disk in disks))


def free_disk_gb(path: Path | None = None) -> int:
    """Free space where the rows write their disks (the checkout's artifacts/)."""
    target = path or state.ROOT / "artifacts"
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        return int(shutil.disk_usage(target).free // 1024 ** 3)
    except OSError:
        return 0


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
                 cpu_oversubscription: float = CPU_OVERSUBSCRIPTION, max_workers: int = DEFAULT_MAX_WORKERS,
                 memory_source: Callable[[], int] | None = None,
                 time_source: Callable[[], float] | None = None,
                 resource_budgets: bool = True,
                 disk_source: Callable[[], int] | None = None, disk_reserve_gb: int = DISK_RESERVE_GB) -> None:
        self.resources = resources
        self.resource_budgets = resource_budgets
        self.reserve_mb = reserve_mb
        self.mem_available_mb = resources.mem_available_mb
        self.initial_mem_budget_mb = max(0, resources.mem_available_mb - reserve_mb)
        self.mem_budget_mb = self.initial_mem_budget_mb
        self._memory_source = memory_source if memory_source is not None else lambda: host_resources().mem_available_mb
        self._time_source = time_source if time_source is not None else time.monotonic
        self._recent_mem_mb = 0
        self.cpu_budget = max(1.0, resources.cpus * cpu_oversubscription)
        self.max_workers = max(1, max_workers)
        self.peak_running = 0
        self._disk_source = disk_source if disk_source is not None else free_disk_gb
        self.disk_reserve_gb = disk_reserve_gb
        self.disk_free_gb = 0

    def _refresh_memory_budget(self, running: list[tuple[VmCost, float]]) -> None:
        # The September 2026 matrix outlived its initial free-memory snapshot and was
        # killed under host pressure. Live RAM must constrain every admission, but QEMU
        # touches guest RAM lazily: reserve recent starts against live RAM until they
        # have had time to become resident. All guests still count against the static cap.
        self.mem_available_mb = self._memory_source()
        now = self._time_source()
        used_mem = sum(cost.mem_mb for cost, _ in running)
        self._recent_mem_mb = sum(cost.mem_mb for cost, started_at in running if now - started_at < GRACE_SEC)
        room = min(self.initial_mem_budget_mb - used_mem,
                   self.mem_available_mb - self.reserve_mb - self._recent_mem_mb)
        # fits() takes total running cost, so express admission room as a total budget.
        self.mem_budget_mb = max(0, used_mem + room)

    def describe(self) -> list[str]:
        if not self.resource_budgets:
            return [f"at most {self.max_workers} VMs at once; VMs sharing host TCP ports run one at a time"]
        return [
            f"host: {self.resources.mem_total_mb} MB RAM, {self.mem_available_mb} MB available, {self.resources.cpus} CPUs",
            f"budget: {self.mem_budget_mb} MB for guests (+{QEMU_OVERHEAD_MB} MB QEMU overhead each), "
            f"{self.cpu_budget:g} vCPUs ({CPU_OVERSUBSCRIPTION:g}x oversubscription), at most {self.max_workers} VMs at once",
            f"disk: a row starts only while free space minus {self.disk_reserve_gb} GB and the running rows' disks holds its own disk",
            f"memory room: min({self.initial_mem_budget_mb} MB initial cap - all running costs, "
            f"live available - {self.reserve_mb} MB reserve - costs of VMs younger than {GRACE_SEC}s); "
            "refreshed before each pending scan",
        ]

    def fits(self, running: Iterable[VmCost], candidate: VmCost) -> bool:
        """Use the current decision snapshot without resampling between candidates."""
        active = list(running)
        if not self.ports_free(active, candidate):
            return False
        if not self.resource_budgets:
            return True
        used_mem = sum(cost.mem_mb for cost in active)
        used_cpu = sum(cost.cpus for cost in active)
        return (used_mem + candidate.mem_mb <= self.mem_budget_mb and used_cpu + candidate.cpus <= self.cpu_budget
                and self.disk_fits(active, candidate))

    def disk_fits(self, running: Iterable[VmCost], candidate: VmCost) -> bool:
        """Room for *candidate*'s disk: live free space minus the reserve and the running rows'
        full disks (what they have written already is counted twice: on purpose, it errs safe)."""
        if not candidate.disk_gb:
            return True
        return self.disk_free_gb - self.disk_reserve_gb - sum(cost.disk_gb for cost in running) >= candidate.disk_gb

    @staticmethod
    def ports_free(running: Iterable[VmCost], candidate: VmCost) -> bool:
        """No host port of *candidate* is held by a running VM."""
        if not candidate.ports:
            return True
        return all(candidate.ports.isdisjoint(cost.ports) for cost in running)

    def run(self, jobs: list[tuple[str, VmCost]], worker: Callable[[str], T],
            on_start: Callable[[str, VmCost, int], None] | None = None) -> list[tuple[str, T | BaseException]]:
        """Run ``worker(name)`` for every job, in the given order of preference, never over budget.

        Returns ``(name, result-or-exception)`` in completion order. A job that does not fit even
        an idle host still runs, alone, so the matrix never stalls on an oversized profile.
        If nothing fits, the next completion triggers another decision and memory sample.
        """
        pending = list(jobs)
        running: dict[concurrent.futures.Future[T], tuple[str, VmCost, float]] = {}
        results: list[tuple[str, T | BaseException]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while pending or running:
                started = False
                if pending and len(running) < self.max_workers:
                    # All candidates in this scan share one snapshot, so a later candidate
                    # cannot win merely because MemAvailable oscillates between reads.
                    if self.resource_budgets:
                        self._refresh_memory_budget([(cost, started_at) for _, cost, started_at in running.values()])
                        self.disk_free_gb = self._disk_source()
                    active = [cost for _, cost, _ in running.values()]
                    used_mem = sum(cost.mem_mb for cost in active)
                    used_cpu = sum(cost.cpus for cost in active)
                    for index, (name, cost) in enumerate(pending):
                        alone = not running and index == 0
                        fits = self.fits(active, cost)
                        if fits or alone:
                            if not fits:
                                ui.print_note(f"starting {name} alone despite budget: requested {cost.mem_mb} MB, {cost.cpus} vCPU; "
                                              f"live available {self.mem_available_mb} MB, budget {self.mem_budget_mb} MB, "
                                              f"CPU budget {self.cpu_budget:g} vCPU")
                            started_at = self._time_source()
                            future = executor.submit(worker, name)
                            running[future] = (name, cost, started_at)
                            pending.pop(index)
                            self.peak_running = max(self.peak_running, len(running))
                            if on_start is not None:
                                on_start(name, cost, len(running))
                            started = True
                            break
                        if index != 0:
                            continue
                        reasons = []
                        busy_ports = sorted(cost.ports.intersection(port for other in active for port in other.ports))
                        if busy_ports:
                            reasons.append(f"host TCP ports in use: {', '.join(map(str, busy_ports))}")
                        if self.resource_budgets and used_mem + cost.mem_mb > self.mem_budget_mb:
                            reasons.append(f"memory: live available {self.mem_available_mb} MB, reserve {self.reserve_mb} MB, "
                                           f"initial cap {self.initial_mem_budget_mb} MB, budget {self.mem_budget_mb} MB, "
                                           f"committed {used_mem} MB ({self._recent_mem_mb} MB younger than {GRACE_SEC}s), "
                                           f"room {max(0, self.mem_budget_mb - used_mem)} MB")
                        if self.resource_budgets and used_cpu + cost.cpus > self.cpu_budget:
                            reasons.append(f"CPU: committed {used_cpu} vCPU, budget {self.cpu_budget:g} vCPU")
                        if self.resource_budgets and not self.disk_fits(active, cost):
                            reasons.append(f"disk: {self.disk_free_gb} GB free, reserve {self.disk_reserve_gb} GB, "
                                           f"running rows' disks {sum(c.disk_gb for c in active)} GB, this one {cost.disk_gb} GB")
                        ui.print_note(f"waiting {name} (requested {cost.mem_mb} MB, {cost.cpus} vCPU): {'; '.join(reasons)}")
                if started:
                    continue
                done, _ = concurrent.futures.wait(list(running), return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    name, _, _ = running.pop(future)
                    try:
                        results.append((name, future.result()))
                    except BaseException as exc:  # the caller decides how a failed worker is reported
                        results.append((name, exc))
        return results


def print_plan(scheduler: DynamicScheduler, jobs: list[tuple[str, VmCost]]) -> None:
    for line in scheduler.describe():
        ui.print_note(line)
    for name, cost in jobs:
        ui.print_kv(name, f"{cost.mem_mb} MB, {cost.cpus} vCPU, {cost.disk_gb} GB disk")
