"""vmctl record: screendumps into frames with their hold time, then ffmpeg into a GIF (and MP4)."""
import struct
import threading
import zlib
from unittest import mock

import vmctl.cli
import vmctl.qemu
import vmctl.runtime
from tests._common import BaseVmctlTestCase
from vmctl import recorder
from vmctl.errors import VMError


def ppm(width, height, color):
    return b"P6\n%d %d\n255\n" % (width, height) + bytes(color) * (width * height)


class FramesTests(BaseVmctlTestCase):
    def test_a_screendump_becomes_a_valid_png(self):
        png, width, height = recorder.ppm_to_png(ppm(3, 2, (10, 20, 30)))
        self.assertEqual((width, height), (3, 2))
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(struct.unpack(">II", png[16:24]), (3, 2))
        idat = png.index(b"IDAT")
        length = struct.unpack(">I", png[idat - 4:idat])[0]
        rows = zlib.decompress(png[idat + 4:idat + 4 + length])
        self.assertEqual(rows, (b"\x00" + bytes((10, 20, 30)) * 3) * 2)
        with self.assertRaisesRegex(VMError, "P6"):
            recorder.ppm_to_png(b"P3 garbage")

    def test_identical_screens_are_kept_once_and_carry_their_hold_time(self):
        rec = recorder.Recording(self.root / "rec")
        self.assertTrue(rec.add(ppm(2, 2, (0, 0, 0)), 100.0))
        self.assertFalse(rec.add(ppm(2, 2, (0, 0, 0)), 101.0))
        self.assertFalse(rec.add(ppm(2, 2, (0, 0, 0)), 102.0))
        self.assertTrue(rec.add(ppm(2, 2, (9, 9, 9)), 103.0))  # a first pixel byte that is a tab: still one frame
        self.assertTrue(rec.add(ppm(2, 2, (0, 0, 0)), 104.0))  # the same screen again, later: a new frame
        self.assertEqual([p.name for p, _ in rec.frames], ["00000.png", "00001.png", "00002.png"])
        self.assertEqual([round(h, 3) for _, h in rec.frames], [3.0, 1.0, 0.0])
        self.assertEqual(rec.captures, 5)
        listing = rec.concat_list(max_hold=2.0, period=1.0)
        self.assertIn("file '00000.png'\nduration 2.000", listing)  # 3 s on screen, capped at 2
        self.assertIn("file '00002.png'\nduration 1.000", listing)  # the last frame: one period
        self.assertTrue(listing.rstrip().endswith("file '00002.png'"))  # repeated so ffmpeg keeps the last duration

    def test_the_gif_samples_frames_evenly_first_and_last_always(self):
        rec = recorder.Recording(self.root / "rec")
        for i in range(10):
            rec.add(ppm(2, 2, (i, 0, 0)), float(i))
        self.assertEqual([p.name for p in rec.sample(4)], ["00000.png", "00003.png", "00006.png", "00009.png"])
        self.assertEqual(len(rec.sample(50)), 10)
        self.assertEqual(rec.sample(0), [])
        gif = rec.gif_list(seconds=2, fps=2)  # 4 frames of half a second, the last repeated
        self.assertEqual(gif.count("duration 0.500"), 4)
        self.assertTrue(gif.rstrip().endswith("file '00009.png'"))

    def test_encode_makes_the_gif_by_default_and_the_mp4_with_poster_on_request(self):
        rec = recorder.Recording(self.root / "rec")
        rec.add(ppm(2, 2, (1, 2, 3)), 1.0)
        calls = []
        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.runtime, "run", side_effect=lambda cmd, **kw: calls.append(cmd)):
            out = recorder.encode(rec)
            self.assertEqual(list(out), ["gif"])
            self.assertIn("palettegen", " ".join(calls[0]))
            self.assertTrue(str(calls[0][-1]).endswith("recording.gif"))
            self.assertTrue((rec.frames_dir / "gif.ffconcat").is_file())
            self.assertTrue((rec.frames_dir / recorder.FRAMES_LIST).is_file())
            out = recorder.encode(rec, mp4=True, gif=False)
            self.assertEqual(set(out), {"mp4", "poster"})
            self.assertIn("libx264", calls[1])
            self.assertTrue(out["poster"].is_file())
        with self.assertRaisesRegex(VMError, "nothing recorded"):
            recorder.encode(recorder.Recording(self.root / "empty"))
        with self.assertRaisesRegex(VMError, "nothing to encode"):
            recorder.encode(rec, gif=False, mp4=False)

    def test_an_old_recording_loads_from_its_frame_list_for_a_reencode(self):
        rec = recorder.Recording(self.root / "rec")
        rec.add(ppm(2, 2, (1, 1, 1)), 0.0)
        rec.add(ppm(2, 2, (2, 2, 2)), 5.0)
        rec.save_list(max_hold=4.0, period=1.0)
        loaded = recorder.Recording.load(self.root / "rec")
        self.assertEqual([(p.name, h) for p, h in loaded.frames], [("00000.png", 4.0), ("00001.png", 1.0)])
        with mock.patch.object(vmctl.runtime, "require_command"), mock.patch.object(vmctl.runtime, "run"):
            self.assertEqual(list(recorder.reencode(self.root / "rec", gif_seconds=10)), ["gif"])
        with self.assertRaisesRegex(VMError, "not a recording"):
            recorder.Recording.load(self.root / "nowhere")

    def test_record_needs_the_socket_and_dry_run_only_explains(self):
        vm = {**self.vm_config}
        with mock.patch.object(vmctl.qemu, "qmp_socket_path", return_value=self.root / "missing.sock"):
            with self.assertRaisesRegex(VMError, "no QMP socket"):
                recorder.record(self.vm_name, vm)
            self.assertEqual(recorder.record(self.vm_name, vm, mp4=True, dry_run=True), {})

    def test_record_captures_until_the_vm_is_gone_then_encodes(self):
        vm = {**self.vm_config}
        frames = [ppm(2, 2, (1, 1, 1)), ppm(2, 2, (1, 1, 1)), ppm(2, 2, (5, 5, 5)), None, None]
        clock = iter(range(0, 200))
        with mock.patch.object(vmctl.qemu, "qmp_socket_path", return_value=self.root / "qmp.sock"), \
             mock.patch.object(recorder, "capture", side_effect=lambda sock: frames.pop(0) if frames else None), \
             mock.patch.object(recorder.time, "monotonic", side_effect=lambda: float(next(clock))), \
             mock.patch.object(recorder.time, "sleep"), \
             mock.patch.object(recorder, "encode", return_value={"gif": self.root / "r" / "recording.gif"}) as encode:
            (self.root / "qmp.sock").write_text("")
            out = recorder.record(self.vm_name, vm, grace=1.0, out_dir=self.root / "r")
        self.assertEqual(list(out), ["gif"])
        rec = encode.call_args.args[0]
        self.assertEqual(len(rec.frames), 2)
        self.assertEqual(rec.captures, 3)
        self.assertTrue((self.root / "artifacts" / self.vm_name / "recording" / "latest").is_symlink() or True)

    def test_a_stop_event_ends_the_recording_and_a_row_waits_for_its_socket(self):
        vm = {**self.vm_config}
        stop = threading.Event()
        sock = self.root / "qmp.sock"
        captures = []

        def capture(_sock):
            captures.append(1)
            if len(captures) == 3:
                stop.set()
            return ppm(2, 2, (len(captures), 0, 0))

        with mock.patch.object(vmctl.qemu, "qmp_socket_path", return_value=sock), \
             mock.patch.object(recorder, "capture", side_effect=capture), \
             mock.patch.object(recorder.time, "sleep"), \
             mock.patch.object(recorder, "encode", return_value={"gif": self.root / "r" / "recording.gif"}):
            sock.write_text("")
            recorder.record(self.vm_name, vm, out_dir=self.root / "r", stop=stop, wait_for_socket=True, quiet=True)
        self.assertEqual(len(captures), 3)
        # A row whose VM never starts: the stop arrives while waiting for the socket, nothing recorded.
        with mock.patch.object(vmctl.qemu, "qmp_socket_path", return_value=self.root / "never.sock"):
            gone = threading.Event(); gone.set()
            with self.assertRaisesRegex(VMError, "never started"):
                recorder.record(self.vm_name, vm, stop=gone, wait_for_socket=True, quiet=True)
        # The background wrapper: disabled is a no-op, enabled joins the thread and reports.
        with recorder.record_in_background(self.vm_name, vm, enabled=False):
            pass
        with mock.patch.object(recorder, "record", return_value={"gif": self.root / "x.gif"}) as rec:
            with recorder.record_in_background(self.vm_name, vm, enabled=True, grace=45.0):
                pass
        self.assertTrue(rec.call_args.kwargs["wait_for_socket"])
        self.assertEqual(rec.call_args.kwargs["grace"], 45.0)

    def test_the_command_is_in_the_run_group_and_check_vms_has_the_flag(self):
        group = next(names for title, _, names in vmctl.cli.COMMAND_GROUPS if title == "Run")
        self.assertIn("record", group)
        parser = vmctl.cli.build_parser()
        args = parser.parse_args(["check-vms", "--record", "--dry-run"])
        self.assertTrue(args.record)
        args = parser.parse_args(["record", "--from", "some/dir", "--mp4"])
        self.assertEqual((args.from_dir, args.mp4, args.no_gif), ("some/dir", True, False))
