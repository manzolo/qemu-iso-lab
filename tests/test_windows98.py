import configparser
import struct
from pathlib import Path
from unittest import mock

from _common import BaseVmctlTestCase
import vmctl.errors
import vmctl.lifecycle
import vmctl.runtime
import vmctl.windows98
from vmctl import cli


CONFIG_SYS = "[menu]\r\nmenuitem=SETUP_CD, Setup\r\nmenudefault=SETUP_CD,30\r\n[SETUP_CD]\r\n"


def bootable_iso(path: Path, image_lba: int = 21, media: int = 2) -> Path:
    """A file shaped like a bootable CD: a boot record, a catalog, and the image it points at."""
    data = bytearray(2048 * (image_lba + 800))
    data[16 * 2048] = 1                                   # primary volume descriptor
    data[16 * 2048 + 1:16 * 2048 + 6] = b"CD001"
    data[17 * 2048] = 0                                   # boot record
    data[17 * 2048 + 1:17 * 2048 + 6] = b"CD001"
    data[17 * 2048 + 0x47:17 * 2048 + 0x4B] = struct.pack("<I", 20)
    data[18 * 2048] = 255
    data[18 * 2048 + 1:18 * 2048 + 6] = b"CD001"
    entry = 20 * 2048 + 32                                # the default entry of the boot catalog
    data[entry + 1] = media
    data[entry + 8:entry + 12] = struct.pack("<I", image_lba)
    path.write_bytes(bytes(data))
    return path


