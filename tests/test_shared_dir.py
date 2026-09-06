import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.errors  # noqa: E402
import vmctl.qemu  # noqa: E402
import vmctl.runtime  # noqa: E402
import vmctl.ssh  # noqa: E402
import vmctl.windows  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class SharedDirConfigTests(BaseVmctlTestCase):
    def test_config_defaults_tag_and_validates(self):
        self.assertIsNone(vmctl.qemu.shared_dir_config(self.vm_config))
        self.vm_config["shared_dir"] = {"source": "shared"}
        self.assertEqual(vmctl.qemu.shared_dir_config(self.vm_config), {"source": "shared", "tag": "shared"})
        self.vm_config["shared_dir"] = {"source": "shared", "tag": "bad tag!"}
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.qemu.shared_dir_config(self.vm_config)
        self.vm_config["shared_dir"] = {"tag": "x"}
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.qemu.shared_dir_config(self.vm_config)
        self.vm_config["shared_dir"] = "shared"
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.qemu.shared_dir_config(self.vm_config)

    def test_source_resolves_relative_to_root_and_expands_home(self):
        self.vm_config["shared_dir"] = {"source": "shared"}
        self.assertEqual(vmctl.qemu.shared_dir_source(self.vm_config), self.root / "shared")
        self.vm_config["shared_dir"] = {"source": "~/Shared"}
        self.assertEqual(vmctl.qemu.shared_dir_source(self.vm_config), Path("~/Shared").expanduser())
        self.assertEqual(vmctl.qemu.virtiofs_socket_path(self.vm_config), self.root / "artifacts/testvm/runtime/virtiofs-shared.sock")


