import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.scheduler as scheduler  # noqa: E402


class SchedulerTests(unittest.TestCase):
    def test_parse_parallel(self):
        self.assertEqual(scheduler.parse_parallel("3"), 3)
        self.assertEqual(scheduler.parse_parallel(2), 2)
        self.assertIsNone(scheduler.parse_parallel("auto"))
        self.assertIsNone(scheduler.parse_parallel("AUTO"))
        for bad in ("0x", "-1", "two"):
            with self.assertRaises(ValueError):
                scheduler.parse_parallel(bad)

    def test_host_resources_reads_meminfo(self):
        meminfo = Path(self.enterContext(__import__("tempfile").TemporaryDirectory())) / "meminfo"
        meminfo.write_text("MemTotal:       31717732 kB\nMemFree:        10855868 kB\nMemAvailable:   21177144 kB\n")
        res = scheduler.host_resources(meminfo)
        self.assertEqual((res.mem_total_mb, res.mem_available_mb), (30974, 20680))
        self.assertGreaterEqual(res.cpus, 1)
        missing = scheduler.host_resources(meminfo.with_name("nope"))
        self.assertEqual((missing.mem_total_mb, missing.mem_available_mb), (0, 0))

    def test_vm_cost_adds_the_qemu_overhead(self):
        self.assertEqual(scheduler.vm_cost({"memory_mb": 1024, "cpus": 2}), scheduler.VmCost(1024 + scheduler.QEMU_OVERHEAD_MB, 2))
        self.assertEqual(scheduler.vm_cost({}).cpus, 1)

    def test_fits_respects_memory_and_cpu_budgets(self):
        sched = scheduler.DynamicScheduler(scheduler.HostResources(32000, 22000, 16), reserve_mb=2000)
        self.assertEqual(sched.mem_budget_mb, 20000)
        self.assertEqual(sched.cpu_budget, 32.0)
        old_ubuntu = scheduler.VmCost(1536, 2)
        windows = scheduler.VmCost(4608, 2)
        self.assertTrue(sched.fits([old_ubuntu] * 4, windows))          # 6144 + 4608 fits 20000
        self.assertTrue(sched.fits([windows] * 4, old_ubuntu))          # 18432 + 1536 = 19968 still fits
        self.assertFalse(sched.fits([windows] * 4 + [old_ubuntu], old_ubuntu))  # 19968 + 1536 > 20000
        self.assertFalse(sched.fits([scheduler.VmCost(512, 8)] * 4, scheduler.VmCost(512, 1)))  # 33 vCPU > 32

    def test_run_packs_small_vms_and_makes_a_big_one_wait(self):
        # 8 GB budget: four 1.5 GB guests run together, the 4.6 GB Windows waits for room.
        sched = scheduler.DynamicScheduler(scheduler.HostResources(16000, 10192, 8), reserve_mb=2000, max_workers=8)
        jobs = [(f"ubuntu-{v}", scheduler.VmCost(1536, 2)) for v in ("8.04", "10.04", "12.04", "14.04")] + [("windows11", scheduler.VmCost(4608, 2))]
        lock = threading.Lock()
        running: set[str] = set()
        timeline: list[tuple[str, frozenset[str]]] = []
        release = {name: threading.Event() for name, _ in jobs}

        def worker(name: str) -> str:
            with lock:
                running.add(name)
                timeline.append((name, frozenset(running)))
            release[name].wait(5)
            with lock:
                running.discard(name)
            return f"done {name}"

        starts: list[tuple[str, int]] = []
        results: list = []

        def run() -> None:
            results.extend(sched.run(jobs, worker, on_start=lambda n, c, k: starts.append((n, k))))

        thread = threading.Thread(target=run); thread.start()
        deadline = time.time() + 5
        while time.time() < deadline and len(starts) < 4:
            time.sleep(0.02)
        self.assertEqual([n for n, _ in starts], ["ubuntu-8.04", "ubuntu-10.04", "ubuntu-12.04", "ubuntu-14.04"])
        time.sleep(0.2)
        self.assertEqual(len(starts), 4, "windows11 must wait: 4 x 1536 + 4608 > 8192")
        release["ubuntu-8.04"].set()
        while time.time() < deadline and len(starts) < 5:
            time.sleep(0.02)
        self.assertEqual(starts[-1][0], "windows11")
        for name in ("ubuntu-10.04", "ubuntu-12.04", "ubuntu-14.04", "windows11"):
            release[name].set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(sorted(r for _, r in results), sorted(f"done {n}" for n, _ in jobs))
        self.assertEqual(sched.peak_running, 4)
        # windows11 never overlapped with all four small ones
        for name, active in timeline:
            if name == "windows11":
                self.assertNotIn("ubuntu-8.04", active)

    def test_run_never_stalls_on_an_oversized_job_and_reports_exceptions(self):
        sched = scheduler.DynamicScheduler(scheduler.HostResources(4000, 3000, 2), reserve_mb=2000)  # 1000 MB budget

        def worker(name: str) -> str:
            if name == "boom":
                raise RuntimeError("worker failed")
            return name

        results = sched.run([("huge", scheduler.VmCost(9000, 4)), ("boom", scheduler.VmCost(512, 1))], worker)
        outcomes = dict(results)
        self.assertEqual(outcomes["huge"], "huge")
        self.assertIsInstance(outcomes["boom"], RuntimeError)
        self.assertEqual(sched.peak_running, 1)


if __name__ == "__main__":
    unittest.main()
