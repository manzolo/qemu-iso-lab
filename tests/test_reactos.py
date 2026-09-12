import argparse
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.errors  # noqa: E402
import vmctl.lifecycle  # noqa: E402
import vmctl.qemu  # noqa: E402
import vmctl.reactos  # noqa: E402
import vmctl.runtime  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


FREELDR = """[FREELOADER]
DefaultOS=LiveImg
TimeOut=3

[Operating Systems]
Setup="ReactOS Setup (Text Mode)"
LiveImg="ReactOS Live Environment (Graphics Mode)"

[Setup]
BootType=ReactOSSetup
"""


class ReactOSTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.vm_config.update(firmware={"type": "bios"}, machine="pc", cpus=1, iso="isos/ReactOS.iso")
        self.vm_config.pop("iso_url", None)  # SourceForge ships a zip: the profile has no ISO URL
        self.vm_config["disk"]["interface"] = "ide"
        self.vm_config["reactos_config"] = {"fullname": "Lab User", "computer_name": "reactos-lab", "password": "lab",
                                            "display": {"XResolution": 1024, "YResolution": 768, "BitsPerPel": 32}}

    def test_unattend_answers_both_stages_and_ends_with_the_token_then_shutdown(self):
        text = vmctl.reactos.render_unattend("reactos", self.vm_config)
        for line in ("UnattendSetupEnabled = yes", "AutoPartition = 1", "FormatPartition = 1", "FsType = 0",
                     'FullName = "Lab User"', 'ComputerName = "REACTOS-LAB"', 'AdminPassword = "lab"',
                     "TimeZoneIndex = 85", "LocaleID = 409", "InstallationType = 1", "BootLoaderLocation = 2",
                     "XResolution = 1024"):
            self.assertIn(line, text)
        run_once = text.index("[GuiRunOnce]")
        shutdown = text.index("shutdown.exe /s /f /t 5")
        self.assertLess(run_once, shutdown)
        self.assertNotIn("COM1", text)  # the debug port is the kernel's; the token is its power-off line
        self.assertEqual(vmctl.reactos.BOOTSTRAP_COMPLETE_TOKEN, "Entering sleep state S5")
        # profile commands run before the shutdown, never after it
        self.vm_config["reactos_config"]["run_once"] = [r"%SystemRoot%\system32\notepad.exe"]
        text = vmctl.reactos.render_unattend("reactos", self.vm_config)
        self.assertLess(text.index("notepad.exe"), text.index("shutdown.exe"))

    def test_unattend_validation(self):
        cfg = self.vm_config["reactos_config"]
        cfg["installation_type"] = "server-core"
        cfg["fs_type"] = 1
        text = vmctl.reactos.render_unattend("reactos", self.vm_config)
        self.assertIn("InstallationType = 2", text)
        self.assertIn("FsType = 1", text)
        for bad in ({"password": ""}, {"password": 'a"b'}, {"computer_name": "bad name"}, {"installation_type": "desktop"}, {"fs_type": 7}):
            with self.subTest(bad=bad):
                self.vm_config["reactos_config"] = {"password": "lab", "computer_name": "R", **bad}
                with self.assertRaises(vmctl.errors.VMError):
                    vmctl.reactos.render_unattend("reactos", self.vm_config)
        self.vm_config["reactos_config"] = None
        self.assertIsNone(vmctl.reactos.reactos_config(self.vm_config))

    def test_freeldr_defaults_to_setup_with_a_short_timeout(self):
        text = vmctl.reactos.render_freeldr(FREELDR)
        self.assertIn("DefaultOS=Setup\nTimeOut=2\n", text)
        self.assertIn('LiveImg="ReactOS Live Environment (Graphics Mode)"', text)
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.reactos.render_freeldr(FREELDR.replace("[Setup]", "[Other]"))
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.reactos.render_freeldr("[FREELOADER]\n[Setup]\n")

    def test_ensure_install_iso_replays_the_boot_record_and_grafts_both_answer_files(self):
        source = self.root / "isos/ReactOS.iso"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"iso")
        unattend = vmctl.reactos.render_unattend("reactos", self.vm_config)
        dest = self.root / "artifacts/testvm/reactos/install.iso"

        def fake_run(cmd, **kwargs):
            if cmd[0] == "xorriso" and "-extract" in cmd:
                Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[-1]).write_text(FREELDR)
            elif cmd[0] == "xorriso" and "-outdev" in cmd:
                Path(cmd[cmd.index("-outdev") + 1]).write_bytes(b"new")
            return mock.Mock(returncode=0)

        with mock.patch.object(vmctl.runtime, "run", side_effect=fake_run) as run, \
             mock.patch.object(vmctl.runtime, "require_command"):
            built = vmctl.reactos.ensure_install_iso("testvm", self.vm_config, source, unattend)
            self.assertEqual(built, dest)
            self.assertTrue(dest.is_file())
            rebuild = [c.args[0] for c in run.call_args_list if "-outdev" in c.args[0]][0]
            self.assertEqual(rebuild[:3], ["xorriso", "-indev", str(source)])
            self.assertIn("replay", rebuild)
            for member in vmctl.reactos.UNATTEND_PATHS + (vmctl.reactos.FREELDR_PATH,):
                self.assertIn(member, rebuild)
            self.assertEqual(rebuild[-1], "-commit")
            # the answer file is CRLF on the medium
            work_unattend = Path(rebuild[rebuild.index("-map") + 1])
            self.assertTrue(work_unattend.name == "unattend.inf")
            # a second call with the same source and answers is a cache hit
            run.reset_mock()
            vmctl.reactos.ensure_install_iso("testvm", self.vm_config, source, unattend)
            run.assert_not_called()
            # changed answers rebuild
            self.vm_config["reactos_config"]["password"] = "other"
            vmctl.reactos.ensure_install_iso("testvm", self.vm_config, source, vmctl.reactos.render_unattend("reactos", self.vm_config))
            self.assertTrue(run.called)

    def test_install_media_is_an_ide_cd_after_the_disk(self):
        args = vmctl.reactos.install_media_args(Path("/x/install.iso"))
        self.assertIn("ide-cd,drive=roscd0,bus=ide.1,bootindex=2", args)
        self.assertIn("id=roscd0,file=/x/install.iso,format=raw,if=none,media=cdrom,readonly=on", args)

    def test_check_profile(self):
        vmctl.reactos.check_profile("reactos", self.vm_config)
        for bad in ({"firmware": {"type": "efi"}}, {"machine": "q35"}, {"cpus": 2}):
            with self.subTest(bad=bad):
                vm = {**self.vm_config, **bad}
                with self.assertRaises(vmctl.errors.VMError):
                    vmctl.reactos.check_profile("reactos", vm)
        vm = {**self.vm_config, "disk": {**self.vm_config["disk"], "interface": "virtio"}}
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.reactos.check_profile("reactos", vm)

    def test_cmd_bootstrap_reactos_builds_the_iso_and_waits_for_the_token_without_no_reboot(self):
        iso = self.root / "isos/ReactOS.iso"
        iso.parent.mkdir(parents=True, exist_ok=True)
        iso.write_bytes(b"x")
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=900, dry_run=False)
        with mock.patch.object(vmctl.lifecycle, "ensure_vm_disk"), \
             mock.patch.object(vmctl.iso, "ensure_iso", return_value=iso), \
             mock.patch.object(vmctl.reactos, "ensure_install_iso", return_value=self.root / "artifacts/testvm/reactos/install.iso") as build, \
             mock.patch.object(vmctl.qemu, "common_args", return_value=["qemu-system-x86_64"]) as common_args, \
             mock.patch.object(vmctl.qemu, "run_and_expect") as run_and_expect:
            self.assertEqual(self.vmctl.cmd_bootstrap_reactos(args), 0)
        build.assert_called_once()
        self.assertEqual(build.call_args.args[2], iso)
        self.assertIn("UnattendSetupEnabled = yes", build.call_args.args[3])
        kwargs = common_args.call_args.kwargs
        self.assertTrue(kwargs["serial_stdio"] and kwargs["headless"])
        self.assertNotIn("no_reboot", kwargs)  # Setup reboots twice
        self.assertEqual(kwargs["disk_bootindex"], 1)
        cmd = run_and_expect.call_args.args[0]
        self.assertIn("ide-cd,drive=roscd0,bus=ide.1,bootindex=2", cmd)
        self.assertEqual(run_and_expect.call_args.kwargs["expected_text"], vmctl.reactos.BOOTSTRAP_COMPLETE_TOKEN)
        self.assertEqual(run_and_expect.call_args.kwargs["exit_grace_sec"], vmctl.reactos.SHUTDOWN_GRACE_SEC)

    def test_cmd_bootstrap_reactos_explains_a_missing_iso_and_tolerates_it_in_dry_run(self):
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=900, dry_run=False)
        with self.assertRaisesRegex(vmctl.errors.VMError, "reactos.org/download"):
            self.vmctl.cmd_bootstrap_reactos(args)
        args.dry_run = True
        with mock.patch.object(vmctl.runtime, "require_command"):
            self.assertEqual(self.vmctl.cmd_bootstrap_reactos(args), 0)

    def test_requires_reactos_config_and_dispatches_in_check_vms(self):
        self.assertEqual(self.vmctl.local_test_mode(self.vm_config)[0], "bootstrap-reactos")
        del self.vm_config["reactos_config"]
        self.write_config_dir()
        with self.assertRaises(vmctl.errors.VMError):
            self.vmctl.cmd_bootstrap_reactos(argparse.Namespace(vm=self.vm_name, timeout=1, dry_run=True))


if __name__ == "__main__":
    unittest.main()