class SharedDirQemuArgsTests(BaseVmctlTestCase):
    def test_common_args_without_shared_dir_is_unchanged(self):
        self.create_disk()
        with mock.patch.object(shutil, "which", return_value="/usr/bin/qemu-system-x86_64"):
            args = vmctl.qemu.common_args(self.vm_config, None, headless=True)
        joined = " ".join(args)
        self.assertNotIn("memory-backend", joined)
        self.assertNotIn("vhost-user-fs", joined)

    def test_common_args_adds_memfd_backend_and_vhost_user_fs_and_starts_virtiofsd(self):
        self.create_disk()
        self.vm_config["shared_dir"] = {"source": "shared", "tag": "lab"}
        with mock.patch.object(shutil, "which", return_value="/usr/bin/qemu-system-x86_64"), \
             mock.patch.object(vmctl.qemu, "ensure_virtiofsd") as ensure:
            args = vmctl.qemu.common_args(self.vm_config, None, headless=True)
        ensure.assert_called_once_with(self.vm_config, dry_run=False)
        machine = args[args.index("-machine") + 1]
        self.assertIn(",memory-backend=mem0", machine)
        self.assertIn("memory-backend-memfd,id=mem0,size=1024M,share=on", args)
        sock = self.root / "artifacts/testvm/runtime/virtiofs-lab.sock"
        self.assertIn(f"socket,id=virtiofs0,path={sock}", args)
        self.assertIn("vhost-user-fs-pci,chardev=virtiofs0,tag=lab", args)
        # the memfd object must be declared before the device that uses the shared memory
        self.assertLess(args.index("-object"), args.index("-chardev"))

    def test_ensure_virtiofsd_creates_relative_source_and_waits_for_the_socket(self):
        self.vm_config["shared_dir"] = {"source": "shared"}
        sock = self.root / "artifacts/testvm/runtime/virtiofs-shared.sock"

        def fake_background(cmd, log_path, dry_run=False, stderr_path=None):
            sock.write_text("")
            return 777

        with mock.patch.object(vmctl.qemu, "find_virtiofsd", return_value="/usr/libexec/virtiofsd"), \
             mock.patch.object(vmctl.runtime, "run_background", side_effect=fake_background) as run_bg:
            vmctl.qemu.ensure_virtiofsd(self.vm_config)
        self.assertTrue((self.root / "shared").is_dir())
        cmd = run_bg.call_args.args[0]
        self.assertEqual(cmd[0], "/usr/libexec/virtiofsd")
        self.assertEqual(cmd[cmd.index("--socket-path") + 1], str(sock))
        self.assertEqual(cmd[cmd.index("--shared-dir") + 1], str(self.root / "shared"))
        self.assertEqual(cmd[cmd.index("--sandbox") + 1], "none")
        self.assertEqual(run_bg.call_args.args[1], self.root / "artifacts/testvm/logs/virtiofsd.log")

    def test_ensure_virtiofsd_reuses_a_daemon_still_waiting_on_its_socket(self):
        # vmctl start calls common_args twice per launch: the second call must not start a
        # second daemon (it would fail on the pid lock) nor delete the first one's socket.
        self.vm_config["shared_dir"] = {"source": "shared"}
        runtime_dir = self.root / "artifacts/testvm/runtime"
        runtime_dir.mkdir(parents=True)
        (runtime_dir / "virtiofs-shared.sock").write_text("")
        (runtime_dir / "virtiofs-shared.sock.pid").write_text(f"{__import__('os').getpid()}\n")
        with mock.patch.object(vmctl.qemu, "find_virtiofsd", return_value="/usr/libexec/virtiofsd"), \
             mock.patch.object(vmctl.runtime, "run_background") as run_bg:
            vmctl.qemu.ensure_virtiofsd(self.vm_config)
        run_bg.assert_not_called()
        self.assertTrue((runtime_dir / "virtiofs-shared.sock").exists())
        # a dead pid means a stale socket: relaunch and clean the leftovers first
        (runtime_dir / "virtiofs-shared.sock.pid").write_text("999999\n")

        def fake_background(cmd, log_path, dry_run=False, stderr_path=None):
            self.assertFalse((runtime_dir / "virtiofs-shared.sock.pid").exists())
            (runtime_dir / "virtiofs-shared.sock").write_text("")
            return 1
        with mock.patch.object(vmctl.qemu, "find_virtiofsd", return_value="/usr/libexec/virtiofsd"), \
             mock.patch.object(vmctl.runtime, "run_background", side_effect=fake_background) as run_bg:
            vmctl.qemu.ensure_virtiofsd(self.vm_config)
        run_bg.assert_called_once()

    def test_ensure_virtiofsd_dry_run_and_missing_pieces(self):
        self.vm_config["shared_dir"] = {"source": "shared"}
        with mock.patch.object(vmctl.qemu, "find_virtiofsd", return_value="/usr/libexec/virtiofsd"), \
             mock.patch.object(vmctl.runtime, "run_background") as run_bg:
            vmctl.qemu.ensure_virtiofsd(self.vm_config, dry_run=True)
        run_bg.assert_not_called()
        with mock.patch.object(vmctl.qemu, "find_virtiofsd", return_value=None):
            with self.assertRaises(vmctl.errors.VMError) as ctx:
                vmctl.qemu.ensure_virtiofsd(self.vm_config)
        self.assertIn("virtiofsd", str(ctx.exception))
        self.vm_config["shared_dir"] = {"source": str(self.root / "does-not-exist")}
        with mock.patch.object(vmctl.qemu, "find_virtiofsd", return_value="/usr/libexec/virtiofsd"):
            with self.assertRaises(vmctl.errors.VMError):
                vmctl.qemu.ensure_virtiofsd(self.vm_config)

    def test_ensure_virtiofsd_fails_when_the_socket_never_appears(self):
        self.vm_config["shared_dir"] = {"source": "shared"}
        with mock.patch.object(vmctl.qemu, "find_virtiofsd", return_value="/usr/libexec/virtiofsd"), \
             mock.patch.object(vmctl.qemu, "VIRTIOFSD_SOCKET_WAIT_SEC", 0.05), \
             mock.patch.object(vmctl.runtime, "run_background", return_value=1):
            with self.assertRaises(vmctl.errors.VMError) as ctx:
                vmctl.qemu.ensure_virtiofsd(self.vm_config)
        self.assertIn("virtiofsd.log", str(ctx.exception))


