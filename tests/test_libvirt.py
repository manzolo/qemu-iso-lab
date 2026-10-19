import argparse
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from _common import BaseVmctlTestCase, enter_context
import vmctl.vmstate
from vmctl import cli, libvirt, lifecycle, qemu, runtime
from vmctl.errors import VMError


class LibvirtTests(BaseVmctlTestCase):
    def args(self, *extra):
        argv = ["export-libvirt", self.vm_name, *extra]
        return cli.build_parser().parse_args(argv)

    def setUp(self):
        super().setUp()
        enter_context(self, mock.patch.object(lifecycle, "running_qemu_pid", return_value=None))
        enter_context(self, mock.patch.object(qemu, "qmp_command", return_value=False))
        enter_context(self, mock.patch("shutil.which", return_value="/fake/virsh"))
        self.run = enter_context(self, mock.patch.object(runtime, "run"))

    @staticmethod
    def version_output(version):
        # A newer client can connect to an older daemon; QEMU's version is unrelated.
        return ("Compiled against library: libvirt 12.0.0\n"
                "Using library: libvirt 12.0.0\nUsing API: QEMU 12.0.0\n"
                "Running hypervisor: QEMU 6.2.0\n"
                f"Running against daemon: {version}\n")

    def test_firmware_version_threshold_uses_daemon_and_c_locale(self):
        uri = "qemu+ssh://server/system"
        for version, expected in (("8.0.0", "raw"), ("9.1.9", "raw"), ("9.2.0", "qcow2"),
                                  ("9.10.0", "qcow2"), ("12.0.0", "qcow2")):
            with self.subTest(version=version):
                def virsh(command, **kwargs):
                    self.assertEqual(command, ["env", "LC_ALL=C", "virsh", "--connect", uri, "version", "--daemon"])
                    kwargs["stdout_log"].write_text(self.version_output(version))
                self.run.side_effect = virsh
                self.assertEqual(libvirt.export_firmware_format(uri), expected)

    def test_unreadable_version_keeps_qcow2(self):
        for output in ("", "unrecognized", self.version_output("unknown"),
                       "Using library: libvirt 8.0.0\nUsing API: QEMU 8.0.0\nRunning hypervisor: QEMU 6.2.0\n"):
            with self.subTest(output=output), mock.patch.object(libvirt, "virsh_output", return_value=output):
                self.assertEqual(libvirt.export_firmware_format("qemu:///system"), "qcow2")
        for error in (OSError("missing"), subprocess.CalledProcessError(1, "virsh"), VMError("unavailable")):
            with self.subTest(error=error), mock.patch.object(libvirt, "virsh_output", side_effect=error):
                self.assertEqual(libvirt.export_firmware_format("qemu:///system"), "qcow2")

    def test_offline_efi_exports_keep_qcow2_without_queries(self):
        self.create_disk()
        self.vm_config["firmware"] = {"type": "efi"}
        paths = tuple(self.root / filename for filename in ("code.fd", "template.fd", "vars.fd"))
        for flags in (("--no-define",), ("--dry-run",), ("--dry-run", "--copy-to-pool")):
            with self.subTest(flags=flags), mock.patch.object(qemu, "resolve_efi_firmware", return_value=paths), \
                    mock.patch.object(libvirt, "virsh_output") as query:
                self.run.reset_mock()
                libvirt.export(self.args(*flags), self.vm_config)
                query.assert_not_called()
                converts = [c.args[0] for c in self.run.call_args_list if c.args[0][:2] == ["qemu-img", "convert"]]
                self.assertEqual(len(converts), 2)
                self.assertTrue(all(c[4:6] == ["-O", "qcow2"] for c in converts))
                if "--dry-run" in flags:
                    self.assertTrue(all(c.kwargs.get("dry_run") for c in self.run.call_args_list))

    def test_bios_sata_xml(self):
        self.vm_config["disk"].update(interface="sata", format="vhd")
        self.vm_config["network_device"] = "e1000e"
        xml = ET.fromstring(libvirt.render_domain_xml("custom", self.vm_config))
        self.assertEqual(xml.findtext("name"), "custom")
        self.assertIsNone(xml.find("os/loader"))
        self.assertEqual(xml.find("devices/disk/driver").get("type"), "vpc")
        self.assertEqual(xml.find("devices/disk/target").get("bus"), "sata")
        self.assertEqual(xml.find("devices/interface/model").get("type"), "e1000e")
        self.assertEqual(xml.find("devices/disk/source").get("file"), str(self.root / self.vm_config["disk"]["path"]))

    def test_bios_ide_xml(self):
        self.vm_config["disk"].update(interface="ide")
        self.vm_config["network_device"] = "e1000"
        xml = ET.fromstring(libvirt.render_domain_xml("retro", self.vm_config))
        self.assertEqual(xml.find("devices/disk/target").get("bus"), "ide")
        self.assertEqual(xml.find("devices/disk/target").get("dev"), "hda")
        self.assertEqual(xml.find("devices/interface/model").get("type"), "e1000")
        self.assertIsNone(xml.find("devices/sound"))
        self.vm_config.update(audio=True, audio_device="ac97")
        xml = ET.fromstring(libvirt.render_domain_xml("retro", self.vm_config))
        self.assertEqual(xml.find("devices/sound").get("model"), "ac97")
        self.assertIsNone(xml.find("features/vmport"))
        self.vm_config["vmport"] = False
        xml = ET.fromstring(libvirt.render_domain_xml("retro", self.vm_config))
        self.assertEqual(xml.find("features/vmport").get("state"), "off")

    def test_efi_shared_windows_xml(self):
        code, template, nvram = [self.root / name for name in ("code.fd", "template.fd", "vars.fd")]
        self.vm_config.update(firmware={"type": "efi"}, shared_dir={"source": "share & files", "tag": "shared"},
                              windows_config={}, audio=True, usb_tablet=True, machine="q35", video={"default": "virtio-gl"})
        with mock.patch.object(qemu, "resolve_efi_firmware", return_value=(code, template, nvram)):
            xml = ET.fromstring(libvirt.render_domain_xml("windows", self.vm_config))
        self.assertEqual(xml.findtext("os/loader"), str(code))
        self.assertEqual(xml.findtext("os/nvram"), str(nvram))
        self.assertEqual(xml.find("devices/disk/target").get("bus"), "virtio")
        self.assertEqual(xml.find("memoryBacking/source").get("type"), "memfd")
        self.assertEqual(xml.find("memoryBacking/access").get("mode"), "shared")
        self.assertEqual(xml.find("devices/filesystem/source").get("dir"), str(self.root / "share & files"))
        self.assertEqual(xml.find("devices/tpm/backend").get("version"), "2.0")
        self.assertEqual(xml.find("devices/video/model").get("type"), "virtio")
        self.assertEqual(xml.find("devices/channel/target").get("name"), "org.qemu.guest_agent.0")
        self.assertIsNotNone(xml.find("devices/sound"))
        self.assertIsNotNone(xml.find("devices/input"))
        self.assertIsNone(xml.find("features/hyperv"), "only hyperv: true asks for the enlightenments")

    def test_hyperv_profiles_get_the_enlightenments_and_the_reference_clock(self):
        # Without them Windows 11 reset the moment WSL started its VM (windows11-studio, 2026-10-04).
        self.vm_config["hyperv"] = True
        with mock.patch.object(libvirt, "host_is_intel", return_value=True):
            xml = ET.fromstring(libvirt.render_domain_xml("windows", self.vm_config))
        hyperv = xml.find("features/hyperv")
        # An explicit list: passthrough refuses a running snapshot, reenlightenment breaks its revert.
        self.assertEqual(hyperv.get("mode"), "custom")
        self.assertIsNone(hyperv.find("reenlightenment"))
        self.assertEqual(hyperv.find("spinlocks").get("retries"), "8191")
        self.assertEqual(hyperv.find("stimer/direct").get("state"), "on")
        self.assertEqual(hyperv.find("evmcs").get("state"), "on")
        self.assertEqual(xml.find("clock/timer").get("name"), "hypervclock")
        with mock.patch.object(libvirt, "host_is_intel", return_value=False):
            xml = ET.fromstring(libvirt.render_domain_xml("windows", self.vm_config))
        self.assertIsNone(xml.find("features/hyperv/evmcs"), "evmcs exists on Intel VMX only")
        self.vm_config["cpu_model"] = "pentium3"
        xml = ET.fromstring(libvirt.render_domain_xml("windows", self.vm_config))
        self.assertIsNone(xml.find("features/hyperv"))
        self.assertIsNone(xml.find("clock"))

    def test_missing_disk(self):
        with self.assertRaisesRegex(VMError, "Installed disk missing"):
            lifecycle.cmd_export_libvirt(self.args())
        self.run.assert_not_called()

    def test_running_pid_or_qmp_refused(self):
        for pid, qmp in ((123, False), (None, True)):
            with mock.patch.object(lifecycle, "running_qemu_pid", return_value=pid), mock.patch.object(qemu, "qmp_command", return_value=qmp):
                with self.assertRaisesRegex(VMError, "running in QEMU"):
                    lifecycle.cmd_export_libvirt(self.args())
        self.run.assert_not_called()

    def test_dry_run_does_not_write_or_query(self):
        self.create_disk()
        lifecycle.cmd_export_libvirt(self.args("--dry-run", "--replace", "--autostart", "--name", "custom"))
        self.assertFalse((self.root / "artifacts/testvm/libvirt").exists())
        self.assertEqual([c.args[0][3] for c in self.run.call_args_list], ["undefine", "define", "autostart"])
        self.assertTrue(all(c.kwargs.get("dry_run") for c in self.run.call_args_list))

    def test_no_define(self):
        self.create_disk()
        lifecycle.cmd_export_libvirt(self.args("--no-define"))
        self.assertTrue((self.root / "artifacts/testvm/libvirt/testvm.xml").exists())
        self.run.assert_not_called()

    def fake_virsh(self, command, **kwargs):
        if "stdout_log" in kwargs:
            action = command[3:]
            output = "testvm\n" if action == ["list", "--all", "--name"] else ""
            if action == ["dumpxml", "testvm"]:
                output = f"<domain><os><nvram>{self.root}/vars.fd</nvram></os></domain>"
            kwargs["stdout_log"].write_text(output)
        elif command[3] == "undefine":
            (self.root / "vars.fd").unlink(missing_ok=True)

    def test_existing_refused_and_replace_preserves_nvram(self):
        self.create_disk()
        self.run.side_effect = self.fake_virsh
        with self.assertRaisesRegex(VMError, "already exists"):
            lifecycle.cmd_export_libvirt(self.args())
        nvram = self.root / "vars.fd"
        nvram.write_bytes(b"guest firmware state")
        lifecycle.cmd_export_libvirt(self.args("--replace"))
        self.assertEqual(nvram.read_bytes(), b"guest firmware state")
        commands = [c.args[0] for c in self.run.call_args_list]
        self.assertIn(["virsh", "--connect", "qemu:///system", "undefine", "testvm", "--nvram", "--snapshots-metadata"], commands)
        self.assertNotIn("--remove-all-storage", str(commands))

    def test_unexport_preserves_disk_and_nvram(self):
        self.create_disk()
        self.run.side_effect = self.fake_virsh
        (self.root / "vars.fd").write_bytes(b"vars")
        lifecycle.cmd_unexport_libvirt(cli.build_parser().parse_args(["unexport-libvirt", "testvm"]))
        self.assertTrue((self.root / self.vm_config["disk"]["path"]).exists())
        self.assertEqual((self.root / "vars.fd").read_bytes(), b"vars")

    def test_efi_export_hands_libvirt_qcow2_firmware_and_unexport_brings_the_vars_back(self):
        self.check_linked_firmware("qcow2")

    def test_linked_efi_export_on_libvirt_8_uses_raw_and_unexports_it(self):
        self.check_linked_firmware("raw", "8.0.0")

    def test_linked_efi_export_on_libvirt_12_uses_qcow2_and_unexports_it(self):
        self.check_linked_firmware("qcow2", "12.0.0")

    def check_linked_firmware(self, fmt, version=None):
        suffix = "qcow2" if fmt == "qcow2" else "fd"
        # libvirt takes virt-manager's snapshots of a pflash VM only with qcow2 variables
        # (and a loader in the same format); vmctl keeps its raw file.
        self.create_disk()
        code, template, vars_raw = [self.root / n for n in ("code.fd", "template.fd", "vars.fd")]
        self.vm_config["firmware"] = {"type": "efi", "code": str(code), "vars_template": str(template), "vars_path": str(vars_raw)}
        self.write_config_dir()
        vars_raw.write_bytes(b"vmctl vars")
        enter_context(self, mock.patch.object(qemu, "resolve_efi_firmware", return_value=(code, template, vars_raw)))
        def export_virsh(command, **kwargs):
            if "stdout_log" in kwargs:
                output = self.version_output(version) if "version" in command else ""
                kwargs["stdout_log"].write_text(output)
        self.run.side_effect = export_virsh
        with mock.patch.object(libvirt.ui, "print_note") as note:
            lifecycle.cmd_export_libvirt(self.args(*([] if version else ["--no-define"])))
        raw_notes = [call.args[0] for call in note.call_args_list if "raw firmware" in call.args[0]]
        self.assertEqual(bool(raw_notes), fmt == "raw")
        if raw_notes:
            self.assertIn("libvirt 8.0.0", raw_notes[0])
            self.assertIn("internal snapshots are not available", raw_notes[0])
        copies = self.root / "artifacts/testvm/libvirt"
        converts = [c.args[0] for c in self.run.call_args_list if c.args[0][0] == "qemu-img"]
        self.assertEqual(converts, [["qemu-img", "convert", "-f", "raw", "-O", fmt, str(code), str(copies / f"OVMF_CODE.{suffix}")],
                                    ["qemu-img", "convert", "-f", "raw", "-O", fmt, str(vars_raw), str(copies / f"OVMF_VARS.{suffix}")]])
        xml = ET.parse(copies / "testvm.xml").getroot()
        self.assertEqual((xml.find("os/loader").get("format"), xml.find("os/nvram").get("format")), (fmt, fmt) if fmt == "qcow2" else (None, None))
        self.assertEqual(xml.findtext("os/nvram"), str((copies / f"OVMF_VARS.{suffix}").resolve()))
        self.assertEqual(xml.findtext("os/loader"), str((copies / f"OVMF_CODE.{suffix}").resolve()))
        # Back: variables changed by the guest are preserved across undefine and restored as raw.
        (copies / f"OVMF_VARS.{suffix}").write_bytes(b"guest vars")
        (copies / f"OVMF_CODE.{suffix}").write_bytes(b"code")
        self.run.reset_mock()
        def virsh(command, **kwargs):
            if "stdout_log" in kwargs:
                out = {"dumpxml": f"<domain><os><nvram>{copies}/OVMF_VARS.{suffix}</nvram></os></domain>"}.get(command[3], "")
                kwargs["stdout_log"].write_text(out)
            elif command[:2] == ["qemu-img", "convert"]:
                Path(command[-1]).write_bytes(b"raw " + Path(command[-2]).read_bytes())
            elif command[3] == "undefine":
                (copies / f"OVMF_VARS.{suffix}").unlink()
        self.run.side_effect = virsh
        lifecycle.cmd_unexport_libvirt(cli.build_parser().parse_args(["unexport-libvirt", "testvm"]))
        commands = [c.args[0] for c in self.run.call_args_list]
        self.assertIn(["virsh", "--connect", "qemu:///system", "undefine", "testvm", "--nvram", "--snapshots-metadata"], commands)
        self.assertIn(["qemu-img", "convert", "-f", fmt, "-O", "raw", str(copies / f"OVMF_VARS.{suffix}"), str(vars_raw)], commands)
        self.assertEqual(vars_raw.read_bytes(), b"raw guest vars")
        self.assertFalse((copies / f"OVMF_VARS.{suffix}").exists())
        self.assertFalse((copies / f"OVMF_CODE.{suffix}").exists())

    def test_unexport_names_the_chown_when_libvirt_kept_the_disk(self):
        # A snapshot revert left windows-11's disk to libvirt-qemu (2026-10-04): vmctl start then
        # died on "Permission denied". vmctl cannot chown; it prints the command.
        disk = self.create_disk()
        with mock.patch("os.getuid", return_value=disk.stat().st_uid + 1), mock.patch("builtins.print") as printed:
            libvirt.warn_foreign_owner(self.vm_config)
        self.assertIn(f"chown {disk.stat().st_uid + 1}:", printed.call_args.args[0])
        self.assertNotIn("sudo", printed.call_args.args[0], "sudo may not exist: the command is run as root by the user")
        self.assertIn(str(disk), printed.call_args.args[0])
        with mock.patch("builtins.print") as printed:
            libvirt.warn_foreign_owner(self.vm_config)
        printed.assert_not_called()

    def pool_virsh(self, existing="", version=None):
        """libvirtd's side of --copy-to-pool: a dir pool under the temp root, volumes as files."""
        pool = self.root / "pool"
        def virsh(command, **kwargs):
            if command[:2] == ["env", "LC_ALL=C"]:
                command = command[2:]
            if command[0] != "virsh":
                if command[:2] == ["qemu-img", "convert"]:
                    Path(command[-1]).write_bytes(b"flat")
                return
            action = command[3:]
            if "stdout_log" in kwargs:
                out = {"version": self.version_output(version) if version else "",
                       "pool-dumpxml": f"<pool type='dir'><target><path>{pool}</path></target></pool>",
                       "vol-list": f" Name   Path\n----------\n {existing}  {pool}/{existing}\n" if existing else " Name Path\n----\n",
                       }.get(action[0], "")
                kwargs["stdout_log"].write_text(out)
            elif action[0] == "vol-create":
                volume = ET.parse(action[2]).getroot()
                self.modes[volume.findtext("name")] = volume.findtext("target/permissions/mode")
            elif action[0] == "vol-upload":
                self.uploaded[action[3]] = Path(action[4]).read_bytes()
        self.uploaded, self.modes = {}, {}
        self.run.side_effect = virsh
        return pool

    def test_copy_to_pool_makes_an_independent_domain(self):
        self.check_pool_firmware("qcow2", "12.0.0")

    def test_copy_to_pool_on_libvirt_8_uses_raw(self):
        self.check_pool_firmware("raw", "8.0.0")

    def check_pool_firmware(self, fmt, version):
        suffix = "qcow2" if fmt == "qcow2" else "fd"
        disk = self.create_disk()
        code, template, vars_raw = [self.root / n for n in ("code.fd", "template.fd", "vars.fd")]
        self.vm_config["firmware"] = {"type": "efi", "code": str(code), "vars_template": str(template), "vars_path": str(vars_raw)}
        self.write_config_dir()
        vars_raw.write_bytes(b"vmctl vars")
        enter_context(self, mock.patch.object(qemu, "resolve_efi_firmware", return_value=(code, template, vars_raw)))
        keys = self.root / "artifacts/testvm/ssh"
        keys.mkdir(parents=True)
        (keys / "id_ed25519").write_text("private")
        pool = self.pool_virsh(version=version)
        lifecycle.cmd_export_libvirt(self.args("--copy-to-pool"))
        self.assertEqual((self.root / "artifacts/testvm/libvirt-copies/testvm/ssh/id_ed25519").read_text(), "private",
                         "the copy's guest knows this key: kept where clean does not reach")
        self.assertEqual(sorted(self.uploaded), [f"testvm-OVMF_CODE.{suffix}", f"testvm-OVMF_VARS.{suffix}", "testvm.qcow2"])
        self.assertEqual(self.uploaded["testvm.qcow2"], b"disk", "the profile's disk itself is uploaded, no staging copy")
        # libvirt hands QEMU the disk and the vars at start, never the read-only loader.
        self.assertEqual(self.modes, {"testvm.qcow2": "0600", f"testvm-OVMF_CODE.{suffix}": "0644", f"testvm-OVMF_VARS.{suffix}": "0600"})
        self.assertEqual(list(disk.parent.glob(".pool-*")), [], "volume definitions removed")
        xml = ET.parse(self.root / "artifacts/testvm/libvirt/testvm.xml").getroot()
        self.assertEqual(xml.find("devices/disk/source").get("file"), str(pool / "testvm.qcow2"))
        expected_format = fmt if fmt == "qcow2" else None
        self.assertEqual(xml.find("os/loader").get("format"), expected_format)
        self.assertEqual(xml.find("os/nvram").get("format"), expected_format)
        self.assertEqual(xml.findtext("os/loader"), str(pool / f"testvm-OVMF_CODE.{suffix}"))
        self.assertEqual(xml.findtext("os/nvram"), str(pool / f"testvm-OVMF_VARS.{suffix}"))
        self.assertEqual(xml.find(f"metadata/{{{libvirt.METADATA_NS}}}export").get("mode"), "copy")
        self.assertEqual(disk.read_text(), "disk")
        self.assertEqual(list((self.root / "artifacts/testvm/libvirt").glob(".pool-*")), [], "staging copies removed")
        commands = [c.args[0] for c in self.run.call_args_list]
        self.assertIn(["virsh", "--connect", "qemu:///system", "pool-refresh", "default"], commands)
        self.assertNotIn("--remove-all-storage", str(commands))
        converts = [c for c in commands if c[:2] == ["qemu-img", "convert"]]
        staging = self.root / "artifacts/testvm/libvirt"
        self.assertEqual(converts, [
            ["qemu-img", "convert", "-f", "raw", "-O", fmt, str(code), str(staging / f".pool-testvm-OVMF_CODE.{suffix}")],
            ["qemu-img", "convert", "-f", "raw", "-O", fmt, str(vars_raw), str(staging / f".pool-testvm-OVMF_VARS.{suffix}")]])

    def test_copy_to_pool_flattens_an_overlay_and_refuses_taken_volumes(self):
        disk = self.create_disk()
        disk.write_bytes(b"QFI\xfb" + bytes(4) + (512).to_bytes(8, "big"))
        self.pool_virsh(existing="other.qcow2")
        lifecycle.cmd_export_libvirt(self.args("--copy-to-pool", "--pool", "default"))
        self.assertEqual(self.uploaded["testvm.qcow2"], b"flat", "a cloud-image overlay is flattened first")
        self.pool_virsh(existing="testvm.qcow2")
        with self.assertRaisesRegex(VMError, "already has testvm.qcow2"):
            lifecycle.cmd_export_libvirt(self.args("--copy-to-pool"))
        self.assertEqual(self.uploaded, {})
        with self.assertRaisesRegex(VMError, "drop --no-define"):
            lifecycle.cmd_export_libvirt(self.args("--copy-to-pool", "--no-define"))

    def test_a_copied_domain_does_not_guard_the_disk_and_unexport_brings_it_back(self):
        self.check_take_back_copy("qcow2")

    def test_take_back_raw_copy(self):
        self.check_take_back_copy("raw")

    def check_take_back_copy(self, fmt):
        suffix = "qcow2" if fmt == "qcow2" else "fd"
        pool = "/var/lib/libvirt/images"
        code, template, vars_raw = [self.root / n for n in ("code.fd", "template.fd", "vars.fd")]
        self.vm_config["firmware"] = {"type": "efi", "code": str(code), "vars_template": str(template), "vars_path": str(vars_raw)}
        self.write_config_dir()
        enter_context(self, mock.patch.object(qemu, "resolve_efi_firmware", return_value=(code, template, vars_raw)))
        copy = (f"<domain xmlns:v='{libvirt.METADATA_NS}'><metadata><v:export mode='copy' profile='testvm'/></metadata>"
                f"<os><loader>{pool}/testvm-OVMF_CODE.{suffix}</loader><nvram>{pool}/testvm-OVMF_VARS.{suffix}</nvram></os>"
                f"<devices><disk device='disk'><source file='{pool}/testvm.qcow2'/></disk>"
                f"<disk device='disk'><source file='{pool}/testvm-added-later.qcow2'/></disk>"
                f"<disk device='cdrom'><source file='{pool}/testvm.iso'/></disk></devices></domain>")
        outputs = {"list": "", "dumpxml": copy}
        def virsh(command, **kwargs):
            if "stdout_log" in kwargs:
                kwargs["stdout_log"].write_text(outputs.get(command[3], ""))
            elif command[3:4] == ["vol-download"]:
                Path(command[5]).write_bytes(b"copy of " + Path(command[4]).name.encode())
            elif command[:2] == ["qemu-img", "convert"]:
                Path(command[-1]).write_bytes(b"raw " + Path(command[-2]).read_bytes())
        self.run.side_effect = virsh
        self.assertFalse(lifecycle.libvirt_domain_defined("testvm"))
        def unexport(*extra):
            lifecycle.cmd_unexport_libvirt(cli.build_parser().parse_args(["unexport-libvirt", "testvm", *extra]))
        def changes():
            return [c.args[0] for c in self.run.call_args_list if "stdout_log" not in c.kwargs]
        # vmctl's own disk has data: the two went separate ways, the copy never overwrites it unasked.
        disk = self.create_disk()
        disk.write_bytes(b"vmctl" + bytes(vmctl.vmstate.DATA_MIN_BYTES))
        with self.assertRaisesRegex(VMError, "--replace"):
            unexport("--yes")
        self.assertEqual((changes(), disk.read_bytes()[:5]), ([], b"vmctl"))
        keep = self.root / "artifacts/testvm/libvirt-copies/testvm"
        (keep / "ssh").mkdir(parents=True)
        (keep / "ssh/id_ed25519").write_text("copy key")
        (keep / "state.json").write_text('{"guest": {"user": "someone", "recorded_by": "install"}}')
        lifecycle.clean_vm("testvm", self.vm_config)
        self.assertFalse(disk.exists())
        self.assertTrue((keep / "ssh/id_ed25519").is_file(), "clean keeps a copy's SSH keys")
        with mock.patch.object(runtime, "confirm_default_no", return_value=False):
            with self.assertRaisesRegex(VMError, "Not confirmed"):
                unexport()
        self.assertEqual(changes(), [])
        unexport("--yes")
        virsh_cmd = ["virsh", "--connect", "qemu:///system"]
        staged_vars = disk.parent / f".libvirt-testvm-vars.{suffix}.part"
        self.assertEqual(changes(), [
            [*virsh_cmd, "vol-download", f"{pool}/testvm.qcow2", str(disk.parent / ".libvirt-testvm.qcow2.part"), "--sparse"],
            [*virsh_cmd, "vol-download", f"{pool}/testvm-OVMF_VARS.{suffix}", str(staged_vars)],
            ["qemu-img", "convert", "-f", fmt, "-O", "raw", str(staged_vars), str(vars_raw.parent / ".libvirt-testvm-vars.raw.part")],
            [*virsh_cmd, "undefine", "testvm", "--keep-nvram", "--snapshots-metadata"],
            [*virsh_cmd, "vol-delete", f"{pool}/testvm.qcow2"],
            [*virsh_cmd, "vol-delete", f"{pool}/testvm-OVMF_CODE.{suffix}"],
            [*virsh_cmd, "vol-delete", f"{pool}/testvm-OVMF_VARS.{suffix}"]],
            "copied back first, then only the volumes the copy was made with; never --remove-all-storage")
        self.assertEqual(disk.read_bytes(), b"copy of testvm.qcow2")
        self.assertEqual(vars_raw.read_bytes(), f"raw copy of testvm-OVMF_VARS.{suffix}".encode())
        self.assertEqual(list(disk.parent.glob(".libvirt-*")) + list(vars_raw.parent.glob(".libvirt-*")), [])
        self.assertEqual(vmctl.vmstate.load("testvm")["origin"]["kind"], "libvirt")
        self.assertEqual(vmctl.vmstate.load("testvm")["guest"]["user"], "someone")
        self.assertEqual((self.root / "artifacts/testvm/ssh/id_ed25519").read_text(), "copy key")
        self.assertFalse(keep.parent.exists(), "the kept keys are vmctl's again")
        outputs["list"] = "testvm\n"
        with self.assertRaisesRegex(VMError, "running"):
            unexport("--yes", "--replace")
        outputs["dumpxml"] = "<domain/>"
        self.assertTrue(lifecycle.libvirt_domain_defined("testvm"))

    def test_boot_options_from_libvirt_are_forgotten(self):
        # The OVMF vars libvirt 8 handed back: BootOrder names libvirt's disk path; everything else stays.
        import struct
        def variable(name, data, state=libvirt.VAR_ADDED):
            raw_name = (name + "\0").encode("utf-16le")
            header = struct.pack("<HBxI8s16sI", 0x55AA, state, 7, bytes(8), bytes(16), 0)
            header += struct.pack("<II16s", len(raw_name), len(data), bytes(16))
            blob = header + raw_name + data
            return blob + bytes(-len(blob) % 4)
        fv = bytearray(0x48)
        fv[0x30:0x32] = (0x48).to_bytes(2, "little")
        names = ["BootOrder", "Boot0000", "Boot0001", "Boot0007", "BootOrder", "SbatLevel", "Timeout"]
        body = b"".join(variable(n, b"data", state=0x3C if i == 0 else libvirt.VAR_ADDED) for i, n in enumerate(names))
        store = libvirt.AUTH_VARIABLE_STORE + (0x1C + len(body) + 64).to_bytes(4, "little") + bytes(8) + body
        vars_file = self.root / "vars.fd"
        vars_file.write_bytes(bytes(fv) + store + b"\xff" * 64)
        size = vars_file.stat().st_size
        self.assertTrue(libvirt.forget_boot_options(vars_file))
        data = vars_file.read_bytes()
        states, offset = {}, 0x48 + 0x1C
        while data[offset:offset + 2] == b"\xaa\x55":
            name_size, data_size = struct.unpack_from("<II", data, offset + 0x24)
            name = data[offset + 60:offset + 60 + name_size].decode("utf-16le").rstrip("\0")
            states.setdefault(name, []).append(data[offset + 2])
            offset = (offset + 60 + name_size + data_size + 3) & ~3
        self.assertEqual(states, {"BootOrder": [0x3C, 0x3D], "Boot0000": [0x3D], "Boot0001": [0x3D], "Boot0007": [0x3D],
                                  "SbatLevel": [libvirt.VAR_ADDED], "Timeout": [libvirt.VAR_ADDED]})
        self.assertEqual(vars_file.stat().st_size, size)
        self.assertFalse(libvirt.forget_boot_options(vars_file), "nothing left to forget")
        vars_file.write_bytes(b"not an OVMF store")
        self.assertFalse(libvirt.forget_boot_options(vars_file))

    def test_active_libvirt_domain_refused(self):
        with mock.patch.object(libvirt, "virsh_output", return_value="testvm\n"):
            with self.assertRaisesRegex(VMError, "running"):
                libvirt.undefine("qemu:///system", "testvm", False)
        self.run.assert_not_called()

    def test_invalid_name(self):
        for name in ("../escape", "-option", "bad/name"):
            with self.assertRaises(VMError):
                libvirt.render_domain_xml(name, self.vm_config)
