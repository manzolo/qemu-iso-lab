import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vmctl import tui_jobs


class TuiJobTests(unittest.TestCase):
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
