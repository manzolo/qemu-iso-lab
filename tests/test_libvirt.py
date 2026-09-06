import argparse
import subprocess
import xml.etree.ElementTree as ET
from unittest import mock

from _common import BaseVmctlTestCase
from vmctl import cli, libvirt, lifecycle, qemu, runtime
from vmctl.errors import VMError


class LibvirtTests(BaseVmctlTestCase):
    def args(self, *extra):
        argv = ["export-libvirt", self.vm_name, *extra]
        return cli.build_parser().parse_args(argv)

    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(lifecycle, "running_qemu_pid", return_value=None))
        self.enterContext(mock.patch.object(qemu, "qmp_command", return_value=False))
        self.enterContext(mock.patch("shutil.which", return_value="/fake/virsh"))
        self.run = self.enterContext(mock.patch.object(runtime, "run"))

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
        self.assertIn(["virsh", "--connect", "qemu:///system", "undefine", "testvm", "--nvram"], commands)
        self.assertNotIn("--remove-all-storage", str(commands))

    def test_unexport_preserves_disk_and_nvram(self):
        self.create_disk()
        self.run.side_effect = self.fake_virsh
        (self.root / "vars.fd").write_bytes(b"vars")
        lifecycle.cmd_unexport_libvirt(cli.build_parser().parse_args(["unexport-libvirt", "testvm"]))
        self.assertTrue((self.root / self.vm_config["disk"]["path"]).exists())
        self.assertEqual((self.root / "vars.fd").read_bytes(), b"vars")

    def test_active_libvirt_domain_refused(self):
        with mock.patch.object(libvirt, "virsh_output", return_value="testvm\n"):
            with self.assertRaisesRegex(VMError, "running"):
                libvirt.undefine("qemu:///system", "testvm", False)
        self.run.assert_not_called()

    def test_invalid_name(self):
        for name in ("../escape", "-option", "bad/name"):
            with self.assertRaises(VMError):
                libvirt.render_domain_xml(name, self.vm_config)
