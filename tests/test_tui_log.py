"""Guest terminal controls must not escape the installation log viewer."""
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import unittest

from vmctl.tui_log import LogText


ROOT = Path(__file__).resolve().parent.parent


class LogTextTests(unittest.TestCase):
    def test_installer_controls_are_removed_at_every_chunk_boundary(self):
        raw = ("\x1b[?1049h\x1b[2;24r\x1b[?6h\x1b[H\x1b[7mInstalling"
               "\x1b[m\x1b(B\x1b)0\x0f 50%\r\n"
               "\x1b]0;Guest title\x07\x1b]52;c;clipboard\x1b\\"
               "\x1bPdevice data\x1b\\\x1b_ignored\x1b\\"
               "\x9b31mDéjà\x9b0m\t100%\rDone\b!\x07\n")
        expected = "Installing 50%\nDéjà\t100%\nDone!\n"
        for boundary in range(len(raw) + 1):
            cleaner = LogText()
            with self.subTest(boundary=boundary):
                self.assertEqual(cleaner.feed(raw[:boundary]) + cleaner.feed(raw[boundary:]), expected)
        cleaner = LogText()
        self.assertEqual("".join(cleaner.feed(char) for char in raw), expected)

    def test_truncated_escape_is_not_printed(self):
        cleaner = LogText()
        self.assertEqual(cleaner.feed("Ready\x1b[?104"), "Ready")
        self.assertEqual(cleaner.feed("9hNext"), "Next")

    def test_filter_streams_progress_without_waiting_for_newline(self):
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "vmctl/tui_log.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            process.stdin.write("\x1b[2;24rDéjà 50%".encode())
            process.stdin.flush()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=5), "Progress was buffered")
            self.assertEqual(os.read(process.stdout.fileno(), 1024).decode(), "Déjà 50%")
            process.send_signal(signal.SIGINT)
            _, errors = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(errors, b"")
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()

    def test_ctrl_c_returns_from_shell_log_viewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "artifacts/test/runtime/tui-job/output.log"
            log.parent.mkdir(parents=True)
            log.write_text("\x1b[2;24r\x1b[HINSTALL_PROGRESS\n")
            # The real shell viewer and tail run in their own foreground group.
            # SIGINT targets the viewer, just as terminal Ctrl-C does.
            script = '''source "$1"
ROOT_DIR="$2"
clear() { :; }
install_interrupt_guard
current_vm=test
show_installation_log
printf 'BACK_TO_MENU\n'
'''
            (Path(tmp) / "vmctl").symlink_to(ROOT / "vmctl", target_is_directory=True)
            # Built from scratch (host isolation): python3 on PATH is the interpreter running the tests.
            env = {"PATH": os.pathsep.join([str(Path(sys.executable).parent), os.defpath]),
                   "HOME": tmp, "LC_ALL": "C.UTF-8", "VMTUI_TEST_MODE": "1", "VMTUI_UI": "textual",
                   "VMTUI_TEXTUAL_PYTHON": sys.executable}
            process = subprocess.Popen(
                ["bash", "-c", script, "log-test", str(ROOT / "bin/vmtui"), tmp],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, start_new_session=True,
            )
            try:
                output = b""
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while b"INSTALL_PROGRESS" not in output:
                        self.assertTrue(selector.select(timeout=5), output)
                        chunk = os.read(process.stdout.fileno(), 4096)
                        self.assertTrue(chunk, output)
                        output += chunk
                os.killpg(process.pid, signal.SIGINT)
                rest, errors = process.communicate(timeout=5)
                output += rest
                self.assertEqual(process.returncode, 0, errors)
                self.assertIn(b"BACK_TO_MENU", output)
                self.assertNotIn(b"\x1b", output)
                self.assertNotIn(b"Traceback", errors)
                self.assertIn("\x1b[2;24r", log.read_text())  # source log is untouched
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
