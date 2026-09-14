import argparse
import json
import unittest
from pathlib import Path
from unittest import mock

from _common import BaseVmctlTestCase
import vmctl.errors
import vmctl.lifecycle
import vmctl.profiledoc
import vmctl.report
import vmctl.runtime


def ppm(width: int, height: int, pixel: bytes) -> bytes:
    return b"P6\n%d %d\n255\n" % (width, height) + pixel * (width * height)


class TimelineTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.report_dir = self.root / "artifacts" / "check-vms" / "run"
        (self.report_dir / "results").mkdir(parents=True)

    def test_tick_keeps_changed_frames_skips_black_and_duplicates_and_caps(self):
        frames = [ppm(4, 4, b"\x00\x00\x00"), ppm(4, 4, b"\x10\x80\xff"), ppm(4, 4, b"\x10\x80\xff"),
                  ppm(4, 4, b"\xff\xff\xff"), None]
        timeline = vmctl.report.Timeline("testvm", self.vm_config, self.report_dir, started=100.0)
        kept = []
        with mock.patch.object(vmctl.report, "capture_frame", side_effect=frames):
            for step, phase in enumerate(("install", "install", "install", "post-install", "post-install")):
                kept.append(timeline.tick(phase, now=100.0 + 30 * step))
        # black frame dropped, identical frame dropped, VM gone (None) dropped
        self.assertEqual(kept, [False, True, False, True, False])
        files = sorted(p.name for p in (self.report_dir / "screens" / "testvm").glob("*.png"))
        self.assertEqual(files, ["00030-install.png", "00090-post-install.png"])
        saved = json.loads((self.report_dir / "screens" / "testvm" / "timeline.json").read_text())
        self.assertEqual([(f["elapsed"], f["phase"]) for f in saved], [(30, "install"), (90, "post-install")])
        self.assertTrue(saved[0]["file"].startswith("screens/testvm/"))
        # the cap: a 41st distinct frame is refused and the timeline says so
        timeline.frames = [{"elapsed": i, "phase": "install", "file": f"x{i}.png"} for i in range(vmctl.report.TIMELINE_MAX_FRAMES)]
        with mock.patch.object(vmctl.report, "capture_frame", return_value=ppm(4, 4, b"\x01\x02\x03")):
            self.assertFalse(timeline.tick("install", now=999.0))
        self.assertTrue(timeline.truncated)

    def test_black_screen_falls_back_to_the_serial_console_text(self):
        # d-i, anaconda, pacstrap and AutoYaST work on ttyS0 and leave the framebuffer black: the
        # timeline then keeps the tail of the newest installer log, deduplicated like the frames.
        logs = self.root / "artifacts" / "testvm" / "logs"
        logs.mkdir(parents=True)
        (logs / "check-vms.stdout.log").write_text("parent log, never a frame\n")
        (logs / "bootstrap-serial.log").write_bytes(
            b"[  0 start  2 shell  (3*shell)  ][ Sep 14  5:38 ]\n"          # d-i status bar: noise
            b"[            (1*installer)  2 shell  3 shell  4- log           ][ Sep 14  5:45 ]\n"
            b"[                       (0*start)\n14  5:44 ][             0- start   (2*shell)\n"   # a bar wrapped by \r
            b"\x1b[1mSelect and install software\x1b[0m ... 45%\r\n"
            b"Retrieving file 12 of 400\nRetrieving file 12 of 400\n"       # repainted line: kept once
            b"... 46%\n")                                                   # progress-only redraw: noise
        timeline = vmctl.report.Timeline("testvm", self.vm_config, self.report_dir, started=0.0)
        with mock.patch.object(vmctl.report, "capture_frame", return_value=ppm(4, 4, b"\x00\x00\x00")):
            self.assertTrue(timeline.tick("install", now=30.0))
            self.assertFalse(timeline.tick("install", now=60.0))  # same text: dropped
        (logs / "bootstrap-serial.log").write_bytes(b"Select and install software ... 80%\n")
        with mock.patch.object(vmctl.report, "capture_frame", return_value=None):
            self.assertTrue(timeline.tick("install", now=90.0))
        self.assertEqual([f.get("text") for f in timeline.frames], ["screens/testvm/00030-install.txt", "screens/testvm/00090-install.txt"])
        saved = (self.report_dir / "screens" / "testvm" / "00030-install.txt").read_text()
        self.assertIn("Select and install software ... 45%", saved)
        self.assertNotIn("\x1b", saved)
        self.assertNotIn("parent log", saved)
        self.assertNotIn("0 start", saved)
        self.assertNotIn("(1*installer)", saved)
        self.assertNotIn("(0*start)", saved)
        self.assertNotIn("(2*shell)", saved)
        self.assertEqual(saved.count("Retrieving file 12 of 400"), 1)
        self.assertNotIn("... 46%", saved)
        self.assertEqual(vmctl.profiledoc.phase_label("bootstrap-preseed", "en"), "installer")
        result = {"id": "testvm", "name": "Test VM", "status": "PASS", "seconds": 90, "timeline": timeline.frames}
        text = vmctl.profiledoc.render_markdown(result, self.vm_config, self.report_dir, "it")
        self.assertIn("<pre>Select and install software ... 45%", text)
        self.assertIn("console seriale", text)

    def test_watch_timeline_is_opt_in_and_record_embeds_the_frames(self):
        args = argparse.Namespace(dry_run=False, _report_dir=str(self.report_dir))
        with mock.patch.object(vmctl.report, "capture_frame") as capture:
            with vmctl.report.watch_timeline("testvm", self.vm_config, args):
                pass
        capture.assert_not_called()  # no --document: no traffic at all
        args.document = True
        with mock.patch.object(vmctl.report, "capture_frame", return_value=ppm(4, 4, b"\x10\x80\xff")):
            with vmctl.report.watch_timeline("testvm", self.vm_config, args):
                pass
        frames = vmctl.report.load_timeline(self.report_dir, "testvm")
        self.assertEqual(len(frames), 1)
        # record() carries the timeline into the result JSON the PDF renderer reads
        (self.report_dir / "screens" / "testvm.png").write_bytes(b"png")
        vmctl.report.record("testvm", self.vm_config, args, "passed", "ok", 61.0, "boot-check")
        result = json.loads((self.report_dir / "results" / "testvm.json").read_text())
        self.assertEqual(result["timeline"], frames)
        self.assertEqual(result["status"], "PASS")


class ProfiledocTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.report_dir = self.root / "artifacts" / "check-vms" / "20260914-000000-000000"
        (self.report_dir / "results").mkdir(parents=True)
        (self.report_dir / "screens" / "testvm").mkdir(parents=True)
        (self.report_dir / "screens" / "testvm.png").write_bytes(b"png")
        (self.report_dir / "screens" / "testvm" / "00030-install.png").write_bytes(b"png")
        self.result = {"id": "testvm", "name": "Test VM", "flow": "bootstrap-preseed", "status": "PASS", "phase": "post-install",
                       "seconds": 654.0, "detail": "preseed + post-install; stopped after check-vms",
                       "screenshot": "screens/testvm.png",
                       "timeline": [{"elapsed": 30, "phase": "install", "file": "screens/testvm/00030-install.png"}]}
        (self.report_dir / "results" / "testvm.json").write_text(json.dumps(self.result))
        self.vm_config["ssh_provision"] = {"user": "lab", "ssh_host_port": 2299,
                                           "post_install_run": ["~/bin/verify-desktop --user lab --package xfce4"]}
        self.write_config_dir()

    def test_markdown_carries_facts_outcome_and_captioned_frames_in_both_languages(self):
        for lang, title, after in (("en", "Profile sheet", "after 0:30"), ("it", "Scheda profilo", "dopo 0:30")):
            text = vmctl.profiledoc.render_markdown(self.result, self.vm_config, self.report_dir, lang)
            self.assertIn("# Test VM", text)
            self.assertIn(title, text)
            self.assertIn("2299", text)
            self.assertIn("verify-desktop --user lab --package xfce4", text)
            self.assertIn('<span class="PASS">PASS</span>', text)
            self.assertIn("10.9", text)  # 654 s
            self.assertIn('src="screens/testvm/00030-install.png"', text)
            self.assertIn(after, text)
            self.assertIn('src="screens/testvm.png"', text)

    def test_build_writes_markdown_and_renders_one_pdf_per_profile_plus_index(self):
        with mock.patch.object(vmctl.profiledoc, "to_pdf") as to_pdf:
            written = vmctl.profiledoc.build(self.report_dir, ("en", "it"))
        names = sorted(str(p.relative_to(self.report_dir)) for p in written)
        self.assertEqual(names, ["pdf/en/index.md", "pdf/en/index.pdf", "pdf/en/testvm.md", "pdf/en/testvm.pdf",
                                 "pdf/it/index.md", "pdf/it/index.pdf", "pdf/it/testvm.md", "pdf/it/testvm.pdf"])
        self.assertTrue((self.report_dir / "pdf" / "it" / "testvm.md").exists())
        self.assertEqual(to_pdf.call_count, 4)
        # every PDF resolves its images against the report directory
        self.assertTrue(all(call.args[1] == self.report_dir for call in to_pdf.call_args_list))
        index = (self.report_dir / "pdf" / "en" / "index.md").read_text()
        self.assertIn('href="testvm.pdf"', index)
        self.assertIn("PASS 1", index)

    def test_only_writes_one_sheet_and_leaves_the_shared_index_alone(self):
        with mock.patch.object(vmctl.profiledoc, "to_pdf") as to_pdf:
            written = vmctl.profiledoc.build(self.report_dir, ("en", "it"), only="testvm")
        names = sorted(str(p.relative_to(self.report_dir)) for p in written)
        self.assertEqual(names, ["pdf/en/testvm.md", "pdf/en/testvm.pdf", "pdf/it/testvm.md", "pdf/it/testvm.pdf"])
        self.assertEqual(to_pdf.call_count, 2)
        self.assertFalse((self.report_dir / "pdf" / "en" / "index.md").exists(),
                         "parallel rows must not race on the index; the closing build writes it")
        with mock.patch.object(vmctl.profiledoc, "to_pdf"):
            self.assertEqual(vmctl.profiledoc.build(self.report_dir, ("en",), only="absent"), [])

    def test_a_finished_row_writes_its_own_sheet_only_with_document(self):
        args = argparse.Namespace(dry_run=False, document=True, _report_dir=str(self.report_dir))
        with mock.patch.object(vmctl.profiledoc, "build") as build:
            vmctl.lifecycle.write_profile_sheet("testvm", args)
        build.assert_called_once()
        self.assertEqual(build.call_args.kwargs["only"], "testvm")
        for missing in (argparse.Namespace(dry_run=False, document=False, _report_dir=str(self.report_dir)),
                        argparse.Namespace(dry_run=True, document=True, _report_dir=str(self.report_dir)),
                        argparse.Namespace(dry_run=False, document=True, _report_dir=None)):
            with mock.patch.object(vmctl.profiledoc, "build") as build:
                vmctl.lifecycle.write_profile_sheet("testvm", missing)
            build.assert_not_called()

    def test_a_missing_pdf_toolchain_does_not_fail_the_row(self):
        args = argparse.Namespace(dry_run=False, document=True, _report_dir=str(self.report_dir))
        with mock.patch.object(vmctl.profiledoc, "build", side_effect=vmctl.errors.VMError("weasyprint missing")):
            with mock.patch.object(vmctl.lifecycle.ui, "print_note") as note:
                vmctl.lifecycle.write_profile_sheet("testvm", args)
        self.assertIn("weasyprint missing", note.call_args.args[0])

    def test_build_rejects_unknown_languages_and_non_report_directories(self):
        with self.assertRaisesRegex(vmctl.errors.VMError, "Unknown language"):
            vmctl.profiledoc.build(self.report_dir, ("de",))
        with self.assertRaisesRegex(vmctl.errors.VMError, "no results/"):
            vmctl.profiledoc.build(self.root / "artifacts" / "nope", ("en",))

    def test_dry_run_lists_without_writing(self):
        with mock.patch.object(vmctl.profiledoc, "to_pdf") as to_pdf:
            written = vmctl.profiledoc.build(self.report_dir, ("en",), dry_run=True)
        self.assertEqual(len(written), 4)
        to_pdf.assert_not_called()
        self.assertFalse((self.report_dir / "pdf").exists())

    def test_latest_report_dir_picks_the_newest_with_results(self):
        older = self.root / "artifacts" / "check-vms" / "20260913-000000-000000"
        (older / "results").mkdir(parents=True)
        empty = self.root / "artifacts" / "check-vms" / "20260915-000000-000000"
        empty.mkdir(parents=True)  # no results: not a finished report
        self.assertEqual(vmctl.report.latest_report_dir(), self.report_dir)

    def test_check_vms_document_needs_report_and_reaches_the_workers(self):
        args = argparse.Namespace(vms=["testvm"], timeout=5, parallel="1", dry_run=True, clean_first=False,
                                  no_clean_first=True, restore=False, report=None, open=False, document=True)
        with self.assertRaisesRegex(vmctl.errors.VMError, "--document needs --report"):
            vmctl.lifecycle.cmd_test_local(args)
        worker_args = argparse.Namespace(timeout=5, dry_run=True, _report_dir=str(self.report_dir), document=True)
        with mock.patch.object(vmctl.lifecycle.subprocess, "run") as run:
            run.return_value = mock.Mock(stdout="__VMCTL_CHECK_VM_RESULT__" + json.dumps({"vm": "testvm", "status": "passed", "detail": "ok"}) + "\n",
                                         stderr="", returncode=0)
            vmctl.lifecycle.run_local_test_vm_subprocess("testvm", worker_args)
        cmd = run.call_args.args[0]
        self.assertIn("--document", cmd)
        self.assertIn("--report-dir", cmd)


if __name__ == "__main__":
    unittest.main()
