import argparse
import subprocess
import xml.etree.ElementTree as ET
from unittest import mock

from _common import BaseVmctlTestCase, enter_context
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
        # libvirt takes virt-manager's snapshots of a pflash VM only with qcow2 variables
        # (and a loader in the same format); vmctl keeps its raw file.
        self.create_disk()
        code, template, vars_raw = [self.root / n for n in ("code.fd", "template.fd", "vars.fd")]
        self.vm_config["firmware"] = {"type": "efi", "code": str(code), "vars_template": str(template), "vars_path": str(vars_raw)}
        self.write_config_dir()
        vars_raw.write_bytes(b"vmctl vars")
        enter_context(self, mock.patch.object(qemu, "resolve_efi_firmware", return_value=(code, template, vars_raw)))
        lifecycle.cmd_export_libvirt(self.args("--no-define"))
        copies = self.root / "artifacts/testvm/libvirt"
        converts = [c.args[0] for c in self.run.call_args_list if c.args[0][0] == "qemu-img"]
        self.assertEqual(converts, [["qemu-img", "convert", "-f", "raw", "-O", "qcow2", str(code), str(copies / "OVMF_CODE.qcow2")],
                                    ["qemu-img", "convert", "-f", "raw", "-O", "qcow2", str(vars_raw), str(copies / "OVMF_VARS.qcow2")]])
        xml = ET.parse(copies / "testvm.xml").getroot()
        self.assertEqual((xml.find("os/loader").get("format"), xml.find("os/nvram").get("format")), ("qcow2", "qcow2"))
        self.assertEqual(xml.findtext("os/nvram"), str((copies / "OVMF_VARS.qcow2").resolve()))
        # Back: the qcow2 variables (changed by the guest in libvirt) become vmctl's raw file again.
        (copies / "OVMF_VARS.qcow2").write_bytes(b"qcow2 vars"); (copies / "OVMF_CODE.qcow2").write_bytes(b"code")
        self.run.reset_mock()
        def virsh(command, **kwargs):
            if "stdout_log" in kwargs:
                out = {"dumpxml": f"<domain><os><nvram>{copies}/OVMF_VARS.qcow2</nvram></os></domain>"}.get(command[3], "")
                kwargs["stdout_log"].write_text(out)
        self.run.side_effect = virsh
        lifecycle.cmd_unexport_libvirt(cli.build_parser().parse_args(["unexport-libvirt", "testvm"]))
        commands = [c.args[0] for c in self.run.call_args_list]
        self.assertIn(["virsh", "--connect", "qemu:///system", "undefine", "testvm", "--nvram", "--snapshots-metadata"], commands)
        self.assertIn(["qemu-img", "convert", "-f", "qcow2", "-O", "raw", str(copies / "OVMF_VARS.qcow2"), str(vars_raw)], commands)
        self.assertFalse((copies / "OVMF_VARS.qcow2").exists())
        self.assertFalse((copies / "OVMF_CODE.qcow2").exists())

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

    def test_active_libvirt_domain_refused(self):
        with mock.patch.object(libvirt, "virsh_output", return_value="testvm\n"):
            with self.assertRaisesRegex(VMError, "running"):
                libvirt.undefine("qemu:///system", "testvm", False)
        self.run.assert_not_called()

    def test_invalid_name(self):
        for name in ("../escape", "-option", "bad/name"):
            with self.assertRaises(VMError):
                libvirt.render_domain_xml(name, self.vm_config)
