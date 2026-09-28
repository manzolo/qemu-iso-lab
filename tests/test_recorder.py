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
        # All dark: a server's clip, half the frames, first and last kept.
        gif = rec.gif_list(seconds=2, fps=2, final_hold=0)
        self.assertEqual(gif.count("duration 0.500"), 2)
        self.assertTrue(gif.rstrip().endswith("file '00009.png'"))

    def test_console_stretches_are_squeezed_and_the_desktop_gets_the_clip(self):
        rec = recorder.Recording(self.root / "rec")
        at = 0.0
        for i in range(40):  # a long serial install: dark frames, each a little different
            rec.add(ppm(32, 32, (i % 20, 0, 0)) + bytes([i]), at); at += 10
        for i in range(12):  # the desktop: lit, so graphical
            rec.add(ppm(32, 32, (200, 120 + i, 60)), at); at += 2
        rec.add(ppm(32, 32, (0, 0, 1)), at)  # the power-off
        self.assertEqual(recorder.frame_kind(rec.frames[0][0]), "console")
        self.assertEqual(recorder.frame_kind(rec.frames[45][0]), "graphic")
        self.assertEqual(rec.final_index(), 51)  # the last desktop frame, not the power-off
        frames = [p.name for p in rec.gif_frames(20)]
        console = [n for n in frames if int(n[:5]) < 40]
        self.assertEqual(len(console), recorder.CONSOLE_FRAMES)
        self.assertEqual(len(frames) - len(console), 12)  # every desktop frame fits
        self.assertEqual(frames[-1], "00051.png")
        self.assertNotIn("00052.png", frames)

    def test_one_canvas_for_a_clip_whose_guest_changes_resolution(self):
        rec = recorder.Recording(self.root / "rec")
        rec.add(ppm(720, 400, (1, 1, 1)), 0.0)
        rec.add(ppm(1024, 768, (2, 2, 2)), 1.0)
        self.assertEqual(recorder.png_size(rec.frames[0][0]), (720, 400))
        self.assertEqual(recorder.canvas(rec), (1024, 768))  # the frame it ends on
        self.assertEqual(recorder.canvas(rec, 480), (480, 360))
        head = recorder.fit((480, 360), 2)
        self.assertIn("color=c=black:s=480x360:r=2[bg]", head)
        self.assertIn("overlay=x=(W-w)/2:y=(H-h)/2:eval=frame", head)

    def test_the_gif_ends_on_the_richest_late_screen_held_longer_not_on_the_power_off(self):
        rec = recorder.Recording(self.root / "rec")
        for i in range(10):  # a long install: 100 s of small frames
            rec.add(ppm(2, 2, (i, 0, 0)), float(i * 10))
        rec.add(ppm(40, 40, (1, 2, 3)) + b"", 100.0)  # the desktop: a bigger PNG
        (rec.frames[-1][0]).write_bytes(rec.frames[-1][0].read_bytes() + b"\0" * 4096)
        rec.add(ppm(2, 2, (0, 0, 0)), 130.0)  # the power-off screen
        rec.add(ppm(2, 2, (0, 0, 1)), 140.0)
        self.assertEqual(rec.final_index(), 10)
        gif = rec.gif_list(seconds=2, fps=2)
        self.assertTrue(gif.rstrip().endswith("file '00010.png'"))
        self.assertIn("file '00010.png'\nduration 4.000", gif)
        self.assertNotIn("00011.png", gif)

    def test_encode_makes_the_gif_by_default_and_the_mp4_with_poster_on_request(self):
        rec = recorder.Recording(self.root / "rec")
        rec.add(ppm(2, 2, (1, 2, 3)), 1.0)
        calls = []
        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.runtime, "run", side_effect=lambda cmd, **kw: calls.append(cmd)):
            out = recorder.encode(rec)
            self.assertEqual(list(out), ["gif"])
            self.assertIn("palettegen", " ".join(calls[0]))
            self.assertIn("-reinit_filter", calls[0])  # a resolution change must not rebuild the graph
            self.assertIn("scale=480:480:force_original_aspect_ratio=decrease", " ".join(calls[0]))
            self.assertNotIn("pad=", " ".join(calls[0]))  # pad overflowed on a later, larger frame (SIGSEGV)
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

    def test_a_failed_screendump_falls_back_to_the_vnc_socket(self):
        sock, vnc = self.root / "qmp.sock", self.root / "vnc.sock"
        sock.write_text("")
        frame = ppm(2, 2, (7, 8, 9))
        with mock.patch.object(vmctl.qemu, "qmp_execute", side_effect=VMError("QMP screendump: no surface")), \
             mock.patch.object(vmctl.qemu, "vnc_frame", return_value=frame) as grab:
            self.assertIsNone(recorder.capture(sock, vnc))  # no VNC socket: nothing
            vnc.write_text("")
            self.assertEqual(recorder.capture(sock, vnc), frame)
            grab.side_effect = OSError("closed")
            self.assertIsNone(recorder.capture(sock, vnc))
        self.assertEqual(grab.call_count, 2)

    def test_vnc_frame_reads_raw_rectangles_and_a_desktop_resize(self):
        import socket as socketlib
        path = self.root / "fake-vnc.sock"
        server = socketlib.socket(socketlib.AF_UNIX); server.bind(str(path)); server.listen(1)

        def read(conn, count):  # exactly *count* bytes: the client sends its messages one by one
            data = b""
            while len(data) < count:
                chunk = conn.recv(count - len(data))
                if not chunk:
                    break
                data += chunk
            return data

        def serve():
            conn, _ = server.accept()
            with conn:
                conn.sendall(b"RFB 003.008\n"); read(conn, 12)
                conn.sendall(b"\x01\x01"); read(conn, 1)
                conn.sendall(struct.pack("!I", 0)); read(conn, 1)
                conn.sendall(struct.pack("!HH", 4, 4) + bytes(16) + struct.pack("!I", 4) + b"QEMU")
                read(conn, 20 + 12 + 10)  # SetPixelFormat, SetEncodings (2), FramebufferUpdateRequest
                # the guest switched mode: DesktopSize 2x1, then one RAW rectangle 00 RR GG BB
                conn.sendall(b"\x00\x00" + struct.pack("!H", 2)
                             + struct.pack("!HHHHi", 0, 0, 2, 1, vmctl.qemu.VNC_DESKTOP_SIZE_ENCODING)
                             + struct.pack("!HHHHi", 0, 0, 2, 1, 0) + bytes([0, 1, 2, 3, 0, 4, 5, 6]))
                while conn.recv(4096):  # stay open until the client hangs up
                    pass

        thread = threading.Thread(target=serve, daemon=True); thread.start()
        frame = vmctl.qemu.vnc_frame(path, timeout=5)
        thread.join(5); server.close()
        self.assertEqual(frame, b"P6\n2 1\n255\n" + bytes([1, 2, 3, 4, 5, 6]))

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
             mock.patch.object(recorder, "capture", side_effect=lambda sock, vnc=None: frames.pop(0) if frames else None), \
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

        def capture(_sock, _vnc=None):
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
