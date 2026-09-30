"""vmctl media-check: the verdict, the firmware messages, the scratch profile and check-vms --media."""
import argparse
import json
import unittest

from tests._common import ROOT
from vmctl import config, lifecycle, mediacheck


class MediaCheckTests(unittest.TestCase):
    def tracked(self, name):
        return json.loads(json.dumps(config.load_tracked(ROOT / "vms" / "profiles")[name]))

    def test_verdict(self):
        self.assertFalse(mediacheck.classify(3, 1, True, None, "BdsDxe: failed to load Boot0001", 20)[0])
        self.assertFalse(mediacheck.classify(3, 1, True, "QEMU exited after 4 s (status 1)", None, 4)[0])
        self.assertTrue(mediacheck.classify(2, 1, True, None, None, 46)[0])
        self.assertTrue(mediacheck.classify(mediacheck.TEXT_BOOT_FRAMES, 0, False, None, None, 180)[0])
        passed, detail = mediacheck.classify(2, 0, False, None, None, 180)
        self.assertFalse(passed)
        self.assertIn("never got past the firmware", detail)

    def test_firmware_lines_are_whole_and_free_of_escape_sequences(self):
        raw = '\x1b[2J\x1b[001;001H\x1b[=3hBdsDxe: failed to load Boot0001 "UEFI QEMU DVD-ROM": Not Found\r\n'
        self.assertEqual(mediacheck.failure_line(raw, mediacheck.FIRMWARE_FAILURES),
                         'BdsDxe: failed to load Boot0001 "UEFI QEMU DVD-ROM": Not Found')
        self.assertIsNone(mediacheck.failure_line("BdsDxe: failed to lo", mediacheck.FIRMWARE_FAILURES))  # not complete yet
        seabios = "Booting from DVD/CD...\nBoot failed: Could not read from CDROM (code 0004)\nBooting from ROM...\n"
        self.assertEqual(mediacheck.failure_line(seabios, mediacheck.FIRMWARE_FAILURES),
                         "Boot failed: Could not read from CDROM (code 0004)")
        self.assertEqual(mediacheck.failure_line("Booting from ROM...\n", mediacheck.FIRMWARE_FAILURES + mediacheck.SEABIOS_FALLTHROUGH),
                         "Booting from ROM...")

    def test_eligible_profiles_and_the_scratch_profile_never_touch_the_vm_disk(self):
        live = self.tracked("debian-gnome-live")
        self.assertTrue(mediacheck.eligible(live))
        self.assertFalse(mediacheck.eligible(self.tracked("debian-server")))  # unattended: the matrix installs it
        scratch = mediacheck.scratch_profile("debian-gnome-live", live)
        base = str(mediacheck.work_dir("debian-gnome-live"))
        self.assertTrue(scratch["disk"]["path"].startswith(base))
        if (scratch.get("firmware") or {}).get("vars_path"):
            self.assertTrue(scratch["firmware"]["vars_path"].startswith(base))
        self.assertNotIn("shared_dir", scratch)
        self.assertEqual(live["disk"]["path"], self.tracked("debian-gnome-live")["disk"]["path"])  # the original is untouched

    def test_check_vms_media_turns_the_skip_of_a_manual_profile_into_a_media_check(self):
        live = self.tracked("debian-gnome-live")
        self.assertEqual(lifecycle.row_mode(live, argparse.Namespace())[0], "skip")
        self.assertEqual(lifecycle.row_mode(live, argparse.Namespace(media=True))[0], "media-check")
        server = self.tracked("debian-server")
        self.assertEqual(lifecycle.row_mode(server, argparse.Namespace(media=True)), lifecycle.local_test_mode(server))


if __name__ == "__main__":
    unittest.main()