class Windows98Tests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.vm_config.update({
            "disk": {"path": "artifacts/testvm/disk.qcow2", "format": "qcow2", "size": "2G", "interface": "ide"},
            "firmware": {"type": "bios"},
            "machine": "pc",
            "cpus": 1,
            "memory_mb": 256,
            "windows98_config": {"computer_name": "WIN98-LAB", "product_key": "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"},
        })

    def test_dos_files_are_written_with_crlf(self):
        autoexec = vmctl.windows98.render_autoexec(self.vm_config)
        # every line ends CRLF: DOS ignores a batch file with Unix line endings
        self.assertEqual(autoexec.count(b"\n"), autoexec.count(b"\r\n"))
        self.assertGreater(autoexec.count(b"\r\n"), 10)
        self.assertNotIn(b"echo ==>", autoexec, "a bare '>' redirects, in DOS too")
        self.assertIn(b"SETUP.EXE %CDROM%\\MSBATCH.INF /IS /IE /NF", autoexec)
        # Microsoft's MBR never returns to the BIOS: with it on the disk the CD wins every reboot
        # and Setup starts over, so the floppy must leave sector 0 alone.
        self.assertNotIn(b"FDISK", autoexec)

    def test_the_cd_menu_is_answered_in_advance(self):
        patched = vmctl.windows98.render_config_sys(CONFIG_SYS).decode("cp850")
        self.assertIn("menudefault=SETUP_CD,0", patched)
        self.assertNotIn(",30", patched)
        with self.assertRaisesRegex(vmctl.errors.VMError, "menudefault"):
            vmctl.windows98.render_config_sys("[menu]\r\nmenuitem=x\r\n")

    def test_answer_file_carries_the_key_under_the_name_windows_98_reads(self):
        text = vmctl.windows98.render_msbatch("testvm", self.vm_config)
        answer = configparser.ConfigParser(interpolation=None)
        answer.read_string(text)
        # "ID prodotto ... (facoltativo)" in the help of Microsoft's own Batch 98 on the CD
        self.assertEqual(answer["Setup"]["ProductID"], "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE")
        self.assertNotIn("ProductKey", text, "that is the later Windows spelling")
        # [Network] holds the computer name: with Network=0 Setup finds it missing and, installing
        # from MS-DOS, asks for what is missing - the user information page that stopped every run.
        self.assertEqual(answer["Setup"]["Network"], "1")
        self.assertEqual(answer["Network"]["ComputerName"], '"WIN98-LAB"')
        self.assertEqual(answer["NameAndOrg"]["Name"], '"Lab User"')
        self.assertEqual(answer["NameAndOrg"]["Display"], "0")
        self.assertEqual(answer["Setup"]["Express"], "1")
        self.assertEqual(answer["Install"]["AddReg"], "VmctlRunOnce")

    def test_answer_file_does_not_invent_a_product_key(self):
        for key in (None, "", "   "):
            with self.subTest(key=key):
                self.vm_config["windows98_config"]["product_key"] = key
                text = vmctl.windows98.render_msbatch("testvm", self.vm_config)
                self.assertNotIn("ProductKey=", text)
                self.assertNotIn("ProductID=", text)

    def test_the_first_logon_script_escapes_its_token_and_powers_the_guest_off(self):
        script = vmctl.windows98.render_setup_script(self.vm_config).decode("cp850")
        self.assertIn("echo ==^> Windows 98 installation complete!> COM1", script)
        self.assertNotIn("echo ==>", script)
        self.assertTrue(script.rstrip().endswith("rundll32.exe user.exe,exitwindows"))

    def test_boot_image_is_found_through_the_catalog(self):
        iso = bootable_iso(self.root / "w98.iso")
        self.assertEqual(vmctl.windows98.boot_image_extent(iso), (21 * 2048, 1440 * 1024))
        plain = self.root / "plain.iso"
        plain.write_bytes(bytes(2048 * 20))          # no volume descriptors at all
        with self.assertRaisesRegex(vmctl.errors.VMError, "No El Torito"):
            vmctl.windows98.boot_image_extent(plain)

    def test_prepared_disk_is_active_fat32_with_boot_code_that_steps_aside(self):
        raw = self.root / "artifacts/testvm/disk.raw"

        written = {}

        def fake_run(command, **kwargs):
            if command[0] == "qemu-img":   # the conversion is the last step: read the raw image here
                written["raw"] = Path(command[-2]).read_bytes()
                Path(command[-1]).write_bytes(b"qcow2")

        with mock.patch.object(vmctl.runtime, "run", side_effect=fake_run), \
             mock.patch.object(vmctl.runtime, "require_command"):
            vmctl.windows98.prepare_disk(self.vm_config)
        self.assertFalse(raw.exists(), "the raw image is a build artifact, not something we keep")
        data = written["raw"]
        self.assertEqual(data[:2], vmctl.windows98.MBR_CODE[:2])
        self.assertEqual(data[510:512], b"\x55\xaa")
        entry = data[446:462]
        self.assertEqual(entry[0], 0x80, "the partition must be active or the MBR skips it")
        self.assertEqual(entry[4], 0x0C, "FAT32 LBA")
        start = struct.unpack("<I", entry[8:12])[0]
        self.assertEqual(start, vmctl.windows98.PARTITION_START_SECTOR)
        # BPB_HiddSec: mkfs.vfat leaves it at zero and Windows Setup keeps it, then looks for IO.SYS
        # at the wrong sector and the guest hangs at "Booting from Hard Disk..."
        for lba in (start, start + vmctl.windows98.FAT32_BACKUP_SECTOR):
            hidden = struct.unpack("<I", data[lba * 512 + 0x1C:lba * 512 + 0x20])[0]
            self.assertEqual(hidden, start, f"hidden sectors at LBA {lba}")
        stub = data[start * 512 + vmctl.windows98.FAT32_STUB_OFFSET:][:2]
        self.assertEqual(stub, vmctl.windows98.RETURN_TO_BIOS)

    def test_a_disk_too_small_for_windows_98_is_refused(self):
        self.vm_config["disk"]["size"] = "100M"
        with self.assertRaisesRegex(vmctl.errors.VMError, "too small"):
            vmctl.windows98.prepare_disk(self.vm_config, dry_run=True)

    def test_the_iso_build_writes_a_part_file_and_carries_the_three_pieces(self):
        iso = bootable_iso(self.root / "w98.iso")

        def fake_run(command, **kwargs):
            if command[0] == "xorriso":
                Path(command[command.index("-outdev") + 1]).write_bytes(b"iso")

        with mock.patch.object(vmctl.runtime, "run", side_effect=fake_run) as run, \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.windows98.subprocess, "run") as mdel, \
             mock.patch.object(vmctl.windows98, "render_config_sys", return_value=b"x"), \
             mock.patch.object(vmctl.windows98.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=CONFIG_SYS)):
            vmctl.windows98.ensure_install_iso("testvm", self.vm_config, iso, "[Setup]\n")
        xorriso = [call.args[0] for call in run.call_args_list if call.args[0][0] == "xorriso"][0]
        self.assertIn(vmctl.windows98.ANSWER_PATH, xorriso)
        self.assertIn(vmctl.windows98.SCRIPT_PATH, xorriso)
        self.assertIn(vmctl.windows98.BOOT_IMAGE_PATH, xorriso)
        self.assertIn("emul_type=diskette", xorriso)
        # xorriso refuses an existing -outdev and silently leaves the old CD in place
        self.assertTrue(xorriso[xorriso.index("-outdev") + 1].endswith(".part"))

    def test_profile_check_rejects_hardware_windows_98_cannot_drive(self):
        vmctl.windows98.check_profile("testvm", self.vm_config)
        for key, value, expected in (("firmware", {"type": "efi"}, "bios"),
                                     ("machine", "q35", "machine must be pc"),
                                     ("disk", {"path": "d", "interface": "virtio"}, "ide"),
                                     ("cpus", 2, "cpus must be 1"),
                                     ("memory_mb", 4096, "512 or less")):
            broken = dict(self.vm_config, **{key: value})
            with self.subTest(key=key), self.assertRaisesRegex(vmctl.errors.VMError, expected):
                vmctl.windows98.check_profile("testvm", broken)

    def test_an_explicit_cpu_model_survives_kvm(self):
        import shutil
        import vmctl.qemu
        self.vm_config["cpu_model"] = "pentium3"
        with mock.patch.object(shutil, "which", return_value="/usr/bin/qemu-system-x86_64"):
            args = vmctl.qemu.common_args(self.vm_config, None, dry_run=True, headless=True,
                                          allow_missing_disk=True, accel="kvm")
        self.assertEqual(args[args.index("-cpu") + 1], "pentium3",
                         "Windows 98 does not survive the feature set of a modern host CPU")

    def test_command_is_registered(self):
        args = cli.build_parser().parse_args(["bootstrap-windows98", "testvm"])
        self.assertIs(args.func, vmctl.lifecycle.cmd_bootstrap_windows98)
        self.assertEqual(args.timeout, 5400)
        groups = {name for _, _, names in cli.COMMAND_GROUPS for name in names}
        self.assertIn("bootstrap-windows98", groups)
