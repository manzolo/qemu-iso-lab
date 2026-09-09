import os
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vmctl import tui_jobs


class TuiJobTests(unittest.TestCase):
    def launch(self, root, command):
        subprocess.run(
            [sys.executable, "-c", "from pathlib import Path; from vmctl import tui_jobs; "
             f"tui_jobs.start(Path({str(root)!r}), 'test-vm', {command!r})"],
            check=True, capture_output=True, timeout=5,
        )

    def wait_until(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(predicate())

    def test_cancel_stops_descendants_and_allows_restart_after_vm_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = tui_jobs.job_dir(root, "test-vm")
            disk = root / "disk.qcow2"
            disk.write_text("keep this disk")
            ready = root / "ready"
            child_code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
            command = [sys.executable, "-u", "-c", (
                "import json, os, pathlib, signal, subprocess, sys, time\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
                f"pathlib.Path({str(ready)!r}).write_text(json.dumps([os.getpid(), child.pid]))\n"
                "print('installation progress', flush=True)\n"
                "time.sleep(30)\n"
            )]
            # This independent session represents a post-install QEMU which
            # outlives the CLI's process group and needs lifecycle cleanup.
            detached = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                        start_new_session=True)
            pgid = None
            try:
                self.launch(root, command)
                self.wait_until(ready.exists)
                pgid = tui_jobs.worker_group(directory)
                self.assertIsNotNone(pgid)

                def stop_vm():
                    self.assertFalse(tui_jobs.group_alive(pgid))
                    self.assertIsNone(detached.poll())
                    with self.assertRaisesRegex(RuntimeError, "operation is already in progress"):
                        tui_jobs.start(root, "test-vm", command)
                    detached.terminate()
                    detached.wait(timeout=5)

                self.assertTrue(tui_jobs.cancel(root, "test-vm", stop_vm, grace_sec=0.1))
                self.assertFalse(tui_jobs.group_alive(pgid))
                for pid in json.loads(ready.read_text()):
                    path = Path(f"/proc/{pid}/stat")
                    if path.exists():
                        self.assertIn(path.read_text().rsplit(")", 1)[1].split()[0], ("Z", "X"))
                self.assertEqual(tui_jobs.status(directory), "cancelled")
                self.assertEqual(disk.read_text(), "keep this disk")
                output = (directory / "output.log").read_text()
                self.assertIn("installation progress", output)
                self.assertIn("cancelled by user", output)
                self.launch(root, [sys.executable, "-c", "print('restarted')"])
                self.wait_until(lambda: tui_jobs.status(directory) == "completed")
            finally:
                if pgid is not None and tui_jobs.group_alive(pgid):
                    os.killpg(pgid, signal.SIGKILL)
                if detached.poll() is None:
                    detached.kill()
                detached.wait(timeout=5)

    def test_cancel_does_not_stop_vm_for_finished_or_missing_job(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stop_vm = mock.Mock()
            self.assertFalse(tui_jobs.cancel(root, "test-vm", stop_vm))
            self.launch(root, [sys.executable, "-c", "pass"])
            directory = tui_jobs.job_dir(root, "test-vm")
            self.wait_until(lambda: tui_jobs.status(directory) == "completed")
            self.assertFalse(tui_jobs.cancel(root, "test-vm", stop_vm))
            self.assertEqual(tui_jobs.status(directory), "completed")
            stop_vm.assert_not_called()

    def test_cancel_refuses_unidentified_supervisor(self):
        with tempfile.TemporaryDirectory() as temp, \
             mock.patch.object(tui_jobs, "status", return_value="running"), \
             mock.patch.object(tui_jobs, "worker_group", return_value=None), \
             mock.patch.object(tui_jobs.os, "killpg") as killpg:
            stop_vm = mock.Mock()
            with self.assertRaisesRegex(RuntimeError, "Cannot identify"):
                tui_jobs.cancel(Path(temp), "test-vm", stop_vm)
            killpg.assert_not_called()
            stop_vm.assert_not_called()

    def test_job_survives_launcher_exit_and_records_output_and_failure(self):
        for code in (0, 7):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                gate = root / "continue"
                directory = tui_jobs.job_dir(root, "test-vm")
                command = [sys.executable, "-u", "-c", (
                    "import os, pathlib, time, sys\n"
                    "print('stdin:', repr(sys.stdin.read()), flush=True)\n"
                    "print('session:', os.getsid(0), flush=True)\n"
                    "deadline = time.monotonic() + 10\n"
                    f"while not pathlib.Path({str(gate)!r}).exists() and time.monotonic() < deadline:\n"
                    "    time.sleep(0.01)\n"
                    "print('guest output', flush=True)\n"
                    "print('guest error', file=sys.stderr, flush=True)\n"
                    f"sys.exit({code})\n"
                )]
                launcher = (
                    "from pathlib import Path\n"
                    "from vmctl import tui_jobs\n"
                    f"tui_jobs.start(Path({temp!r}), 'test-vm', {command!r})\n"
                )
                try:
                    subprocess.run([sys.executable, "-c", launcher], check=True,
                                   capture_output=True, timeout=5)
                    self.assertEqual(tui_jobs.status(directory), "running")
                    with self.assertRaisesRegex(RuntimeError, "already running"):
                        tui_jobs.start(root, "test-vm", command)
                finally:
                    gate.touch()
                    deadline = time.monotonic() + 10
                    while tui_jobs.status(directory) == "running" and time.monotonic() < deadline:
                        time.sleep(0.01)
                expected = "completed" if code == 0 else "failed (7)"
                self.assertEqual(tui_jobs.status(directory), expected)
                output = (directory / "output.log").read_text()
                self.assertIn("stdin: ''", output)
                self.assertIn("guest output", output)
                self.assertIn("guest error", output)
                self.assertIn(f"Command {expected}.", output)
                session_line = next(line for line in output.splitlines() if line.startswith("session:"))
                self.assertNotEqual(int(session_line.split()[1]), os.getsid(0))

    def test_stale_running_status_is_interrupted(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            self.assertEqual(tui_jobs.status(directory), "")
            (directory / "lock").touch()
            (directory / "status").write_text("running\n")
            self.assertEqual(tui_jobs.status(directory), "interrupted")

    def test_job_path_rejects_non_vm_names(self):
        for name in ("", ".", "..", "../outside", "/tmp/outside"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                tui_jobs.job_dir(Path("/tmp"), name)
