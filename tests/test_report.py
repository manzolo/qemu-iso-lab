import argparse
import json
import struct
import zlib
from pathlib import Path
from unittest import mock

from _common import BaseVmctlTestCase
from vmctl import cli, lifecycle, qemu, report, runtime
from vmctl.errors import VMError


class ReportTests(BaseVmctlTestCase):
    def test_profile_history_is_preserved_separately_from_failed_run(self):
        self.vm_config["meta"] = {"status": "unattended", "verified": "2026-09-07"}
        args = argparse.Namespace(dry_run=False, _report_dir=str(self.root / "report"))
        report.record("testvm", self.vm_config, args, "failed", "desktop session missing", 1.0, "bootstrap-unattended")
        result = json.loads((self.root / "report/results/testvm.json").read_text())
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["profile_status"], "unattended")
        self.assertEqual(result["profile_verified"], "2026-09-07")
        page = report.render_html([result], {}, self.root)
        self.assertIn("Profile status: unattended", page)
        self.assertIn("Last live PASS: 2026-09-07", page)
        self.assertIn("FAIL: 1", page)
        result["profile_status"] = "<script>"
        result["profile_verified"] = '<img src=x onerror="bad">'
        page = report.render_html([result], {}, self.root)
        self.assertIn("Profile status: &lt;script&gt;", page)
        self.assertNotIn('<img src=x', page)

    def test_ppm_png_2x2(self):
        pixels = bytes([10, 32, 35, 255, 0, 0, 0, 255, 0, 0, 0, 255])
        png = report.ppm_to_png(b"P6\n# comment\n2 2\n255\n" + pixels)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        pos, compressed = 8, b""
        while pos < len(png):
            size = struct.unpack("!I", png[pos:pos+4])[0]
            kind, payload = png[pos+4:pos+8], png[pos+8:pos+8+size]
            self.assertEqual(struct.unpack("!I", png[pos+8+size:pos+12+size])[0], zlib.crc32(kind + payload))
            if kind == b"IHDR":
                self.assertEqual(struct.unpack("!2I5B", payload), (2, 2, 8, 2, 0, 0, 0))
            if kind == b"IDAT":
                compressed += payload
            pos += size + 12
        self.assertEqual(zlib.decompress(compressed), b"\0" + pixels[:6] + b"\0" + pixels[6:])
        for invalid in (b"", b"P3 2 2 255\n", b"P6 2 2 255\nshort", b"P6 0 2 255\n"):
            with self.assertRaises(ValueError):
                report.ppm_to_png(invalid)

    def test_html_escaped_and_self_contained(self):
        (self.root / "screen.png").write_bytes(report.ppm_to_png(b"P6 1 1 255\nabc"))
        results = [dict(id="a", name="<script>", flow="boot-check", status=status, phase="boot", seconds=1.5,
                        detail='error & "detail"', screenshot="screen.png") for status in ("PASS", "WARN", "FAIL", "SKIP")]
        html = report.render_html(results, {"host": "<host>", "commit": "abc"}, self.root)
        self.assertIn("Total: 4", html)
        self.assertIn("PASS: 1", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("data:image/png;base64,", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn('src="http', html)

    def test_cards_and_badges_are_status_filters(self):
        results = [{"id": "a", "name": "A", "flow": "boot-check", "status": "PASS", "phase": "boot", "seconds": 1, "detail": ""},
                   {"id": "b", "name": "B", "flow": "bootstrap-preseed", "status": "FAIL", "phase": "install", "seconds": 2, "detail": "x"}]
        html_text = report.render_html(results, {"host": "h", "date": "2026-09-06T00:00:00+00:00", "commit": "abc"}, self.root)
        self.assertIn('<button type="button" class="metric PASS" data-status="PASS"', html_text)
        self.assertIn('<button type="button" class="metric total" data-status=""', html_text)
        self.assertIn('<button type="button" class="badge FAIL" data-status="FAIL"', html_text)
        self.assertNotIn('<span class="badge', html_text)
        self.assertIn("function setStatus(value)", html_text)
        self.assertIn(".metric[data-status], .badge[data-status]", html_text)

    def test_final_capture_wakes_a_blanked_console_but_the_watcher_does_not(self):
        # A server guest reaches the screenshot minutes after boot, past console blanking:
        # without the nudge the capture is a black rectangle (seen in the Rocky report row).
        calls = []

        def fake_qmp(socket_path, command, arguments=None):
            calls.append(command)
            if command == "screendump":
                Path(arguments["filename"]).write_bytes(b"P6 2 1 255\n" + b"\xff" * 6)
            return True

        with mock.patch.object(qemu, "qmp_command", side_effect=fake_qmp), \
             mock.patch.object(report.time, "sleep") as sleep:
            self.assertIsNone(report.capture_screenshot("vm", self.vm_config, self.root))
        self.assertEqual(calls, ["send-key", "screendump"])  # a lit screen is kept at once
        sleep.assert_called_once_with(report.CONSOLE_WAKE_DELAY_SEC)

        calls.clear()
        with mock.patch.object(qemu, "qmp_command", side_effect=fake_qmp):
            self.assertIsNone(report.capture_screenshot("vm", self.vm_config, self.root, wake=False))
        self.assertEqual(calls, ["screendump"])  # the install-time watcher must not type

    def test_all_black_capture_is_retried_then_kept(self):
        calls = []

        def fake_qmp(socket_path, command, arguments=None):
            calls.append(command)
            if command == "screendump":
                Path(arguments["filename"]).write_bytes(b"P6 2 1 255\n" + b"\x00" * 6)
            return True

        with mock.patch.object(qemu, "qmp_command", side_effect=fake_qmp), \
             mock.patch.object(report.time, "sleep"):
            self.assertIsNone(report.capture_screenshot("vm", self.vm_config, self.root))
        # A guest that paints its console late gets a few tries; a genuinely dark screen is
        # kept as it is instead of failing the row.
        self.assertEqual(calls.count("screendump"), report.BLANK_RETRIES)
        self.assertTrue((self.root / "screens/vm.png").exists())
        self.assertTrue(report.looks_blank(b"P6 2 1 255\n" + b"\x00" * 6))
        self.assertFalse(report.looks_blank(b"P6 2 1 255\n" + b"\xff" * 6))

    def test_capture_qmp_arguments(self):
        def qmp_call(path, command, **kwargs):
            self.assertIn(command, ("send-key", "screendump"))
            if command == "screendump":
                from pathlib import Path
                Path(kwargs["arguments"]["filename"]).write_bytes(b"P6 1 1 255\nabc")
            return True
        with mock.patch.object(qemu, "qmp_command", side_effect=qmp_call):
            self.assertIsNone(report.capture_screenshot("testvm", self.vm_config, self.root))
        self.assertTrue((self.root / "screens/testvm.png").exists())
        self.assertFalse((self.root / "screens/testvm.ppm").exists())

    def test_matrix_report_capture_before_stop_and_restore(self):
        args = cli.build_parser().parse_args(["check-vms", "testvm", "--restore", "--report"])
        events = []
        def capture(name, vm, directory, wake=True):
            events.append("capture")
            (directory / "screens/testvm.png").write_bytes(report.ppm_to_png(b"P6 1 1 255\nabc"))
            return None
        with mock.patch.object(lifecycle, "local_test_clean_candidates", return_value=["testvm"]), \
             mock.patch.object(lifecycle, "stash_local_test_artifacts", return_value={"testvm": "backup"}), \
             mock.patch.object(lifecycle, "restore_local_test_artifacts", side_effect=lambda *a, **k: events.append("restore")), \
             mock.patch.object(lifecycle, "prepare_vm_for_local_test", return_value=(self.vm_config, None)), \
             mock.patch.object(lifecycle, "local_test_prereq_skip", return_value=None), \
             mock.patch.object(lifecycle, "local_test_mode", return_value=("bootstrap-preseed", "test")), \
             mock.patch.object(lifecycle, "cmd_bootstrap_preseed", side_effect=lambda a: events.append("bootstrap")), \
             mock.patch.object(lifecycle, "cmd_stop", side_effect=lambda a: events.append("stop")), \
             mock.patch.object(report, "capture_screenshot", side_effect=capture), \
             mock.patch.object(runtime, "run_output", return_value="fakecommit"):
            self.assertEqual(lifecycle.cmd_test_local(args), 0)
        self.assertEqual(events, ["stop", "bootstrap", "capture", "stop", "restore"])
        from pathlib import Path
        result = json.loads((Path(args._report_dir) / "results/testvm.json").read_text())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["phase"], "post-install")
        self.assertIn("fakecommit", (Path(args._report_dir) / "report.html").read_text())

    def test_parallel_worker_receives_report_directory(self):
        args = argparse.Namespace(timeout=10, dry_run=False, _report_dir=str(self.root / "report"))
        output = '__VMCTL_CHECK_VM_RESULT__{"status":"passed","detail":"ok"}'
        with mock.patch.object(lifecycle.subprocess, "run", return_value=mock.Mock(stdout=output, stderr="", returncode=0)) as run:
            lifecycle.run_local_test_vm_subprocess("testvm", args)
        self.assertEqual(run.call_args.args[0][-2:], ["--report-dir", args._report_dir])

    def test_dry_run_report_no_files_or_browser(self):
        args = cli.build_parser().parse_args(["--dry-run", "check-vms", "testvm", "--report", "--open", "--no-clean-first"])
        with mock.patch.object(lifecycle, "run_local_test_once", return_value=("passed", "dry run")), mock.patch.object(runtime, "run") as run:
            self.assertEqual(lifecycle.cmd_test_local(args), 0)
        self.assertFalse((self.root / "artifacts").exists())
        self.assertTrue(run.call_args.kwargs["dry_run"])

    def test_parallel_matrix_aggregates_worker_records(self):
        args = cli.build_parser().parse_args(["check-vms", "testvm", "--parallel", "2", "--report", "--no-clean-first"])
        def worker(name, args):
            report.record(name, self.vm_config, args, "passed", "ok", 2.5, "boot-check")
            return "passed", "ok", ""
        with mock.patch.object(lifecycle, "run_local_test_vm_subprocess", side_effect=worker), mock.patch.object(runtime, "run_output", return_value="commit"):
            self.assertEqual(lifecycle.cmd_test_local(args), 0)
        from pathlib import Path
        result = json.loads((Path(args._report_dir) / "results/testvm.json").read_text())
        self.assertEqual(result["seconds"], 2.5)
        self.assertEqual(result["status"], "WARN")
        self.assertIn("WARN: 1", (Path(args._report_dir) / "report.html").read_text())

    def test_failure_still_captures_before_stop(self):
        args = argparse.Namespace(timeout=10, dry_run=False)
        events = []
        with mock.patch.object(lifecycle, "prepare_vm_for_local_test", return_value=(self.vm_config, None)), \
             mock.patch.object(lifecycle, "local_test_prereq_skip", return_value=None), \
             mock.patch.object(lifecycle, "local_test_mode", return_value=("bootstrap-preseed", "test")), \
             mock.patch.object(lifecycle, "cmd_bootstrap_preseed", side_effect=VMError("installation failed")), \
             mock.patch.object(report, "capture", side_effect=lambda *a: events.append("capture")), \
             mock.patch.object(lifecycle, "cmd_stop", side_effect=lambda *a: events.append("stop")):
            status, detail = lifecycle.run_local_test_once("testvm", self.vm_config, args)
        self.assertEqual(status, "failed")
        self.assertEqual(events, ["capture", "stop"])
        self.assertEqual(detail, "installation failed")

    def test_report_directory_survives_restore_and_rejects_stale_results(self):
        args = argparse.Namespace(vms=["testvm"], report="artifacts/testvm/report", dry_run=False)
        with self.assertRaisesRegex(VMError, "outside per-VM"):
            report.init(args)
        args.report = "artifacts/check-vms/run"
        report.init(args)
        with self.assertRaisesRegex(VMError, "already contains results"):
            report.init(args)

    def test_failed_post_install_reports_reached_phase(self):
        args = argparse.Namespace(timeout=10, dry_run=False)
        def bootstrap(child_args):
            report.phase(child_args, "post-install")
            raise VMError("SSH failed")
        with mock.patch.object(lifecycle, "prepare_vm_for_local_test", return_value=(self.vm_config, None)), \
             mock.patch.object(lifecycle, "local_test_prereq_skip", return_value=None), \
             mock.patch.object(lifecycle, "local_test_mode", return_value=("bootstrap-preseed", "test")), \
             mock.patch.object(lifecycle, "cmd_bootstrap_preseed", side_effect=bootstrap), \
             mock.patch.object(lifecycle, "cmd_stop"):
            status, _ = lifecycle.run_local_test_once("testvm", self.vm_config, args)
        self.assertEqual(status, "failed")
        self.assertEqual(args._report_phase, "post-install")
