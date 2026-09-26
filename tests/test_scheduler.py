import concurrent.futures
import io
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.scheduler as scheduler  # noqa: E402
from _common import enter_context  # noqa: E402


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        # Every scheduler test supplies its own samples, regardless of host pressure.
        self.read_host_resources = scheduler.host_resources
        enter_context(self, mock.patch.object(scheduler, "host_resources", side_effect=AssertionError("unexpected host probe")))
        self.monotonic = enter_context(self, mock.patch.object(scheduler.time, "monotonic", return_value=0.0))

    def run_controlled(self, sched, jobs, completions, worker=lambda name: name):
        """Finish selected futures without threads or timing-dependent admission checks."""
        futures = {}
        starts = []
        checkpoints = iter(completions)

        def submit(fn, name):
            future = concurrent.futures.Future()
            futures[name] = (future, fn)
            return future

        def wait(active, *, return_when):
            expected_starts, name = next(checkpoints)
            self.assertEqual([entry[0] for entry in starts], expected_starts)
            self.assertEqual(return_when, concurrent.futures.FIRST_COMPLETED)
            future, fn = futures[name]
            self.assertIn(future, active)
            try:
                future.set_result(fn(name))
            except BaseException as exc:
                future.set_exception(exc)
            return {future}, set(active) - {future}

        def on_start(name, cost, count):
            starts.append((name, count, sched.mem_available_mb, sched.mem_budget_mb))

        with mock.patch.object(scheduler.concurrent.futures, "ThreadPoolExecutor") as executor, \
             mock.patch.object(scheduler.concurrent.futures, "wait", side_effect=wait), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            executor.return_value.__enter__.return_value.submit.side_effect = submit
            results = sched.run(jobs, worker, on_start=on_start)
        self.assertIsNone(next(checkpoints, None), "all completion checkpoints must be reached")
        return results, starts, stdout.getvalue()

    def test_parse_parallel(self):
        self.assertEqual(scheduler.parse_parallel("3"), 3)
        self.assertEqual(scheduler.parse_parallel(2), 2)
        self.assertIsNone(scheduler.parse_parallel("auto"))
        self.assertIsNone(scheduler.parse_parallel("AUTO"))
        for bad in ("0x", "-1", "two"):
            with self.assertRaises(ValueError):
                scheduler.parse_parallel(bad)

    def test_shared_ports_wait_while_unrelated_jobs_run_and_release_after_failure(self):
        for budgets in (True, False):
            for fail_router in (False, True):
                with self.subTest(resource_budgets=budgets, fail_router=fail_router):
                    resources = scheduler.HostResources(16000, 12000, 8) if budgets else scheduler.HostResources(0, 0, 1)
                    memory_source = mock.Mock(return_value=12000)
                    sched = scheduler.DynamicScheduler(resources, max_workers=3, resource_budgets=budgets,
                                                       memory_source=memory_source)
                    jobs = [("router", scheduler.VmCost(1000, 2, frozenset({2238, 2239}))),
                            ("dns", scheduler.VmCost(1000, 2, frozenset({2238}))),
                            ("desktop", scheduler.VmCost(1000, 2, frozenset({2239}))),
                            ("other", scheduler.VmCost(1000, 2))]

                    def worker(name):
                        if name == "router" and fail_router:
                            raise RuntimeError("installer failed")
                        return name

                    results, _, output = self.run_controlled(sched, jobs, [
                        (["router", "other"], "router"),
                        (["router", "other", "dns", "desktop"], "dns"),
                        (["router", "other", "dns", "desktop"], "desktop"),
                        (["router", "other", "dns", "desktop"], "other"),
                    ], worker=worker)
                    self.assertEqual(sched.peak_running, 3)
                    self.assertEqual([name for name, _ in results], ["router", "dns", "desktop", "other"])
                    self.assertEqual(isinstance(results[0][1], RuntimeError), fail_router)
                    self.assertIn("host TCP ports in use: 2238", output)
                    if not budgets:
                        memory_source.assert_not_called()
                        self.assertNotIn("memory:", output)
                        self.assertNotIn("CPU:", output)
                        self.assertNotIn("despite budget", output)

    def test_fixed_parallelism_still_limits_workers_without_resource_budgets(self):
        sched = scheduler.DynamicScheduler(scheduler.HostResources(0, 0, 1), max_workers=2,
                                           resource_budgets=False)
        jobs = [(name, scheduler.VmCost(9000, 8)) for name in ("a", "b", "c")]
        self.run_controlled(sched, jobs, [(["a", "b"], "a"),
                                          (["a", "b", "c"], "b"),
                                          (["a", "b", "c"], "c")])
        self.assertEqual(sched.peak_running, 2)

    def test_host_resources_reads_meminfo(self):
        meminfo = Path(enter_context(self, __import__("tempfile").TemporaryDirectory())) / "meminfo"
        meminfo.write_text("MemTotal:       31717732 kB\nMemFree:        10855868 kB\nMemAvailable:   21177144 kB\n")
        # Exercise the parser itself, while every scheduler below remains isolated.
        res = self.read_host_resources(meminfo)
        self.assertEqual((res.mem_total_mb, res.mem_available_mb), (30974, 20680))
        self.assertGreaterEqual(res.cpus, 1)
        missing = self.read_host_resources(meminfo.with_name("nope"))
        self.assertEqual((missing.mem_total_mb, missing.mem_available_mb), (0, 0))

    def test_vm_cost_adds_the_qemu_overhead(self):
        self.assertEqual(scheduler.vm_cost({"memory_mb": 1024, "cpus": 2}), scheduler.VmCost(1024 + scheduler.QEMU_OVERHEAD_MB, 2))
        self.assertEqual(scheduler.vm_cost({}).cpus, 1)

    def test_fits_respects_memory_and_cpu_budgets(self):
        sched = scheduler.DynamicScheduler(scheduler.HostResources(32000, 22000, 16), reserve_mb=2000,
                                           memory_source=lambda: 22000)
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
        sched = scheduler.DynamicScheduler(scheduler.HostResources(16000, 10192, 8), reserve_mb=2000, max_workers=8,
                                           memory_source=lambda: 10192)
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
        deadline = time.time() + 5  # its own budget: on a loaded host the first wait can eat the previous one
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
        sched = scheduler.DynamicScheduler(scheduler.HostResources(4000, 3000, 2), reserve_mb=2000,
                                           memory_source=lambda: 3000)  # 1000 MB budget

        def worker(name: str) -> str:
            if name == "boom":
                raise RuntimeError("worker failed")
            return name

        results = sched.run([("huge", scheduler.VmCost(9000, 4)), ("boom", scheduler.VmCost(512, 1))], worker)
        outcomes = dict(results)
        self.assertEqual(outcomes["huge"], "huge")
        self.assertIsInstance(outcomes["boom"], RuntimeError)
        self.assertEqual(sched.peak_running, 1)

    def test_run_shrinks_budget_when_live_memory_drops(self):
        samples = mock.Mock(side_effect=[10000, 10000, 6000, 6000, 6000])
        sched = scheduler.DynamicScheduler(scheduler.HostResources(16000, 10000, 8), reserve_mb=2000,
                                           memory_source=samples)
        jobs = [("anchor", scheduler.VmCost(4000, 1)), ("short", scheduler.VmCost(1000, 1)),
                ("waiting", scheduler.VmCost(3000, 1))]
        self.assertTrue(sched.fits([cost for _, cost in jobs[:2]], jobs[2][1]))
        results, starts, output = self.run_controlled(sched, jobs, [
            (["anchor", "short"], "short"),
            (["anchor", "short"], "anchor"),
            (["anchor", "short", "waiting"], "waiting"),
        ])
        self.assertEqual([name for name, _ in results], ["short", "anchor", "waiting"])
        self.assertEqual(starts[-1], ("waiting", 1, 6000, 4000))
        waiting = [line for line in output.splitlines() if "waiting waiting" in line]
        self.assertEqual(len(waiting), 2)
        for field in ("requested 3000 MB", "live available 6000 MB", "committed 5000 MB", "room 0 MB"):
            self.assertIn(field, waiting[0])
        self.assertEqual(samples.call_count, 5)

    def test_run_expands_budget_again_when_live_memory_returns(self):
        samples = mock.Mock(side_effect=[10000, 10000, 6000, 10000])
        sched = scheduler.DynamicScheduler(scheduler.HostResources(16000, 10000, 8), reserve_mb=2000,
                                           memory_source=samples)
        jobs = [("anchor", scheduler.VmCost(4000, 1)), ("short", scheduler.VmCost(1000, 1)),
                ("waiting", scheduler.VmCost(3000, 1))]
        results, starts, _ = self.run_controlled(sched, jobs, [
            (["anchor", "short"], "short"),
            (["anchor", "short", "waiting"], "waiting"),
            (["anchor", "short", "waiting"], "anchor"),
        ])
        self.assertEqual(starts[-1], ("waiting", 2, 10000, 8000))
        self.assertEqual([name for name, _ in results], ["short", "waiting", "anchor"])
        self.assertEqual(samples.call_count, 4)

    def test_rapid_starts_cannot_exceed_static_budget_before_ram_is_resident(self):
        # Even rising MemAvailable must not spend the RAM promised to untouched guests.
        samples = mock.Mock(return_value=12000)
        sched = scheduler.DynamicScheduler(scheduler.HostResources(16000, 10000, 8), reserve_mb=2000,
                                           memory_source=samples)
        jobs = [(name, scheduler.VmCost(3000, 1)) for name in ("a", "b", "c", "d")]
        results, starts, output = self.run_controlled(sched, jobs, [
            (["a", "b"], "a"),
            (["a", "b", "c"], "b"),
            (["a", "b", "c", "d"], "d"),
            (["a", "b", "c", "d"], "c"),
        ])
        self.assertEqual(sched.peak_running, 2)
        for _, count, available, budget in starts:
            self.assertEqual(available, 12000)
            self.assertEqual(budget, 8000)
            self.assertLessEqual(count * 3000, 8000)
        self.assertEqual([name for name, _ in results], ["a", "b", "d", "c"])
        self.assertIn("committed 6000 MB", output)
        self.assertIn("room 2000 MB", output)

    def test_pending_scan_uses_one_snapshot_despite_oscillating_memory(self):
        samples = mock.Mock(side_effect=[10000, 6000, 10000, 10000, 10000])
        sched = scheduler.DynamicScheduler(scheduler.HostResources(16000, 10000, 8), reserve_mb=2000,
                                           memory_source=samples)
        jobs = [("anchor", scheduler.VmCost(4000, 1)), ("big", scheduler.VmCost(5000, 1)),
                ("small", scheduler.VmCost(4000, 1))]
        _, starts, output = self.run_controlled(sched, jobs, [
            (["anchor"], "anchor"),
            (["anchor", "big"], "big"),
            (["anchor", "big", "small"], "small"),
        ])
        self.assertEqual([name for name, *_ in starts], ["anchor", "big", "small"])
        waiting = [line for line in output.splitlines() if "waiting " in line]
        self.assertEqual(len(waiting), 2, "only the first pending job should log in each blocked scan")
        self.assertIn("waiting big", waiting[0])
        self.assertIn("live available 6000 MB", waiting[0])
        self.assertIn("waiting small", waiting[1])
        self.assertEqual(samples.call_count, 5)
        self.assertEqual(self.monotonic.call_count, samples.call_count + len(starts))

    def test_run_never_stalls_at_zero_live_budget_and_reports_exceptions(self):
        sched = scheduler.DynamicScheduler(scheduler.HostResources(4000, 3000, 2), reserve_mb=2000,
                                           memory_source=lambda: 0)

        def worker(name):
            if name == "boom":
                raise RuntimeError("worker failed")
            return name

        results, starts, output = self.run_controlled(sched, [
            ("huge", scheduler.VmCost(9000, 4)), ("boom", scheduler.VmCost(512, 1)),
        ], [(["huge"], "huge"), (["huge", "boom"], "boom")], worker)
        self.assertEqual(results[0], ("huge", "huge"))
        self.assertIsInstance(results[1][1], RuntimeError)
        self.assertEqual(sched.peak_running, 1)
        self.assertTrue(all(count == 1 and budget == 0 for _, count, _, budget in starts))
        self.assertIn("starting huge alone despite budget", output)
        self.assertIn("live available 0 MB", output)
        self.assertIn("budget 0 MB", output)

    def test_default_memory_source_refreshes_and_describe_keeps_the_snapshot(self):
        resources = scheduler.HostResources(16000, 10000, 8)
        with mock.patch.object(scheduler, "host_resources", return_value=scheduler.HostResources(16000, 6000, 1)) as probe:
            sched = scheduler.DynamicScheduler(resources, reserve_mb=2000)
            self.run_controlled(sched, [("a", scheduler.VmCost(1000, 1))], [(["a"], "a")])
            with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                scheduler.print_plan(sched, [("next", scheduler.VmCost(3000, 2))])
            description = "\n".join(sched.describe())
            probe.assert_called_once_with()
        self.assertIn("6000 MB available", description)
        self.assertIn("budget: 4000 MB", description)
        self.assertIn("8000 MB initial cap", description)
        self.assertIn(f"younger than {scheduler.GRACE_SEC}s", description)
        self.assertIn("3000 MB, 2 vCPU", stdout.getvalue())
        self.assertEqual(sched.cpu_budget, 16)

    def test_live_sampling_preserves_cpu_and_worker_limits(self):
        for cpus, max_workers in [(1, 8), (8, 1)]:
            with self.subTest(cpus=cpus, max_workers=max_workers):
                sched = scheduler.DynamicScheduler(scheduler.HostResources(16000, 10000, cpus), reserve_mb=2000,
                                                   max_workers=max_workers, memory_source=lambda: 10000)
                jobs = [(name, scheduler.VmCost(512, 2)) for name in ("a", "b")]
                _, _, output = self.run_controlled(sched, jobs, [(["a"], "a"), (["a", "b"], "b")])
                self.assertEqual(sched.peak_running, 1)
                if cpus == 1:
                    self.assertIn("CPU:", output)
                    self.assertIn("committed 2 vCPU", output)

    def test_live_budget_keeps_pending_preference_and_allows_smaller_jobs(self):
        samples = mock.Mock(side_effect=[10000, 9000, 9000, 9000])
        sched = scheduler.DynamicScheduler(scheduler.HostResources(16000, 10000, 8), reserve_mb=2000,
                                           memory_source=samples)
        jobs = [("anchor", scheduler.VmCost(4000, 1)), ("big", scheduler.VmCost(4000, 1)),
                ("small", scheduler.VmCost(1000, 1))]
        results, _, _ = self.run_controlled(sched, jobs, [
            (["anchor", "small"], "anchor"),
            (["anchor", "small", "big"], "big"),
            (["anchor", "small", "big"], "small"),
        ])
        self.assertEqual([name for name, _ in results], ["anchor", "big", "small"])

    def test_resident_guests_leave_room_for_small_vm_on_30_gb_host(self):
        # Reproduce the 19 GB available host: after one of three 4 GB guests exits,
        # the two residents must not be charged against MemAvailable a second time.
        for reserve, cap, room in [(2000, 17000, 7784), (2048, 16952, 7736)]:
            with self.subTest(reserve_mb=reserve):
                snapshots = iter([(19000, 0)] * 3 + [(6712, scheduler.GRACE_SEC - 1), (10808, scheduler.GRACE_SEC)])
                clock = mock.Mock(return_value=0.0)

                def memory_source():
                    available, now = next(snapshots)
                    clock.return_value = now
                    return available

                sched = scheduler.DynamicScheduler(scheduler.HostResources(30000, 19000, 8), reserve_mb=reserve,
                                                   memory_source=memory_source, time_source=clock)
                self.assertEqual(sched.mem_budget_mb, cap)
                jobs = [(name, scheduler.VmCost(4608, 2)) for name in ("a", "b", "c")]
                jobs.append(("ubuntu-8.04", scheduler.VmCost(1536, 2)))
                results, starts, output = self.run_controlled(sched, jobs, [
                    (["a", "b", "c"], "a"),
                    (["a", "b", "c", "ubuntu-8.04"], "b"),
                    (["a", "b", "c", "ubuntu-8.04"], "c"),
                    (["a", "b", "c", "ubuntu-8.04"], "ubuntu-8.04"),
                ])
                self.assertIn("live available 6712 MB", output)
                self.assertEqual(starts[-1], ("ubuntu-8.04", 3, 10808, cap))
                self.assertEqual(starts[-1][3] - 2 * 4608, room)
                self.assertEqual([name for name, _ in results], ["a", "b", "c", "ubuntu-8.04"])
                self.assertIsNone(next(snapshots, None))

    def test_grace_expires_per_vm_at_the_boundary(self):
        clock = mock.Mock(return_value=float(scheduler.GRACE_SEC))
        sched = scheduler.DynamicScheduler(scheduler.HostResources(30000, 19000, 8),
                                           memory_source=lambda: 10808, time_source=clock)
        cost = scheduler.VmCost(4608, 2)
        running = [(cost, 0.0), (cost, 1.0)]
        active = [cost, cost]
        # Only the second VM remains in grace, leaving 4152 MB instead of 7736 MB.
        sched._refresh_memory_budget(running)
        self.assertEqual(sched.mem_budget_mb - 9216, 4152)
        self.assertFalse(sched.fits(active, cost))
        clock.return_value += 1
        # A decision snapshot stays stable even if grace expires during the scan.
        self.assertFalse(sched.fits(active, cost))
        clock.assert_called_once_with()
        sched._refresh_memory_budget(running)
        self.assertEqual(sched.mem_budget_mb - 9216, 7736)
        self.assertTrue(sched.fits(active, cost))

    def test_live_pressure_still_limits_guests_after_grace(self):
        samples = mock.Mock(side_effect=[3000, 10808, 14904])
        sched = scheduler.DynamicScheduler(scheduler.HostResources(30000, 19000, 8),
                                           memory_source=samples, time_source=lambda: float(scheduler.GRACE_SEC))
        cost = scheduler.VmCost(4608, 2)
        small = scheduler.VmCost(1536, 2)
        sched._refresh_memory_budget([(cost, 0.0)] * 2)
        self.assertEqual(sched.mem_budget_mb - 9216, 952)
        self.assertFalse(sched.fits([cost, cost], small))
        sched._refresh_memory_budget([(cost, 0.0)] * 2)
        self.assertEqual(sched.mem_budget_mb - 9216, 7736)
        self.assertTrue(sched.fits([cost, cost], small))
        # At t3 one resident remains and the initial cap still limits the recovered RAM.
        sched._refresh_memory_budget([(cost, 0.0)])
        self.assertEqual(sched.mem_budget_mb - 4608, 12344)

    def test_waiting_log_covers_only_the_first_of_many_pending_jobs(self):
        sched = scheduler.DynamicScheduler(scheduler.HostResources(4000, 3000, 2), reserve_mb=2000,
                                           memory_source=lambda: 3000, time_source=lambda: 0.0)
        jobs = [(f"vm-{index}", scheduler.VmCost(1000, 1)) for index in range(40)]
        names = [name for name, _ in jobs]
        _, _, output = self.run_controlled(sched, jobs, [(names[:index + 1], name) for index, name in enumerate(names)])
        waiting = [line for line in output.splitlines() if "waiting " in line]
        self.assertEqual(len(waiting), len(jobs) - 1)
        for line, name in zip(waiting, names[1:]):
            self.assertIn(f"waiting {name} ", line)


if __name__ == "__main__":
    unittest.main()
