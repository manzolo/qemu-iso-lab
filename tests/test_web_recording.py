"""Record at the chosen rate and export GIF at 1 fps or MP4 at the capture rate."""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from vmctl import recorder, web_recording
from vmctl.errors import VMError


FRAME = b"P6\n16 16\n255\n" + bytes([190, 60, 30]) * 256


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = web_recording.Recordings()

    def start(self, fps=1):
        with mock.patch.object(recorder, "capture", return_value=FRAME), \
             mock.patch.object(web_recording.shutil, "which", return_value="/usr/bin/ffmpeg"):
            info = self.manager.start("test", self.root / "qmp.sock", self.root, fps)
        session = self.manager.get(info["id"])
        self.addCleanup(session.stop)
        return session

    def test_stop_keeps_frames_for_both_download_formats(self):
        session = self.start()
        with self.assertRaisesRegex(VMError, "Stop"):
            session.export("mp4")
        info = session.stop()
        self.assertEqual(info["status"], "stopped")
        self.assertEqual(info["frames"], 1)
        listing = session.recording.frames_dir / recorder.FRAMES_LIST
        self.assertIn("duration 1.000", listing.read_text())
        with self.assertRaisesRegex(VMError, "GIF or MP4"):
            session.export("../file")

    def test_missing_ffmpeg_or_display_does_not_create_session(self):
        with mock.patch.object(web_recording.shutil, "which", return_value=None):
            with self.assertRaisesRegex(VMError, "ffmpeg"):
                self.manager.start("test", self.root / "sock", self.root)
        with mock.patch.object(web_recording.shutil, "which", return_value="ffmpeg"), \
             mock.patch.object(recorder, "capture", return_value=None):
            with self.assertRaisesRegex(VMError, "No screen"):
                self.manager.start("test", self.root / "sock", self.root)
        self.assertFalse(self.manager.sessions)

    def test_duplicate_recording_is_rejected_and_unknown_ids_are_not_paths(self):
        session = self.start()
        with mock.patch.object(web_recording.shutil, "which", return_value="ffmpeg"):
            with self.assertRaisesRegex(VMError, "already"):
                self.manager.start("test", self.root / "sock", self.root)
        with self.assertRaisesRegex(VMError, "not found"):
            self.manager.get("../../etc/passwd")
        session.stop()

    def test_frame_rate_validation(self):
        for fps in (0, 60, "10", True, None):
            with self.subTest(fps=fps), self.assertRaisesRegex(VMError, "frame rate"):
                self.manager.start("test", self.root / "sock", self.root, fps)

    def test_ten_fps_captures_multiple_frames_within_one_second(self):
        with mock.patch.object(recorder, "capture", return_value=FRAME):
            session = self.start(fps=10)
            deadline = time.monotonic() + .9
            while session.info()["frames"] < 3 and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertGreaterEqual(session.stop()["frames"], 3)

    def test_disconnect_and_capture_errors_finish_with_downloadable_frames(self):
        for frame in (None, OSError("display unavailable")):
            with self.subTest(frame=frame):
                session = self.start()
                with mock.patch.object(recorder, "capture", side_effect=frame if isinstance(frame, Exception) else lambda _: None):
                    session.thread.join(timeout=3)
                self.assertFalse(session.thread.is_alive())
                self.assertEqual(session.info()["status"], "stopped")
                self.assertTrue(session.recording.frames)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg and ffprobe")
    def test_real_gif_and_mp4_keep_still_frame_duration(self):
        session = self.start(fps=10)
        session.stop()
        # An immediate stop must still produce a nonempty GIF at the lower export rate.
        self.assertTrue(session.export("gif").startswith(b"GIF"))
        first = session.recording.frames[0][0]
        session.recording.frames = [(first, 3.0)]
        # A second frame verifies that the GIF does not sample/compress the timeline.
        session.recording.add(b"P6\n16 16\n255\n" + bytes([10, 200, 70]) * 256, session.started)
        session.recording.frames = [(first, 3.0), (session.recording.frames[-1][0], 2.0)]
        for kind in ("gif", "mp4"):
            output = self.root / f"download.{kind}"
            output.write_bytes(session.export(kind))
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(output)],
                                   check=True, capture_output=True, text=True)
            data = json.loads(probe.stdout)
            self.assertGreaterEqual(float(data["format"]["duration"]), 5)
            self.assertEqual(data["streams"][0]["codec_name"], "gif" if kind == "gif" else "h264")
            self.assertEqual(data["streams"][0]["width"], 16)
            if kind == "mp4":
                self.assertEqual(data["streams"][0]["avg_frame_rate"], "10/1")
