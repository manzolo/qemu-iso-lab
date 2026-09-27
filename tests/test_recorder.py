"""vmctl record: screendumps into frames with their hold time, then ffmpeg into MP4/GIF."""
import struct
import zlib
from pathlib import Path
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
        self.assertTrue(rec.add(ppm(2, 2, (9, 9, 9)), 103.0))
        self.assertTrue(rec.add(ppm(2, 2, (0, 0, 0)), 104.0))  # the same screen again, later: a new frame
        self.assertEqual([p.name for p, _ in rec.frames], ["00000.png", "00001.png", "00002.png"])
        self.assertEqual([round(h, 3) for _, h in rec.frames], [3.0, 1.0, 0.0])
        self.assertEqual(rec.captures, 5)
        listing = rec.concat_list(max_hold=2.0, period=1.0)
        self.assertIn("file '00000.png'\nduration 2.000", listing)  # 3 s on screen, capped at 2
        self.assertIn("file '00001.png'\nduration 1.000", listing)
        self.assertIn("file '00002.png'\nduration 1.000", listing)  # the last frame: one period
        self.assertTrue(listing.rstrip().endswith("file '00002.png'"))  # repeated so ffmpeg keeps the last duration

    def test_encode_runs_ffmpeg_for_the_mp4_the_poster_and_the_gif(self):
        rec = recorder.Recording(self.root / "rec")
        rec.add(ppm(2, 2, (1, 2, 3)), 1.0)
        calls = []
        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.runtime, "run", side_effect=lambda cmd, **kw: calls.append(cmd)):
            out = recorder.encode(rec, max_hold=4.0, period=1.0, gif=True)
        self.assertEqual(set(out), {"mp4", "poster", "gif"})
        self.assertEqual(calls[0][:1], ["ffmpeg"])
        self.assertIn("libx264", calls[0])
        self.assertTrue(str(calls[0][-1]).endswith("recording.mp4"))
        self.assertIn("palettegen", " ".join(calls[1]))
        self.assertTrue(str(calls[1][-1]).endswith("recording.gif"))
        self.assertTrue(out["poster"].is_file())
        self.assertTrue((rec.frames_dir / "frames.ffconcat").is_file())
        with self.assertRaisesRegex(VMError, "nothing recorded"):
            recorder.encode(recorder.Recording(self.root / "empty"), 4.0, 1.0, False)

    def test_record_needs_the_socket_and_dry_run_only_explains(self):
        vm = {**self.vm_config}
        with mock.patch.object(vmctl.qemu, "qmp_socket_path", return_value=self.root / "missing.sock"):
            with self.assertRaisesRegex(VMError, "no QMP socket"):
                recorder.record(self.vm_name, vm)
            self.assertEqual(recorder.record(self.vm_name, vm, gif=True, dry_run=True), {})

    def test_record_captures_until_the_vm_is_gone_then_encodes(self):
        vm = {**self.vm_config}
        frames = [ppm(2, 2, (1, 1, 1)), ppm(2, 2, (1, 1, 1)), ppm(2, 2, (5, 5, 5)), None, None]
        clock = iter(range(0, 200))
        with mock.patch.object(vmctl.qemu, "qmp_socket_path", return_value=self.root / "qmp.sock"), \
             mock.patch.object(recorder, "capture", side_effect=lambda sock: frames.pop(0) if frames else None), \
             mock.patch.object(recorder.time, "monotonic", side_effect=lambda: float(next(clock))), \
             mock.patch.object(recorder.time, "sleep"), \
             mock.patch.object(recorder, "encode", return_value={"mp4": self.root / "r" / "recording.mp4"}) as encode:
            (self.root / "qmp.sock").write_text("")
            out = recorder.record(self.vm_name, vm, grace=1.0, out_dir=self.root / "r")
        self.assertEqual(list(out), ["mp4"])
        rec = encode.call_args.args[0]
        self.assertEqual(len(rec.frames), 2)
        self.assertEqual(rec.captures, 3)

    def test_the_command_is_in_the_run_group(self):
        group = next(names for title, _, names in vmctl.cli.COMMAND_GROUPS if title == "Run")
        self.assertIn("record", group)
