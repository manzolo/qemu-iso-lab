from pathlib import Path
from unittest import mock

from _common import BaseVmctlTestCase
import vmctl.errors
import vmctl.lifecycle
import vmctl.runtime
import vmctl.windowsxp
from vmctl import cli


def fake_xorriso(command, **kwargs):
    """Stand in for the real tools: only xorriso produces a file, at -outdev."""
    if command[0] == "xorriso":
        Path(command[command.index("-outdev") + 1]).write_bytes(b"iso")


def iso_with_descriptors(path: Path, types: tuple[int, ...]) -> Path:
    """A file shaped like an ISO: volume descriptors at LBA 16 and up, one per sector."""
    data = bytearray(2048 * (16 + len(types)))
    for index, kind in enumerate(types):
        start = (16 + index) * 2048
        data[start] = kind
        data[start + 1:start + 6] = b"CD001"
    path.write_bytes(bytes(data))
    return path


class WindowsXpTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.vm_config.update({
            "disk": {"path": "artifacts/testvm/disk.qcow2", "format": "qcow2", "size": "16G", "interface": "ide"},
            "firmware": {"type": "bios"},
            "machine": "pc",
            "cpus": 1,
            "network_device": "rtl8139",
            "video": {"headless": ["-vga", "cirrus", "-display", "none"]},
            "windowsxp_config": {"product_key": "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE",
                                 "computer_name": "WINXP-LAB", "admin_password": "lab"},
        })

    def test_answer_file_is_fully_unattended_and_ends_with_token_then_shutdown(self):
        text = vmctl.windowsxp.render_winnt_sif("testvm", self.vm_config)
        self.assertIn("UnattendMode=FullUnattended", text)
        self.assertIn('UnattendSwitch="Yes"', text)  # no Windows Welcome: it waits for a click
        self.assertIn("AutoPartition=1", text)
        self.assertIn("OemSkipEula=Yes", text)
        self.assertIn("ProductKey=AAAAA-BBBBB-CCCCC-DDDDD-EEEEE", text)
        self.assertIn("ComputerName=WINXP-LAB", text)
        commands = [line for line in text.splitlines() if line.startswith("Command")]
        self.assertEqual(commands, [f'Command0="{vmctl.windowsxp.first_logon_command()}"'])

    def test_the_first_logon_script_carries_the_work_and_the_answer_file_one_line(self):
        package = self.root / "isos" / "sp3.exe"
        package.parent.mkdir(parents=True, exist_ok=True)
        package.write_bytes(b"MZ")
        self.vm_config["windowsxp_config"]["service_pack"] = "isos/sp3.exe"
        self.vm_config["windowsxp_config"]["setup_commands"] = ["echo mine"]
        script = vmctl.windowsxp.render_setup_script(self.vm_config)
        self.assertIn("start /wait %VMCTLSP% /quiet /norestart /nobackup", script)
        self.assertIn("if not %VMCTLRC%==0 if not %VMCTLRC%==3010", script)  # 3010: reboot pending
        self.assertIn("echo mine", script)
        # autologon must survive the first boot: the answer file only covers the GuiRunOnce one
        self.assertIn("AutoAdminLogon /t REG_SZ /d 1 /f", script)
        self.assertIn("DefaultUserName /t REG_SZ /d Administrator /f", script)
        # Winlogon would otherwise delete those values when its own counter runs out
        self.assertIn("reg delete", script)
        self.assertIn("AutoLogonCount /f", script)
        self.assertIn('reg query "HKLM\\SOFTWARE\\Microsoft\\Windows NT', script)
        self.assertTrue(script.rstrip().endswith("shutdown -s -t 5 -f"))
        # every '>' of a token is escaped: in a batch file too, a bare one redirects
        self.assertNotIn("echo ==>", script)
        self.assertIn("echo ==^> Windows XP installation complete!", script)
        commands = [line for line in vmctl.windowsxp.render_winnt_sif("testvm", self.vm_config).splitlines()
                    if line.startswith("Command")]
        self.assertEqual(commands, [f'Command0="{vmctl.windowsxp.first_logon_command()}"'])
        self.assertIn("> COM1 2>&1", commands[0])
        self.assertLess(len(vmctl.windowsxp.first_logon_command()), 260)  # the Windows RunOnce limit

    def test_the_script_reaches_the_medium(self):
        source = iso_with_descriptors(self.root / "xp.iso", (1, 0, 255))
        with mock.patch.object(vmctl.runtime, "run", side_effect=fake_xorriso) as run, \
             mock.patch.object(vmctl.runtime, "require_command"):
            vmctl.windowsxp.ensure_install_iso("testvm", self.vm_config, source, "[Data]\n")
        xorriso = [call.args[0] for call in run.call_args_list if call.args[0][0] == "xorriso"][0]
        self.assertIn(vmctl.windowsxp.SETUP_SCRIPT_PATH, xorriso)

    def test_a_tracked_profile_without_a_key_fails_before_anything_is_built(self):
        del self.vm_config["windowsxp_config"]["product_key"]
        with self.assertRaisesRegex(vmctl.errors.VMError, "product_key"):
            vmctl.windowsxp.render_winnt_sif("testvm", self.vm_config)

    def test_missing_service_pack_file_is_reported_by_name(self):
        self.vm_config["windowsxp_config"]["service_pack"] = "isos/nope.exe"
        with self.assertRaisesRegex(vmctl.errors.VMError, "service_pack does not exist"):
            vmctl.windowsxp.render_setup_script(self.vm_config)

    def test_boot_record_detection(self):
        bootable = iso_with_descriptors(self.root / "boot.iso", (1, 0, 255))
        plain = iso_with_descriptors(self.root / "plain.iso", (1, 2, 255))
        self.assertTrue(vmctl.windowsxp.iso_has_boot_record(bootable))
        self.assertFalse(vmctl.windowsxp.iso_has_boot_record(plain))

    def test_a_medium_without_a_boot_record_gets_grub_as_its_el_torito_image(self):
        source = iso_with_descriptors(self.root / "xp.iso", (1, 2, 255))
        with mock.patch.object(vmctl.runtime, "run", side_effect=fake_xorriso) as run, \
             mock.patch.object(vmctl.runtime, "require_command"):
            vmctl.windowsxp.ensure_install_iso("testvm", self.vm_config, source, "[Data]\n")
        grub = [call.args[0] for call in run.call_args_list if call.args[0][0] == "grub-mkimage"]
        self.assertEqual(len(grub), 1)
        self.assertIn("i386-pc-eltorito", grub[0])
        self.assertIn("ntldr", grub[0])
        command = [call.args[0] for call in run.call_args_list if call.args[0][0] == "xorriso"][0]
        self.assertIn("/I386/WINNT.SIF", command)
        self.assertIn(f"bin_path={vmctl.windowsxp.GRUB_CORE_PATH}", command)
        self.assertIn("boot_info_table=on", command)
        self.assertNotIn("replay", command, "there is no original boot record to replay")

    def test_a_bootable_medium_keeps_its_own_boot_record(self):
        source = iso_with_descriptors(self.root / "xp.iso", (1, 0, 255))
        with mock.patch.object(vmctl.runtime, "run", side_effect=fake_xorriso) as run, \
             mock.patch.object(vmctl.runtime, "require_command"):
            vmctl.windowsxp.ensure_install_iso("testvm", self.vm_config, source, "[Data]\n")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse([c for c in commands if c[0] == "grub-mkimage"], "no GRUB when the CD boots")
        xorriso = [c for c in commands if c[0] == "xorriso"][0]
        self.assertIn("replay", xorriso)

    def test_the_iso_is_rebuilt_only_when_the_source_or_the_answers_change(self):
        source = iso_with_descriptors(self.root / "xp.iso", (1, 0, 255))
        dest = vmctl.windowsxp.install_iso_path(self.vm_config)
        dest.parent.mkdir(parents=True, exist_ok=True)

        with mock.patch.object(vmctl.runtime, "run", side_effect=fake_xorriso) as run, \
             mock.patch.object(vmctl.runtime, "require_command"):
            vmctl.windowsxp.ensure_install_iso("testvm", self.vm_config, source, "[Data]\n")
            first = run.call_count
            vmctl.windowsxp.ensure_install_iso("testvm", self.vm_config, source, "[Data]\n")
            self.assertEqual(run.call_count, first, "same source and answers: cached")
            vmctl.windowsxp.ensure_install_iso("testvm", self.vm_config, source, "[Data]\nchanged\n")
            self.assertGreater(run.call_count, first, "new answers: rebuilt")

    def test_the_tablet_rides_a_controller_xp_has_a_driver_for(self):
        import vmctl.qemu
        self.vm_config.update({"usb_tablet": True, "usb_controller": "builtin"})
        args = vmctl.qemu.common_args(self.vm_config, None, dry_run=True, headless=True,
                                      allow_missing_disk=True)
        self.assertIn("usb-tablet", args)
        self.assertNotIn("qemu-xhci", args, "XP has no xHCI driver: the tablet would not exist")
        self.vm_config.pop("usb_controller")
        self.assertIn("qemu-xhci", vmctl.qemu.common_args(self.vm_config, None, dry_run=True,
                                                          headless=True, allow_missing_disk=True))

    def test_installer_cd_sits_behind_the_disk_in_the_boot_order(self):
        args = vmctl.windowsxp.install_media_args(Path("/tmp/install.iso"))
        self.assertIn("bootindex=2", " ".join(args))
        self.assertIn("bus=ide.1", " ".join(args))

    def test_the_standard_vga_is_refused_because_of_the_first_logon_dialog(self):
        self.vm_config["video"] = {"headless": ["-display", "none"]}
        with self.assertRaisesRegex(vmctl.errors.VMError, "Cirrus"):
            vmctl.windowsxp.check_profile("testvm", self.vm_config)
        self.vm_config["video"] = {"headless": ["-vga", "cirrus", "-display", "none"]}
        vmctl.windowsxp.check_profile("testvm", self.vm_config)

    def test_profile_check_rejects_hardware_xp_cannot_drive(self):
        vmctl.windowsxp.check_profile("testvm", self.vm_config)  # the good one passes
        for key, value, expected in (("firmware", {"type": "efi"}, "bios"),
                                     ("machine", "q35", "machine must be pc"),
                                     ("disk", {"path": "d", "interface": "virtio"}, "ide"),
                                     ("cpus", 2, "cpus must be 1"),
                                     ("network_device", "virtio-net-pci", "network_device")):
            broken = dict(self.vm_config, **{key: value})
            with self.subTest(key=key), self.assertRaisesRegex(vmctl.errors.VMError, expected):
                vmctl.windowsxp.check_profile("testvm", broken)

    def test_command_is_registered_and_the_dry_run_stops_at_a_missing_iso(self):
        parser = cli.build_parser()
        args = parser.parse_args(["bootstrap-windowsxp", "testvm"])
        self.assertIs(args.func, vmctl.lifecycle.cmd_bootstrap_windowsxp)
        self.assertEqual(args.timeout, 3600)
        groups = {name for _, _, names in cli.COMMAND_GROUPS for name in names}
        self.assertIn("bootstrap-windowsxp", groups)
