from unittest import mock

from _common import BaseVmctlTestCase
import vmctl.vmstats as vmstats


def write_proc(root, pid, *, ticks, rss_kb=2048, read=0, write=0, io=True):
    base = root / str(pid)
    base.mkdir(parents=True, exist_ok=True)
    # The command name may hold spaces and parentheses: fields start after the last ')'.
    fields = ["S"] + ["0"] * 10 + [str(ticks), "0"] + ["0"] * 30
    (base / "stat").write_text(f"{pid} (qemu-system (x86)) " + " ".join(fields) + "\n")
    (base / "status").write_text(f"Name:\tqemu\nVmRSS:\t   {rss_kb} kB\nThreads:\t9\n")
    if io:
        (base / "io").write_text(f"rchar: 1\nwchar: 2\nread_bytes: {read}\nwrite_bytes: {write}\n")
    elif (base / "io").exists():
        (base / "io").unlink()


class VmStatsTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.proc = self.root / "proc"
        self.clock = [100.0]
        patcher = mock.patch.object(vmstats.time, "monotonic", side_effect=lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        tick = mock.patch.object(vmstats, "CLK_TCK", 100)
        tick.start()
        self.addCleanup(tick.stop)

    def test_the_first_reading_has_memory_but_no_rates(self):
        write_proc(self.proc, 42, ticks=500, rss_kb=4096)
        stats = vmstats.Sampler(self.proc).measure(42, vcpus=2, memory_mb=1024)
        self.assertEqual(stats["rss_bytes"], 4096 * 1024)
        self.assertEqual(stats["memory_bytes"], 1024 * 1024 * 1024)
        self.assertIsNone(stats["cpu_percent"])
        self.assertIsNone(stats["write_bps"])

    def test_cpu_is_a_share_of_the_vms_own_vcpus_and_disk_a_rate(self):
        sampler = vmstats.Sampler(self.proc)
        write_proc(self.proc, 42, ticks=1000, read=0, write=0)
        sampler.measure(42, vcpus=2)
        self.clock[0] += 2.0
        write_proc(self.proc, 42, ticks=1200, read=4 * 1024 * 1024, write=8 * 1024 * 1024)  # 2 CPU-seconds in 2 s
        stats = sampler.measure(42, vcpus=2)
        self.assertEqual(stats["cpu_percent"], 50.0)  # one of two vCPUs busy
        self.assertEqual(stats["read_bps"], 2 * 1024 * 1024)
        self.assertEqual(stats["write_bps"], 4 * 1024 * 1024)
        self.assertEqual(stats["idle_seconds"], 0)

    def test_idle_time_grows_while_cpu_and_disk_stay_quiet(self):
        sampler = vmstats.Sampler(self.proc)
        write_proc(self.proc, 42, ticks=1000)
        sampler.measure(42, vcpus=1)
        for _ in range(3):
            self.clock[0] += 60.0
            sampler.measure(42, vcpus=1)  # no CPU time, no bytes: idle
        self.assertEqual(sampler.measure(42, vcpus=1)["idle_seconds"], 180)
        self.clock[0] += 60.0
        write_proc(self.proc, 42, ticks=1000 + 6000)  # busy again
        self.assertEqual(sampler.measure(42, vcpus=1)["idle_seconds"], 0)

    def test_readings_too_close_keep_the_previous_rate(self):
        sampler = vmstats.Sampler(self.proc)
        write_proc(self.proc, 42, ticks=0)
        sampler.measure(42)
        self.clock[0] += 1.0
        write_proc(self.proc, 42, ticks=50)
        self.assertEqual(sampler.measure(42)["cpu_percent"], 50.0)
        self.clock[0] += 0.1  # a second browser tab polling at the same moment
        write_proc(self.proc, 42, ticks=60)
        self.assertEqual(sampler.measure(42)["cpu_percent"], 50.0)

    def test_an_unreadable_io_file_leaves_disk_unknown(self):
        sampler = vmstats.Sampler(self.proc)
        write_proc(self.proc, 42, ticks=0, io=False)
        sampler.measure(42)
        self.clock[0] += 1.0
        stats = sampler.measure(42)
        self.assertEqual(stats["cpu_percent"], 0.0)
        self.assertIsNone(stats["read_bps"])

    def test_a_vanished_process_is_none_and_forgotten(self):
        sampler = vmstats.Sampler(self.proc)
        write_proc(self.proc, 42, ticks=0)
        sampler.measure(42)
        for name in ("stat", "status", "io"):
            (self.proc / "42" / name).unlink()
        self.assertIsNone(sampler.measure(42))
        self.assertNotIn(42, sampler.previous)

    def test_rows_give_their_pid_through_every_runtime_form(self):
        self.assertEqual(vmstats.pid_of({"runtime": "tracked:11"}), 11)
        self.assertEqual(vmstats.pid_of({"runtime": "running:12"}), 12)
        self.assertEqual(vmstats.pid_of({"runtime": "hostfwd:2222", "runtime_note": "pid=13"}), 13)
        self.assertIsNone(vmstats.pid_of({"runtime": "closed:2222"}))
        self.assertIsNone(vmstats.pid_of({"runtime": "-"}))

    def test_for_rows_measures_running_rows_only_and_drops_stopped_ones(self):
        sampler = vmstats.Sampler(self.proc)
        write_proc(self.proc, 11, ticks=0)
        write_proc(self.proc, 12, ticks=0)
        rows = [{"name": "a", "running": True, "runtime": "tracked:11", "cpus": 2, "memory_mb": 512},
                {"name": "b", "running": False, "runtime": "-", "cpus": 1, "memory_mb": 256},
                {"name": "c", "running": True, "runtime": "running:12", "cpus": "", "memory_mb": ""}]
        out = vmstats.for_rows(sampler, rows)
        self.assertEqual(sorted(out), ["a", "c"])
        self.assertEqual(out["a"]["memory_bytes"], 512 * 1024 * 1024)
        rows[2]["running"] = False
        vmstats.for_rows(sampler, rows)
        self.assertEqual(set(sampler.previous), {11})
