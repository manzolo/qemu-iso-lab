import contextlib
import io
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vmctl import flash_progress
from vmctl.errors import VMError


class FlashProgressTests(unittest.TestCase):
    def test_percentages_from_each_tool_and_unknown_output(self):
        for output, expected in (
            ("(42.50/100%)", 42.5),
            ("Elapsed: 00:01, Completed: 17.2%, Rate: 100MB/min", 17.2),
            ("pct rescued: 92.34%, read errors: 0", 92.34),
            ("(1.00/100%)\r(7.00/100%)", 7.0),
            ("creating bitmap", None),
        ):
            self.assertEqual(flash_progress.percentage(output), expected)

    def test_non_tty_output_is_compact_and_log_keeps_both_streams(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            progress = flash_progress.FlashProgress(Path(directory))
            progress.run([sys.executable, "-c", "import sys; print('tool banner'); print('diagnostic', file=sys.stderr)"],
                         title="Prepare image")
            log = progress.log_path.read_text()
            self.assertIn("tool banner", log)
            self.assertIn("diagnostic", log)
            self.assertIn("Exit code: 0", log)
            self.assertNotIn("tool banner", output.getvalue())
            self.assertNotIn("\x1b", output.getvalue())
            self.assertIn("[ok] Prepare image", output.getvalue())

    def test_failure_keeps_exit_status_and_prints_diagnostics_without_success(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            progress = flash_progress.FlashProgress(Path(directory))
            with self.assertRaises(subprocess.CalledProcessError) as error:
                progress.run([sys.executable, "-c", "import sys; print('filesystem rejected', file=sys.stderr); sys.exit(7)"],
                             title="Scan partition")
            self.assertEqual(error.exception.returncode, 7)
            self.assertIn("filesystem rejected", error.exception.output)
            self.assertIn("filesystem rejected", output.getvalue())
            self.assertIn(str(progress.log_path), output.getvalue())
            self.assertNotIn("[ok]", output.getvalue())

    def test_interrupt_waits_for_tool_exit_before_returning(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            progress = flash_progress.FlashProgress(Path(directory))
            process = mock.Mock(pid=1234)
            process.wait.side_effect = [KeyboardInterrupt(), 0]
            process.poll.return_value = None
            with mock.patch.object(flash_progress.subprocess, "Popen", return_value=process), \
                 mock.patch.object(os, "killpg") as kill:
                with self.assertRaisesRegex(VMError, "interrupted; the command has stopped"):
                    progress.run(["test-tool"], title="Write disk")
            kill.assert_called_once_with(1234, signal.SIGINT)
            self.assertEqual(process.wait.call_count, 2)
            self.assertNotIn("[ok]", output.getvalue())

    def test_tty_progress_fits_80_columns(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            progress = flash_progress.FlashProgress(Path(directory))
            with mock.patch.object(flash_progress.shutil, "get_terminal_size", return_value=os.terminal_size((80, 24))):
                progress._draw("A very long partition description " * 4, 12.5, 63)
            line = output.getvalue().split("\r\x1b[2K")[-1]
            self.assertLessEqual(len(line), 79)
            self.assertIn("12.5%", line)
            self.assertIn("01:03", line)
            self.assertNotIn("[ok]", line)
