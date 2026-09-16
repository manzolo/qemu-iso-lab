import argparse
import json
import os
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

    def test_screenshot_dialog_closes_on_escape_without_scrolling_away(self):
        html = report.render_html(
            [{"id": "vm", "name": "VM", "flow": "f", "status": "PASS", "phase": "p",
              "seconds": 1, "detail": "d", "screenshot": "screens/vm.png"}],
            {"host": "h", "date": "d", "commit": "c"}, self.root)
        self.assertIn("event.key === 'Escape'", html)
        self.assertIn("closeLightbox", html)
        # The dialogs sit at the end of the document: a bare hash jump would scroll the
        # table away, so both directions restore the reader's position.
        self.assertIn("window.scrollTo({ top: y, behavior: 'instant' })", html)
        self.assertIn("Esc closes it", html)

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

    def test_only_a_blank_screen_is_woken(self):
        # A server guest reaches the screenshot minutes after boot, past console blanking: without
        # the nudge the capture is a black rectangle (seen in the Rocky report row). But a key is
        # not neutral on a guest that is showing something - on the Windows 7 desktop it opened the
        # Start menu, and the same Enter would have pressed the default button of the reboot dialog
        # the device installation leaves up - so the nudge waits for a frame that came back blank.
        calls = []
        frame = [b"P6 2 1 255\n" + b"\xff" * 6]

        def fake_qmp(socket_path, command, arguments=None):
            calls.append(command)
            if command == "screendump":
                Path(arguments["filename"]).write_bytes(frame[0])
            return True

        with mock.patch.object(qemu, "qmp_command", side_effect=fake_qmp), \
             mock.patch.object(report.time, "sleep") as sleep:
            self.assertIsNone(report.capture_screenshot("vm", self.vm_config, self.root))
        self.assertEqual(calls, ["screendump"], "a lit screen is kept at once, untouched")
        sleep.assert_not_called()

        calls.clear()
        frame[0] = b"P6 2 1 255\n" + b"\x00" * 6  # blank: now the nudge is worth its risk
        with mock.patch.object(qemu, "qmp_command", side_effect=fake_qmp), \
             mock.patch.object(report.time, "sleep") as sleep:
            self.assertIsNone(report.capture_screenshot("vm", self.vm_config, self.root))
        self.assertEqual(calls[:3], ["screendump", "send-key", "screendump"])
        sleep.assert_called_with(report.CONSOLE_WAKE_DELAY_SEC)

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

    def test_vnc_fallback_when_screendump_is_refused(self):
        # virtio-vga-gl refuses QMP screendump, so those rows used to reach the report with
        # no image at all; the VM's own VNC socket shows the same screen.
        calls = []

        def fake_qmp(socket_path, command, arguments=None):
            calls.append(command)
            return command != "screendump"

        png = report.ppm_to_png(b"P6 2 1 255\n" + b"\xff" * 6)
        with mock.patch.object(qemu, "qmp_command", side_effect=fake_qmp), \
             mock.patch.object(report, "capture_via_vnc", return_value=png) as vnc, \
             mock.patch.object(report.time, "sleep"):
            self.assertIsNone(report.capture_screenshot("vm", self.vm_config, self.root))
        vnc.assert_called_once()
        self.assertEqual((self.root / "screens/vm.png").read_bytes(), png)

        with mock.patch.object(qemu, "qmp_command", side_effect=fake_qmp), \
             mock.patch.object(report, "capture_via_vnc", side_effect=OSError("no socket")), \
             mock.patch.object(report.time, "sleep"):
            error = report.capture_screenshot("vm", self.vm_config, self.root)
        self.assertIn("screendump did not succeed", error)
        self.assertIn("no socket", error)

    def test_row_records_whether_the_declared_agent_answers(self):
        args = argparse.Namespace(dry_run=False, _report_dir=str(self.root), report=str(self.root))
        (self.root / "screens").mkdir(parents=True, exist_ok=True)
        (self.root / "screens/vm.png").write_bytes(report.ppm_to_png(b"P6 2 1 255\n" + b"\xff" * 6))
        self.vm_config["guest_agent"] = True

        # A profile that declares the channel and stays silent is a defect worth seeing.
        with mock.patch.object(report.guest_agent, "responds", return_value=False):
            report.probe_guest_agent(self.vm_config, args)
        report.record("vm", self.vm_config, args, "passed", "installed", 1.0, "flow")
        row = json.loads((self.root / "results/vm.json").read_text())
        self.assertEqual(row["status"], "WARN")
        self.assertIn("guest agent silent", row["detail"])

        with mock.patch.object(report.guest_agent, "responds", return_value=True):
            report.probe_guest_agent(self.vm_config, args)
        report.record("vm", self.vm_config, args, "passed", "installed", 1.0, "flow")
        row = json.loads((self.root / "results/vm.json").read_text())
        self.assertEqual(row["status"], "PASS")
        self.assertIn("guest agent answers", row["detail"])

        # A profile without the channel is not probed and not annotated.
        self.vm_config.pop("guest_agent")
        args._guest_agent_state = None
        with mock.patch.object(report.guest_agent, "responds") as responds:
            report.probe_guest_agent(self.vm_config, args)
        responds.assert_not_called()

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


