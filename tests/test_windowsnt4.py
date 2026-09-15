import configparser
import struct
from pathlib import Path
from unittest import mock

from _common import BaseVmctlTestCase
import vmctl.errors
import vmctl.lifecycle
import vmctl.runtime
import vmctl.windows98
import vmctl.windowsnt4
from vmctl import cli


SAMPLE_UNATTEND = (
    "[Unattended]\r\nOemPreinstall = no\r\n\r\n[GuiUnattended]\r\n"
    'TimeZone = "(GMT+01:00) Berlino, Stoccolma, Roma, Berna, Bruxelles, Vienna"\r\n'
).encode("cp1252")


VENDOR_INF = (
    "[Options]\r\n    AMDPCI\r\n[Install-Option]\r\n    Set from = adapteroptions\r\nadapteroptions = +\r\n"
    '    LoadLibrary "x" $(!STF_CWDDIR)\\$(DialogDllName) hLib\r\n    ui start "Inputdlg" $(hLib)\r\n'
    "skipoptions =+\r\n    Shell $(UtilityInf), AddValueList, $(KeyParameters), $(NewValueList)\r\n"
).encode("cp1252")


def fake_extract(command, **kwargs):
    """xorriso -extract of the medium's own PCnet INF: a one-file cabinet lands at the target."""
    if "-extract" in command:
        Path(command[command.index("-extract") + 2]).write_bytes(vmctl.windowsnt4.cab_store_single("oemnadap.inf", VENDOR_INF))
    return mock.Mock(returncode=0)


