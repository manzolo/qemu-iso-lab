import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vmctl import qemu, recent, runtime, state, webui
from vmctl.errors import VMError


class RecentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        patch = mock.patch.object(state, "ROOT", self.root)
        patch.start()
        self.addCleanup(patch.stop)
        self.executable = self.root / "qemu-system-fixture"
        self.executable.write_text("#!/bin/sh\nexit 0\n")
        self.executable.chmod(0o755)
        self.cmd = [str(self.executable), "-name", "vmctl:testvm"]

    def test_launches_replace_one_timestamp_and_survive_disk_cleanup(self):
        with mock.patch.object(recent.time, "time_ns", side_effect=[10**9, 2*10**9, 3*10**9]):
            recent.record(self.cmd)
            recent.record([str(self.executable), "-name", "vmctl:other"])
            recent.record(self.cmd)
        self.assertEqual(recent.last_started("testvm"), 3)
        self.assertEqual(recent.last_started("other"), 2)
        self.assertFalse((self.root / "artifacts/testvm").exists())
        self.assertEqual(len(list((self.root / "artifacts/.recent").iterdir())), 2)

    def test_foreground_and_logged_launches_record(self):
        for logged in (False, True):
            with self.subTest(logged=logged):
                runtime.run(self.cmd, stdout_log=self.root / "output.log" if logged else None)
                self.assertIsNotNone(recent.last_started("testvm"))
                (self.root / "artifacts/.recent/testvm").unlink()

    def test_background_launches_record_with_combined_or_separate_logs(self):
        for separate in (False, True):
            with self.subTest(separate=separate), mock.patch.object(runtime.subprocess, "Popen") as popen:
                popen.return_value.pid = 42
                self.assertEqual(runtime.run_background(self.cmd, self.root / "output.log",
                                 stderr_path=self.root / "error.log" if separate else None), 42)
                self.assertIsNotNone(recent.last_started("testvm"))
                (self.root / "artifacts/.recent/testvm").unlink()

    def test_serial_launch_records_and_dry_run_does_not(self):
        self.executable.write_text("#!/bin/sh\necho boot-ready\n")
        qemu.run_and_expect(self.cmd, "boot-ready", 5, dry_run=True)
        self.assertIsNone(recent.last_started("testvm"))
        with subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT) as process:
            with mock.patch.object(qemu.subprocess, "Popen", return_value=process):
                qemu.run_and_expect(self.cmd, "boot-ready", 5)
        self.assertIsNotNone(recent.last_started("testvm"))

    def test_foreground_launch_keeps_input_failure_output_and_timeout(self):
        self.executable.write_text("#!/bin/sh\ncat\necho error >&2\nexit 7\n")
        with self.assertRaises(subprocess.CalledProcessError) as error:
            runtime.run(self.cmd, quiet=True, capture_error_output=True, stdin_text="hello")
        self.assertEqual(error.exception.returncode, 7)
        self.assertEqual(error.exception.output, "hello")
        self.assertEqual(error.exception.stderr, "error\n")
        self.executable.write_text("#!/bin/sh\nexec sleep 5\n")
        with self.assertRaisesRegex(VMError, "Timed out"):
            runtime.run(self.cmd, timeout_sec=0.02)

    def test_dry_runs_and_spawn_errors_do_not_record(self):
        runtime.run(self.cmd, dry_run=True)
        runtime.run_background(self.cmd, self.root / "output.log", dry_run=True)
        self.assertIsNone(recent.last_started("testvm"))
        with mock.patch.object(runtime.subprocess, "Popen", side_effect=OSError("cannot spawn")):
            with self.assertRaises(OSError):
                runtime.run(self.cmd)
            with self.assertRaises(OSError):
                runtime.run_background(self.cmd, self.root / "output.log")
        self.assertIsNone(recent.last_started("testvm"))

    def test_unrelated_commands_and_invalid_names_are_ignored(self):
        for cmd in (["echo", "-name", "vmctl:testvm"], [str(self.executable)],
                    [str(self.executable), "-name", "testvm"],
                    [str(self.executable), "-name", "vmctl:../escape"]):
            recent.record(cmd)
        self.assertFalse((self.root / "artifacts/.recent").exists())
        self.assertIsNone(recent.last_started("../escape"))

    def test_corrupt_history_and_storage_errors_do_not_break_launches(self):
        recent.record(self.cmd)
        path = self.root / "artifacts/.recent/testvm"
        for contents in ("invalid", "-1", "0", str(10**100)):
            path.write_text(contents)
            self.assertIsNone(recent.last_started("testvm"))
        with mock.patch.object(recent.os, "replace", side_effect=OSError("read only")):
            runtime.run(self.cmd)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_snapshot_reads_persisted_launches_for_known_profiles(self):
        recent.record(self.cmd)
        with mock.patch("vmctl.tui_bridge.ClassicBridge") as bridge:
            bridge.return_value.snapshot.return_value = [{"name": "testvm"}, {"name": "never-started"}]
            bridge.return_value.labs.return_value = []
            rows = webui.Snapshot().get()["vms"]
        self.assertEqual(rows[0]["last_started"], recent.last_started("testvm"))
        self.assertIsNone(rows[1]["last_started"])