class ReportHousekeepingTests(BaseVmctlTestCase):
    def make_report(self, relative, rows=1, age_sec=0.0, payload=b"x" * 1024):
        directory = self.root / "artifacts/check-vms" / relative
        (directory / "results").mkdir(parents=True)
        (directory / "screens").mkdir()
        (directory / "screens" / "shot.png").write_bytes(payload)
        for index in range(rows):
            (directory / "results" / f"vm{index}.json").write_text(json.dumps({"id": f"vm{index}"}))
        when = 1_000_000.0 - age_sec
        for path in list(directory.rglob("*")) + [directory]:
            os.utime(path, (when, when))
        return directory

    def test_discovery_finds_nested_reports_and_ignores_everything_else(self):
        old = self.make_report("20260901-000000", age_sec=86400)
        recent = self.make_report("20260902-000000")
        nested = self.make_report("doc-20260914/debian-xfce", age_sec=3600)
        (self.root / "artifacts/check-vms/not-a-report").mkdir()
        found = report.discover_reports(self.root / "artifacts/check-vms")
        self.assertEqual(found, [old, nested, recent])  # oldest first
        # A report's own subdirectories are not separate reports.
        self.assertNotIn(recent / "results", found)

    def test_prune_keeps_the_newest_and_reports_what_it_freed(self):
        base = self.root / "artifacts/check-vms"
        older = self.make_report("20260901-000000", age_sec=4 * 86400)
        old = self.make_report("20260902-000000", age_sec=3 * 86400)
        keeper = self.make_report("20260903-000000", age_sec=2 * 86400)
        removed, active = report.prune_reports(1, base=base, now=1_000_000.0)
        self.assertEqual([directory for directory, _ in removed], [older, old])
        self.assertEqual(active, [])
        self.assertGreater(sum(size for _, size in removed), 2048)
        self.assertFalse(older.exists())
        self.assertTrue(keeper.exists())

    def test_prune_never_removes_a_report_that_is_still_being_written(self):
        base = self.root / "artifacts/check-vms"
        running = self.make_report("20260904-000000", age_sec=60)
        newest = self.make_report("20260905-000000")
        removed, active = report.prune_reports(1, base=base, now=1_000_000.0)
        self.assertEqual(removed, [])
        self.assertEqual(active, [running])
        self.assertTrue(running.exists() and newest.exists())

    def test_older_than_and_dry_run(self):
        base = self.root / "artifacts/check-vms"
        ancient = self.make_report("20260801-000000", age_sec=30 * 86400)
        yesterday = self.make_report("20260913-000000", age_sec=86400)
        self.make_report("20260914-000000", age_sec=7200)
        removed, _ = report.prune_reports(1, 7, base=base, now=1_000_000.0, dry_run=True)
        self.assertEqual([directory for directory, _ in removed], [ancient])
        self.assertTrue(ancient.exists(), "a dry run must not delete anything")
        self.assertTrue(yesterday.exists())
        removed, _ = report.prune_reports(1, 7, base=base, now=1_000_000.0)
        self.assertFalse(ancient.exists())
        self.assertTrue(yesterday.exists(), "younger than --older-than, kept even though it is not the newest")

    def test_emptied_campaign_parent_goes_away_with_its_reports(self):
        base = self.root / "artifacts/check-vms"
        nested = self.make_report("doc-20260914/debian-xfce", age_sec=86400)
        self.make_report("20260914-000000")
        report.prune_reports(1, base=base, now=1_000_000.0)
        self.assertFalse(nested.exists())
        self.assertFalse(nested.parent.exists(), "the campaign directory is empty now")

    def test_command_reports_rows_and_size_without_deleting_on_dry_run(self):
        matrix = self.make_report("20260901-000000", rows=3, age_sec=86400)
        self.make_report("20260914-000000")
        args = argparse.Namespace(keep=1, older_than=None, dry_run=True)
        with mock.patch.object(lifecycle.ui, "print_status") as status:
            self.assertEqual(lifecycle.cmd_clean_reports(args), 0)
        lines = [call.args[1] for call in status.call_args_list]
        self.assertTrue(any("3 rows" in line and str(matrix.name) in line for line in lines), lines)
        self.assertTrue(any(line.startswith("Would free") for line in lines), lines)
        self.assertTrue(matrix.exists())

    def test_finished_report_points_at_the_cleanup_only_when_it_is_worth_it(self):
        base = self.root / "artifacts/check-vms"
        self.make_report("20260914-000000", payload=b"x" * 4096)
        with mock.patch.object(report.ui, "print_note") as note:
            report.warn_reports_size(base, limit=1024 ** 3)
        note.assert_not_called()
        with mock.patch.object(report.ui, "print_note") as note:
            report.warn_reports_size(base, limit=1024)
        self.assertIn("clean-reports", note.call_args.args[0])