class SharedDirLinuxProvisionTests(BaseVmctlTestCase):
    def _ssh_vm(self) -> None:
        self.vm_config["shared_dir"] = {"source": "shared", "tag": "lab"}
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2299, "ssh_key": str(self.root / "k")}
        (self.root / "k").write_text("k"); (self.root / "k.pub").write_text("pub")

    def test_scripts_mount_with_fstab_automount_and_link_home_and_desktop(self):
        self._ssh_vm()
        system = vmctl.ssh.shared_dir_system_script(self.vm_config)
        self.assertIn("tag=lab; mnt=/mnt/lab;", system)
        self.assertIn("nofail,x-systemd.automount,x-systemd.idle-timeout=60", system)
        self.assertIn(">> /etc/fstab", system)
        self.assertIn('mount -t virtiofs "$tag" "$mnt"', system)
        user = vmctl.ssh.shared_dir_user_script(self.vm_config)
        self.assertIn('ln -sfn "$mnt" "$HOME/$tag"', user)
        self.assertIn("xdg-user-dir DESKTOP", user)
        self.assertIn('"$HOME/Scrivania"', user)

    def test_provision_runs_root_part_with_sudo_then_user_part(self):
        self._ssh_vm()
        with mock.patch.object(vmctl.runtime, "run") as run_cmd:
            vmctl.ssh.provision_shared_dir(self.vm_config)
        cmds = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(len(cmds), 2)
        self.assertTrue(cmds[0][-1].startswith("sudo sh -lc "))
        self.assertIn("/etc/fstab", cmds[0][-1])
        self.assertTrue(cmds[1][-1].startswith("sh -lc "))
        self.assertIn("ln -sfn", cmds[1][-1])
        del self.vm_config["shared_dir"]
        with mock.patch.object(vmctl.runtime, "run") as run_cmd:
            vmctl.ssh.provision_shared_dir(self.vm_config)
        run_cmd.assert_not_called()

    def test_run_post_install_provisions_the_share_before_the_profile_steps(self):
        self._ssh_vm()
        self.vm_config["ssh_provision"]["post_install_run"] = ["echo hi"]
        self.write_config_dir()
        calls: list[str] = []
        with mock.patch.object(shutil, "which", return_value="/usr/bin/ssh"), \
             mock.patch.object(vmctl.ssh, "wait_for_ssh"), \
             mock.patch.object(vmctl.ssh, "wait_for_guest_post_install_ready"), \
             mock.patch.object(vmctl.ssh, "provision_shared_dir", side_effect=lambda *a, **k: calls.append("share")), \
             mock.patch.object(vmctl.ssh, "post_install_run", side_effect=lambda *a, **k: calls.append("run")):
            self.vmctl.run_post_install(self.vm_name, self.vm_config, 30)
        self.assertEqual(calls, ["share", "run"])


class SharedDirWindowsTests(BaseVmctlTestCase):
    def test_windows_setup_script_installs_winfsp_after_the_guest_tools_when_sharing(self):
        self.vm_config["windows_config"] = {"username": "tester", "password": "pw"}
        script = vmctl.windows.render_setup_script(self.vm_name, self.vm_config)
        self.assertNotIn("WinFSP", script)
        self.vm_config["shared_dir"] = {"source": "shared", "tag": "shared"}
        script = vmctl.windows.render_setup_script(self.vm_name, self.vm_config)
        self.assertIn("Invoke-Step 'virtiofs share (WinFSP)'", script)
        self.assertIn(vmctl.windows.DEFAULT_WINFSP_URL, script)
        self.assertIn("Set-Service -Name VirtioFsSvc -StartupType Automatic", script)
        self.assertIn("Start-Service -Name VirtioFsSvc", script)
        self.assertIn("Win32_LogicalDisk", script)
        self.assertIn("WScript.Shell", script)
        self.assertIn("('shared' + '.lnk')", script)
        self.assertIn("[Environment]::GetFolderPath('Desktop')", script)
        self.assertLess(script.index("Invoke-Step 'VirtIO guest tools'"), script.index("Invoke-Step 'virtiofs share (WinFSP)'"))
        self.assertLess(script.index("Invoke-Step 'virtiofs share (WinFSP)'"), script.index("if ($script:Failures.Count -eq 0)"))
        self.vm_config["windows_config"]["winfsp_url"] = "https://example.invalid/winfsp.msi"
        self.assertIn("https://example.invalid/winfsp.msi", vmctl.windows.render_setup_script(self.vm_name, self.vm_config))


if __name__ == "__main__":
    unittest.main()