class WindowsNt4Tests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.vm_config.update({
            "disk": {"path": "artifacts/testvm/disk.raw", "format": "raw", "size": "2G", "interface": "ide"},
            "firmware": {"type": "bios"},
            "machine": "pc",
            "acpi": False,
            "cpus": 1,
            "memory_mb": 256,
            "network_device": "ne2k_isa,iobase=0x300,irq=5",
            "video": {"headless": ["-vga", "std", "-display", "none"]},
            "windowsnt4_config": {"computer_name": "NT4-LAB", "product_key": "123-4567890",
                                  "timezone": "(GMT) Ora di Greenwich; Dublino, Edimburgo, Londra, Lisbona"},
        })
        # the DOS side, as files: a FreeDOS floppy and the two Microsoft pieces
        for name in ("isos/freedos-1.3-x86boot.img", "isos/dos/OAKCDROM.SYS", "isos/dos/MSCDEX.EXE"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")

    def test_answer_file_preinstalls_from_oem_and_ends_in_the_first_logon_command(self):
        text = vmctl.windowsnt4.render_unattend("testvm", self.vm_config)
        answer = configparser.ConfigParser(interpolation=None, allow_no_value=True)
        answer.read_string(text)
        # $OEM$ is copied and CMDLINES.TXT runs only with this
        self.assertEqual(answer["Unattended"]["OemPreinstall"], "yes")
        self.assertEqual(answer["Unattended"]["NoWaitAfterTextMode"], "1")
        self.assertEqual(answer["Unattended"]["NoWaitAfterGUIMode"], "1")
        # NT 4 spells the licence ProductID, quoted
        self.assertEqual(answer["UserData"]["ProductID"], '"123-4567890"')
        self.assertEqual(answer["UserData"]["ComputerName"], "NT4-LAB")
        # the display name in the medium's language, never Windows 2000's numeric index
        self.assertEqual(answer["GuiUnattended"]["TimeZone"], '"(GMT) Ora di Greenwich; Dublino, Edimburgo, Londra, Lisbona"')
        self.assertNotIn("TimeZone = 110", text)
        # an installed ISA NE2000 with the parameters QEMU was given: its INF asks nothing in unattended mode
        self.assertEqual(answer["Network"]["InstallAdapters"], "AdaptersSection")
        self.assertEqual(answer["AdaptersSection"]["NE2000"], "NE2000Params")
        self.assertEqual((answer["NE2000Params"]["InterruptNumber"], answer["NE2000Params"]["IOBaseAddress"], answer["NE2000Params"]["BusType"]), ("5", "768", "1"))
        self.assertNotIn("DetectAdapters", text)
        self.vm_config["network_device"] = "pcnet"
        self.assertIn('DetectAdapters = ""', vmctl.windowsnt4.render_unattend("testvm", self.vm_config))
        self.assertEqual(answer["Display"]["AutoConfirm"], "1")
        # the plain VGA mode: the Cirrus driver hangs the first boot after SP6a (verified live)
        self.assertEqual((answer["Display"]["XResolution"], answer["Display"]["BitsPerPel"]), ("640", "4"))
        # NT 4 left its RunOnce key empty with a [GuiRunOnce] section (verified live): the
        # first-logon command travels in the .reg instead
        self.assertNotIn("[GuiRunOnce]", text)
        registry = vmctl.windowsnt4.render_registry(self.vm_config).decode("cp850")
        self.assertIn("[HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\RunOnce]", registry)
        # no redirection on the RunOnce line: COM1 is opened by FIRST.CMD itself, after a pause
        self.assertIn('"vmctl"="cmd /c C:\\\\VMCTL\\\\FIRST.CMD"', registry)
        launcher = vmctl.windowsnt4.render_launcher_script().decode("cp850")
        self.assertIn("ping -n 6 127.0.0.1 > nul", launcher)
        self.assertIn("call C:\\VMCTL\\REPORT.CMD > COM1 2>&1", launcher)
        # the serial mouse probe would otherwise hold COM1 exactly when RunOnce fires (verified live)
        self.assertIn("Services\\Sermouse]", registry)
        self.assertIn('"Start"=dword:00000004', registry)
        # a first-boot stop error must stay visible and write no dump (a dump trashed the FAT16 volume)
        self.assertIn('"CrashDumpEnabled"=dword:00000000', registry)
        self.assertIn('"AutoReboot"=dword:00000000', registry)

    def test_the_time_zone_comes_from_the_vendor_sample_on_the_medium(self):
        del self.vm_config["windowsnt4_config"]["timezone"]
        medium = self.root / "nt4.iso"
        medium.write_bytes(b"iso")

        def fake_xorriso(command, **kwargs):
            Path(command[command.index("-extract") + 2]).write_bytes(SAMPLE_UNATTEND)
            return mock.Mock(returncode=0)

        with mock.patch.object(vmctl.windowsnt4.subprocess, "run", side_effect=fake_xorriso):
            text = vmctl.windowsnt4.render_unattend("testvm", self.vm_config, medium)
        self.assertIn('TimeZone = "(GMT+01:00) Berlino, Stoccolma, Roma, Berna, Bruxelles, Vienna"', text)
        # no sample and no profile value: say what is expected instead of installing with GMT
        with mock.patch.object(vmctl.windowsnt4.subprocess, "run", return_value=mock.Mock(returncode=1)), \
             self.assertRaisesRegex(vmctl.errors.VMError, "timezone"):
            vmctl.windowsnt4.render_unattend("testvm", self.vm_config, medium)

    def test_a_tracked_profile_without_a_key_fails_before_anything_is_built(self):
        del self.vm_config["windowsnt4_config"]["product_key"]
        with self.assertRaisesRegex(vmctl.errors.VMError, "product_key"):
            vmctl.windowsnt4.render_unattend("testvm", self.vm_config)

    def test_oem_files_are_dos_named_crlf_and_carry_the_shutdown_tool(self):
        files = vmctl.windowsnt4.oem_files(self.vm_config)
        for name, data in files.items():
            stem, _, ext = name.partition(".")
            self.assertTrue(name.isupper() and len(stem) <= 8 and len(ext) <= 3, f"{name} is not an 8.3 upper-case name")
            if not name.endswith(".EXE"):
                self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), f"{name} must be CRLF")
        self.assertEqual(files["CMDLINES.TXT"], b'[Commands]\r\n".\\VMCTL.CMD"\r\n')
        tool = files["VMCTLOFF.EXE"]
        self.assertEqual(tool[:2], b"MZ")
        pe = struct.unpack_from("<I", tool, 0x3C)[0]
        self.assertEqual(tool[pe:pe + 4], b"PE\0\0")
        self.assertEqual(struct.unpack_from("<H", tool, pe + 24 + 48)[0], 4, "subsystem version 4.0, or the NT 4 loader refuses it")
        for symbol in (b"ExitWindowsEx", b"AdjustTokenPrivileges", b"SeShutdownPrivilege", b"USER32.DLL"):
            self.assertIn(symbol, tool)

    def test_cmdlines_stage_copies_the_first_logon_pieces_and_runs_the_service_pack(self):
        package = self.root / "isos" / "SP6I386.EXE"
        package.write_bytes(b"MZ")
        self.vm_config["windowsnt4_config"]["service_pack"] = "isos/SP6I386.EXE"
        script = vmctl.windowsnt4.render_setup_script(self.vm_config).decode("cp850")
        # Setup deletes $WIN_NT$.~LS when it finishes: what the first logon needs goes to C:\VMCTL
        self.assertIn("copy .\\FIRST.CMD C:\\VMCTL\\", script)
        self.assertIn("copy .\\REPORT.CMD C:\\VMCTL\\", script)
        self.assertIn("copy .\\AUTOLOG.REG C:\\VMCTL\\", script)
        self.assertIn("copy .\\VMCTLOFF.EXE C:\\VMCTL\\", script)
        # not a bare "regedit": %SystemRoot% is not on the PATH during the GUI stage (verified live)
        self.assertIn("%SystemRoot%\\regedit.exe /s .\\VMCTL.REG", script)
        # the pack stays one self-extracting 8.3 file: an extracted tree stopped the DOS-side copy
        self.assertIn("start /wait .\\NT4SP.EXE /u /q /z /o", script)
        self.assertIn("echo UPDATE_EXIT=%ERRORLEVEL%> C:\\VMCTL\\SP.LOG", script)
        registry = vmctl.windowsnt4.render_registry(self.vm_config).decode("cp850")
        self.assertIn("REGEDIT4", registry)
        self.assertIn('"AutoAdminLogon"="1"', registry)
        self.assertIn('"DefaultUserName"="Administrator"', registry)
        self.assertIn('"DefaultDomainName"="NT4-LAB"', registry)
        first = vmctl.windowsnt4.render_first_logon_script(self.vm_config).decode("cp850")
        # the proof is CSDVersion: NT 4's start /wait never hands back update.exe's exit code
        self.assertNotIn('find "UPDATE_EXIT=0"', first)
        self.assertIn('find "Service Pack" C:\\VMCTL\\VER.REG > nul', first)
        self.assertIn("echo ==^> Windows NT 4.0 installation FAILED: CSDVersion shows no service pack", first)
        self.assertIn('find "CSDVersion" C:\\VMCTL\\VER.REG', first)
        # the blank-password autologon works once: the first logon sets the password and re-imports
        self.assertIn("net user Administrator lab", first)
        self.assertIn("%SystemRoot%\\regedit.exe /s C:\\VMCTL\\AUTOLOG.REG", first)
        self.assertLess(first.index("net user"), first.index("installation complete"))
        autolog = vmctl.windowsnt4.render_autologon_registry(self.vm_config).decode("cp850")
        self.assertIn('"DefaultPassword"="lab"', autolog)
        self.assertIn('"AutoAdminLogon"="1"', autolog)
        self.assertIn("echo ==^> Windows NT 4.0 installation complete!", first)
        self.assertNotIn("echo ==>", first)
        # the token comes before the shutdown, and the shutdown is our own tool
        self.assertLess(first.index("installation complete"), first.index("C:\\VMCTL\\VMCTLOFF.EXE"))

    def test_missing_service_pack_file_is_reported_by_name(self):
        self.vm_config["windowsnt4_config"]["service_pack"] = "isos/nope.exe"
        with self.assertRaisesRegex(vmctl.errors.VMError, "service_pack does not exist"):
            vmctl.windowsnt4.render_setup_script(self.vm_config)

    def test_freedos_startup_files_mount_the_cd_and_run_winnt_unattended(self):
        config = vmctl.windowsnt4.render_fdconfig().decode("cp850")
        self.assertIn("DEVICE=A:\\CDROM.SYS /D:MSCD001", config)
        self.assertIn("/P=A:\\FDAUTO.BAT", config)
        auto = vmctl.windowsnt4.render_fdauto(self.vm_config).decode("cp850")
        self.assertIn("A:\\MSCDEX.EXE /D:MSCD001 /L:D", auto)
        self.assertIn("WINNT.EXE /U:D:\\UNATTEND.TXT /S:D:\\I386 /B", auto)
        self.assertIn("SET PATH=A:\\;A:\\FREEDOS\\BIN", auto)
        for text in (config, auto):
            self.assertEqual(text.count("\n"), text.count("\r\n"))

    def test_prepared_disk_is_active_fat16_with_hidden_sectors_and_boot_code_that_steps_aside(self):
        raw = self.root / "artifacts/testvm/disk.raw"
        written = {}

        def fake_run(command, **kwargs):
            if command[0] == "mkfs.vfat":
                written["mkfs"] = command

        with mock.patch.object(vmctl.runtime, "run", side_effect=fake_run) as run, \
             mock.patch.object(vmctl.runtime, "require_command"):
            vmctl.windowsnt4.prepare_disk(self.vm_config)
        # raw stays raw: NT 4 never flushes the disk cache, and qcow2 metadata died with a hard kill
        self.assertTrue(raw.exists())
        self.assertFalse([c for c in (call.args[0] for call in run.call_args_list) if c[0] == "qemu-img"])
        data = raw.read_bytes()
        self.assertEqual(data[:len(vmctl.windows98.MBR_CODE)], vmctl.windows98.MBR_CODE)
        entry = data[446:462]
        self.assertEqual(entry[0], 0x80)
        self.assertEqual(entry[4], 0x06, "FAT16: neither DOS nor NT 4 reads FAT32")
        self.assertEqual(struct.unpack("<I", entry[8:12])[0], vmctl.windowsnt4.PARTITION_START_SECTOR)
        mkfs = written["mkfs"]
        self.assertEqual(mkfs[mkfs.index("-F") + 1], "16")
        # BPB_HiddSec through mkfs itself: the NT boot sector computes every address from it
        self.assertEqual(mkfs[mkfs.index("-h") + 1], str(vmctl.windowsnt4.PARTITION_START_SECTOR))
        stub = data[vmctl.windowsnt4.PARTITION_START_SECTOR * 512 + vmctl.windowsnt4.FAT16_STUB_OFFSET:][:2]
        self.assertEqual(stub, vmctl.windows98.RETURN_TO_BIOS)

    def test_disk_sizes_outside_what_fat16_and_dos_can_take_are_refused(self):
        for size, expected in (("100M", "too small"), ("4G", "too large")):
            self.vm_config["disk"]["size"] = size
            with self.subTest(size=size), self.assertRaisesRegex(vmctl.errors.VMError, expected):
                vmctl.windowsnt4.prepare_disk(self.vm_config, dry_run=True)

    def test_the_iso_build_boots_our_floppy_and_grafts_oem_under_i386(self):
        self.vm_config["network_device"] = "pcnet"
        source = self.root / "nt4.iso"
        source.write_bytes(b"iso")
        package = self.root / "isos" / "SP6I386.EXE"
        package.write_bytes(b"MZ")
        self.vm_config["windowsnt4_config"]["service_pack"] = "isos/SP6I386.EXE"

        def fake_run(command, **kwargs):
            if command[0] == "xorriso":
                Path(command[command.index("-outdev") + 1]).write_bytes(b"iso")

        with mock.patch.object(vmctl.runtime, "run", side_effect=fake_run) as run, \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.windowsnt4.subprocess, "run", side_effect=fake_extract):
            vmctl.windowsnt4.ensure_install_iso("testvm", self.vm_config, source, "[Unattended]\n")
            first = run.call_count
            vmctl.windowsnt4.ensure_install_iso("testvm", self.vm_config, source, "[Unattended]\n")
            self.assertEqual(run.call_count, first, "same source and answers: cached")
        commands = [call.args[0] for call in run.call_args_list]
        xorriso = [c for c in commands if c[0] == "xorriso"][0]
        self.assertIn("/UNATTEND.TXT", xorriso)
        # not the root: $OEM$ at the root is silently ignored (verified live)
        self.assertIn("/I386/$OEM$", xorriso)
        self.assertIn("/I386/$OEM$/NT4SP.EXE", xorriso)
        self.assertIn("emul_type=diskette", xorriso)
        # the PCnet INF goes back over the medium's own, with the dialog-skipping branch
        self.assertIn(vmctl.windowsnt4.PCNET_INF_PATH, xorriso)
        patched = Path(xorriso[xorriso.index(vmctl.windowsnt4.PCNET_INF_PATH) - 1])
        self.assertTrue(patched.name == "OEMNADAP.IN_")
        self.assertTrue(xorriso[xorriso.index("-outdev") + 1].endswith(".part"))
        # BACHSB~1.RM_ keeps its tilde, or text-mode Setup stops on that file (verified live)
        self.assertIn("omit_version:untranslated_names", xorriso)
        copied = [c[-1] for c in commands if c[0] == "mcopy"]
        self.assertEqual(copied, ["::CDROM.SYS", "::MSCDEX.EXE", "::FDCONFIG.SYS", "::FDAUTO.BAT"])

    def test_cabinet_round_trip_and_the_pcnet_patch(self):
        cab = vmctl.windowsnt4.cab_store_single("oemnadap.inf", VENDOR_INF)
        self.assertEqual(cab[:4], b"MSCF")
        self.assertEqual(vmctl.windowsnt4.cab_extract_single(cab), ("oemnadap.inf", VENDOR_INF))
        big = bytes(range(256)) * 300  # more than one 32 KB block
        self.assertEqual(vmctl.windowsnt4.cab_extract_single(vmctl.windowsnt4.cab_store_single("x.bin", big))[1], big)
        name, text = vmctl.windowsnt4.cab_extract_single(vmctl.windowsnt4.patch_pcnet_inf(cab))
        lines = text.decode("cp1252").split("\r\n")
        at = lines.index("adapteroptions = +")
        # the branch Microsoft's own ISA INF has: skip the dialog, write the defaults
        # TP=0 (auto port) is what the confirmed dialog wrote; the INF's own default of 1 ended in a
        # tcpip.sys stop (verified live)
        self.assertEqual(lines[at + 1:at + 5], ['    ifstr(i) $(!STF_GUI_UNATTENDED) == "YES"', "        Set TPValue = 0",
                                                "        goto skipoptions", "    endif"])
        self.assertNotIn("\n", text.decode("cp1252").replace("\r\n", ""), "every line CRLF, like the vendor file")
        patched = vmctl.windowsnt4.patch_pcnet_inf(cab)
        self.assertEqual(vmctl.windowsnt4.patch_pcnet_inf(patched), patched, "already unattended-aware: left alone")
        with self.assertRaisesRegex(vmctl.errors.VMError, "adapteroptions"):
            vmctl.windowsnt4.patch_pcnet_inf(vmctl.windowsnt4.cab_store_single("other.inf", b"[Options]\r\n"))
        with self.assertRaisesRegex(vmctl.errors.VMError, "cabinet"):
            vmctl.windowsnt4.cab_extract_single(b"not a cab")

    def test_the_pcnet_patch_can_be_declined(self):
        work = self.root / "work"
        work.mkdir()
        self.assertIsNone(vmctl.windowsnt4.pcnet_inf_override(self.vm_config, self.root / "nt4.iso", work), "no PCnet, no patch")
        self.vm_config["network_device"] = "pcnet"
        self.vm_config["windowsnt4_config"]["patch_pcnet_inf"] = False
        self.assertIsNone(vmctl.windowsnt4.pcnet_inf_override(self.vm_config, self.root / "nt4.iso", work))
        del self.vm_config["windowsnt4_config"]["patch_pcnet_inf"]
        with mock.patch.object(vmctl.windowsnt4.subprocess, "run", side_effect=fake_extract):
            override = vmctl.windowsnt4.pcnet_inf_override(self.vm_config, self.root / "nt4.iso", work)
        assert override is not None
        self.assertIn(b"STF_GUI_UNATTENDED", vmctl.windowsnt4.cab_extract_single(override.read_bytes())[1])

    def test_the_dos_pieces_are_named_when_missing(self):
        (self.root / "isos/dos/MSCDEX.EXE").unlink()
        with self.assertRaisesRegex(vmctl.errors.VMError, "MSCDEX.EXE"):
            vmctl.windowsnt4.ensure_dos_pieces(self.vm_config)

    def test_the_cd_driver_and_mscdex_are_extracted_from_a_windows_98_cd(self):
        (self.root / "isos/dos/OAKCDROM.SYS").unlink()
        (self.root / "isos/dos/MSCDEX.EXE").unlink()
        self.vm_config["windowsnt4_config"]["cdrom_driver_iso"] = "isos/windows98.iso"
        w98 = self.root / "isos/windows98.iso"
        w98.write_bytes(bytes(2048 * 64))

        def fake_run(command, **kwargs):
            if command[0] == "mcopy":
                Path(command[-1]).write_bytes(b"x")

        with mock.patch.object(vmctl.windows98, "boot_image_extent", return_value=(21 * 2048, 4096)), \
             mock.patch.object(vmctl.runtime, "run", side_effect=fake_run) as run, \
             mock.patch.object(vmctl.runtime, "require_command"):
            vmctl.windowsnt4.ensure_dos_pieces(self.vm_config)
        members = [c[c.index("-i") + 2] for c in (call.args[0] for call in run.call_args_list) if c[0] == "mcopy"]
        self.assertEqual(members, ["::OAKCDROM.SYS", "::MSCDEX.EXE"])
        self.assertTrue((self.root / "isos/dos/MSCDEX.EXE").is_file())

    def test_installer_cd_sits_behind_the_disk_in_the_boot_order(self):
        args = " ".join(vmctl.windowsnt4.install_media_args(Path("/tmp/install.iso")))
        self.assertIn("bootindex=2", args)
        self.assertIn("bus=ide.1", args)

    def test_acpi_off_reaches_the_machine_argument(self):
        import shutil
        import vmctl.qemu
        with mock.patch.object(shutil, "which", return_value="/usr/bin/qemu-system-x86_64"):
            args = vmctl.qemu.common_args(self.vm_config, None, dry_run=True, headless=True,
                                          allow_missing_disk=True, accel="kvm")
        self.assertIn("acpi=off", args[args.index("-machine") + 1])
        self.assertEqual(args[args.index("-cpu") + 1], "host")
        self.vm_config.pop("acpi")
        with mock.patch.object(shutil, "which", return_value="/usr/bin/qemu-system-x86_64"):
            args = vmctl.qemu.common_args(self.vm_config, None, dry_run=True, headless=True,
                                          allow_missing_disk=True, accel="kvm")
        self.assertNotIn("acpi", args[args.index("-machine") + 1])

    def test_profile_check_rejects_hardware_nt4_cannot_drive(self):
        vmctl.windowsnt4.check_profile("testvm", self.vm_config)
        for key, value, expected in (("firmware", {"type": "efi"}, "bios"),
                                     ("machine", "q35", "machine must be pc"),
                                     ("acpi", True, "acpi must be false"),
                                     ("disk", {"path": "d", "interface": "virtio", "format": "raw"}, "ide"),
                                     ("disk", {"path": "d", "interface": "ide", "format": "qcow2"}, "format must be raw"),
                                     ("cpus", 2, "cpus must be 1"),
                                     ("video", {"headless": ["-vga", "cirrus", "-display", "none"]}, "Cirrus"),
                                     ("network_device", "tulip", "ne2k_isa"),
                                     ("usb_tablet", True, "no USB stack")):
            broken = dict(self.vm_config, **{key: value})
            with self.subTest(key=key), self.assertRaisesRegex(vmctl.errors.VMError, expected):
                vmctl.windowsnt4.check_profile("testvm", broken)

    def test_command_is_registered_and_selected_by_the_matrix(self):
        args = cli.build_parser().parse_args(["bootstrap-windowsnt4", "testvm"])
        self.assertIs(args.func, vmctl.lifecycle.cmd_bootstrap_windowsnt4)
        self.assertEqual(args.timeout, 5400)
        groups = {name for _, _, names in cli.COMMAND_GROUPS for name in names}
        self.assertIn("bootstrap-windowsnt4", groups)
        mode, note = vmctl.lifecycle.local_test_mode(self.vm_config)
        self.assertEqual(mode, "bootstrap-windowsnt4")
        self.assertIn("NT 4.0", note)
