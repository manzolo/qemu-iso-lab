import argparse
import hashlib
import shutil
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.config  # noqa: E402
import vmctl.errors  # noqa: E402
import vmctl.iso  # noqa: E402
import vmctl.lifecycle  # noqa: E402
import vmctl.qemu  # noqa: E402
import vmctl.runtime  # noqa: E402
import vmctl.ssh  # noqa: E402
import vmctl.windows  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class WindowsRenderTests(BaseVmctlTestCase):
    def _windows_vm(self, **extra) -> None:
        self.vm_config["windows_config"] = {
            "username": "tester",
            "password": "s3cret&<pass>",
            "realname": "Test User",
            "computer_name": "win11 lab machine name too long",
            "edition": "Windows 11 Pro",
            "language": "it-IT",
            "timezone": "W. Europe Standard Time",
            "setup_commands": ["Write-Host 'hello'", "Set-Content -Path C:\\x.txt -Value 'a'"],
            **extra,
        }

    def _with_ssh(self) -> None:
        key = self.root / "keys" / "id_ed25519"
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_text("private\n", encoding="utf-8")
        (self.root / "keys" / "id_ed25519.pub").write_text("ssh-ed25519 AAAATEST tester@host\n", encoding="utf-8")
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2235, "ssh_key": str(key)}

    def test_autounattend_covers_every_pass(self):
        self._windows_vm()
        xml = vmctl.windows.render_autounattend(self.vm_name, self.vm_config)
        self.assertIn('<settings pass="windowsPE">', xml)
        self.assertIn('<settings pass="specialize">', xml)
        self.assertIn('<settings pass="oobeSystem">', xml)
        self.assertIn("<UILanguage>it-IT</UILanguage>", xml)
        self.assertIn("<InputLocale>it-IT</InputLocale>", xml)
        self.assertIn("<Value>Windows 11 Pro</Value>", xml)
        self.assertIn("<Key>W269N-WFGWX-YVC9B-4J6C9-T83GX</Key>", xml)
        self.assertIn("<WillWipeDisk>true</WillWipeDisk>", xml)
        self.assertIn("<Type>EFI</Type>", xml)
        self.assertIn("<TimeZone>W. Europe Standard Time</TimeZone>", xml)
        self.assertIn("<ComputerName>WIN11-LAB-MACHI</ComputerName>", xml)
        self.assertIn("<Name>tester</Name>", xml)
        self.assertIn("<DisplayName>Test User</DisplayName>", xml)
        self.assertIn("<Group>Administrators</Group>", xml)
        self.assertIn("<AutoLogon>", xml)
        # values are XML-escaped, never raw
        self.assertIn("<Value>s3cret&amp;&lt;pass&gt;</Value>", xml)
        self.assertNotIn("s3cret&<pass>", xml)

    def test_autounattend_injects_virtio_drivers_for_the_flavor_on_every_cd_letter(self):
        self._windows_vm(driver_flavor="w10")
        xml = vmctl.windows.render_autounattend(self.vm_name, self.vm_config)
        for letter in vmctl.windows.DRIVER_CD_LETTERS:
            self.assertIn(f"<Path>{letter}:\\viostor\\w10\\amd64</Path>", xml)
            self.assertIn(f"<Path>{letter}:\\NetKVM\\w10\\amd64</Path>", xml)
        self.assertNotIn("w11", xml)

    def test_autounattend_bypass_and_autologon_are_optional(self):
        self._windows_vm()
        xml = vmctl.windows.render_autounattend(self.vm_name, self.vm_config)
        self.assertIn("BypassTPMCheck", xml)
        self.assertIn("BypassSecureBootCheck", xml)
        self._windows_vm(bypass_requirements=False, auto_logon=False)
        xml = vmctl.windows.render_autounattend(self.vm_name, self.vm_config)
        self.assertNotIn("LabConfig", xml)
        self.assertNotIn("<AutoLogon>", xml)

    def test_first_logon_launches_the_seed_script_and_specialize_stays_empty(self):
        ns = {"u": "urn:schemas-microsoft-com:unattend"}
        for edition in ("Windows 10 Pro", "Windows 11 Pro"):
            with self.subTest(edition=edition):
                self._windows_vm(edition=edition)
                xml = vmctl.windows.render_autounattend(self.vm_name, self.vm_config)
                root = ET.fromstring(xml)
                # nothing runs in specialize: a failing RunSynchronousCommand blocks Setup with a dialog
                self.assertEqual(root.findall("u:settings[@pass='specialize']//u:RunSynchronousCommand", ns), [])
                self.assertNotIn("EnableLUA", xml)
                self.assertNotIn("schtasks", xml)
                logon = root.findall("u:settings[@pass='oobeSystem']//u:FirstLogonCommands/u:SynchronousCommand/u:CommandLine", ns)
                self.assertEqual(len(logon), 1)
                command = logon[0].text
                # Windows 10 drops RunOnce values longer than MAX_PATH: the launcher must stay short
                self.assertLess(len(command), vmctl.windows.RUNONCE_MAX_COMMAND_LENGTH)
                self.assertTrue(command.startswith("cmd.exe /c for %d in (D E F G) do if exist %d:\\vmctl-setup.ps1 "))
                self.assertIn("powershell.exe -NoProfile -ExecutionPolicy Bypass -File %d:\\vmctl-setup.ps1", command)
                self.assertNotIn(vmctl.windows.BOOTSTRAP_COMPLETE_TOKEN, xml)

    def test_international_core_is_declared_in_oobe_system_too(self):
        # otherwise Windows 10 shows the region/keyboard pages once Skip*OOBE are gone
        self._windows_vm()
        xml = vmctl.windows.render_autounattend(self.vm_name, self.vm_config)
        oobe_pass = xml[xml.index('<settings pass="oobeSystem">'):]
        self.assertIn('<component name="Microsoft-Windows-International-Core"', oobe_pass)
        self.assertIn("<UILanguage>it-IT</UILanguage>", oobe_pass)
        self.assertIn("<InputLocale>it-IT</InputLocale>", oobe_pass)

    def test_oobe_block_avoids_the_deprecated_skip_flags(self):
        # SkipMachineOOBE/SkipUserOOBE make Windows 10 skip the stage that runs FirstLogonCommands
        self._windows_vm()
        xml = vmctl.windows.render_autounattend(self.vm_name, self.vm_config)
        oobe = xml[xml.index("<OOBE>"):xml.index("</OOBE>")]
        self.assertNotIn("SkipMachineOOBE", oobe)
        self.assertNotIn("SkipUserOOBE", oobe)
        for flag in ("<HideEULAPage>true", "<HideLocalAccountScreen>true", "<HideOnlineAccountScreens>true",
                     "<HideWirelessSetupInOOBE>true", "<ProtectYourPC>3"):
            self.assertIn(flag, oobe)

    def test_setup_script_has_no_uac_step(self):
        self._windows_vm()
        script = vmctl.windows.render_setup_script(self.vm_name, self.vm_config)
        self.assertNotIn("EnableLUA", script)
        self.assertNotIn("schtasks", script)
        completion = script.index("if ($script:Failures.Count -eq 0)")
        self.assertLess(script.index("Invoke-Step 'setup command 2'"), completion)

    def test_product_key_follows_edition_or_profile(self):
        self._windows_vm(edition="Windows 11 Home")
        self.assertEqual(vmctl.windows.product_key(self.vm_config["windows_config"]), "TX9XD-98N7V-6WMQ6-BX7FG-H8Q99")
        self._windows_vm(product_key="AAAAA-BBBBB-CCCCC-DDDDD-EEEEE")
        self.assertEqual(vmctl.windows.product_key(self.vm_config["windows_config"]), "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE")
        self._windows_vm(edition="Windows 11 Ultimate Deluxe")
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.windows.render_autounattend(self.vm_name, self.vm_config)

    def test_product_key_falls_back_to_the_edition_family(self):
        # UUP dump names the image "Windows 11 Professional"; Microsoft ISOs say "Windows 11 Pro"
        self.assertEqual(vmctl.windows.product_key({"edition": "Windows 11 Professional"}), "W269N-WFGWX-YVC9B-4J6C9-T83GX")
        self.assertEqual(vmctl.windows.product_key({"edition": "Windows 10 Pro N"}), "W269N-WFGWX-YVC9B-4J6C9-T83GX")
        self.assertEqual(vmctl.windows.product_key({"edition": "Windows 11 Home Single Language"}), "TX9XD-98N7V-6WMQ6-BX7FG-H8Q99")
        self.assertEqual(vmctl.windows.edition_family("Windows 11 Starter Deluxe"), None)

    def test_image_index_replaces_the_image_name(self):
        self._windows_vm(image_index=1)
        xml = vmctl.windows.render_autounattend(self.vm_name, self.vm_config)
        self.assertIn("<Key>/IMAGE/INDEX</Key>", xml)
        self.assertIn("<Value>1</Value>", xml)
        self.assertNotIn("/IMAGE/NAME", xml)

    def test_computer_name_is_netbios_safe(self):
        self.assertEqual(vmctl.windows.computer_name("win11-unattended", {}), "WIN11-UNATTENDE")
        self.assertEqual(vmctl.windows.computer_name("x", {"computer_name": "my lab.pc"}), "MY-LAB-PC")
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.windows.computer_name("x", {"computer_name": "___"})

    def test_render_requires_username_and_plain_password(self):
        self._windows_vm(username="")
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.windows.render_autounattend(self.vm_name, self.vm_config)
        self._windows_vm(password="")
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.windows.render_setup_script(self.vm_name, self.vm_config)
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.windows.render_autounattend(self.vm_name, {**self.vm_config, "windows_config": None})

    def test_setup_script_installs_openssh_with_the_project_key_and_ends_with_token_then_shutdown(self):
        self._windows_vm()
        self._with_ssh()
        script = vmctl.windows.render_setup_script(self.vm_name, self.vm_config)
        self.assertIn("Find-MediaFile 'virtio-win-guest-tools.exe'", script)
        self.assertIn("Add-WindowsCapability -Online -Name $cap.Name", script)
        self.assertIn("Set-Service -Name sshd -StartupType Automatic", script)
        self.assertIn("New-NetFirewallRule -Name 'vmctl-sshd'", script)
        self.assertIn("administrators_authorized_keys", script)
        self.assertIn("-Value 'ssh-ed25519 AAAATEST tester@host' -Encoding Ascii", script)
        self.assertIn("    Write-Host 'hello'\n", script)
        self.assertIn("    Set-Content -Path C:\\x.txt -Value 'a'\n", script)
        self.assertIn("@('/hibernate', 'off')", script)
        self.assertIn('throw "powercfg $($pcArgs -join \' \') exited with code $LASTEXITCODE"', script)
        # progress lines reach the host through COM1 = QEMU serial stdio
        self.assertIn("New-Object System.IO.Ports.SerialPort 'COM1'", script)
        done_at = script.index("setup-done.txt")
        token_at = script.index(vmctl.windows.BOOTSTRAP_COMPLETE_TOKEN)
        shutdown_at = script.index("shutdown.exe /s /t 10 /f")
        self.assertLess(done_at, token_at)
        self.assertLess(token_at, shutdown_at)
        self.assertTrue(script.rstrip().endswith("shutdown.exe /s /t 10 /f"))

    def test_setup_script_never_reports_success_after_a_failed_step(self):
        self._windows_vm()
        self._with_ssh()
        script = vmctl.windows.render_setup_script(self.vm_name, self.vm_config)
        self.assertIn("$ErrorActionPreference = 'Stop'", script)
        self.assertNotIn("$ErrorActionPreference = 'Continue'", script)
        # every action is a recorded step, the success token is gated on zero failures
        self.assertIn("function Invoke-Step", script)
        self.assertIn("$script:Failures += $Name", script)
        self.assertIn("if ($script:Failures.Count -eq 0) {", script)
        success_branch = script[script.index("if ($script:Failures.Count -eq 0) {"):]
        self.assertIn(vmctl.windows.BOOTSTRAP_COMPLETE_TOKEN, success_branch.split("} else {")[0])
        self.assertIn(vmctl.windows.BOOTSTRAP_FAILED_TOKEN, success_branch.split("} else {")[1])
        self.assertEqual(script.count(vmctl.windows.BOOTSTRAP_COMPLETE_TOKEN), 1)
        # native installers are judged by exit code
        self.assertIn("-Wait -PassThru", script)
        self.assertIn("if ($proc.ExitCode -notin @(0, 3010, 1641))", script)
        self.assertIn("if ((Get-Service -Name sshd).Status -ne 'Running')", script)
        # a capability installed on the very last attempt is recognised (re-check after Add-WindowsCapability)
        add_at = script.index("Add-WindowsCapability -Online -Name $cap.Name | Out-Null")
        recheck = "if ((Get-WindowsCapability -Online -Name $cap.Name).State -eq 'Installed') { $installed = $true; break }"
        self.assertLess(add_at, script.index(recheck))
        self.assertLess(script.index(recheck), script.index("Start-Sleep -Seconds 20"))
        self.assertIn("if ($LASTEXITCODE -ne 0) { throw \"icacls exited with code $LASTEXITCODE\" }", script)
        # profile commands: each one is a step and a non-zero native exit code fails it
        self.assertIn("Invoke-Step 'setup command 1' {", script)
        self.assertIn("Invoke-Step 'setup command 2' {", script)
        self.assertIn("if ($LASTEXITCODE -is [int] -and $LASTEXITCODE -ne 0) { throw \"exit code $LASTEXITCODE\" }", script)

    def test_setup_script_uses_sids_for_the_authorized_keys_acl(self):
        self._windows_vm()
        self._with_ssh()
        script = vmctl.windows.render_setup_script(self.vm_name, self.vm_config)
        self.assertIn("/grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F'", script)
        self.assertNotIn("'Administrators:F'", script)

    def test_setup_script_skips_openssh_and_guest_tools_when_disabled(self):
        self._windows_vm(install_openssh=False, install_guest_tools=False, setup_commands=[])
        script = vmctl.windows.render_setup_script(self.vm_name, self.vm_config)
        self.assertNotIn("OpenSSH", script)
        self.assertNotIn("virtio-win-guest-tools.exe", script)
        self.assertIn("# (no setup_commands in the profile)", script)
        self.assertIn(vmctl.windows.BOOTSTRAP_COMPLETE_TOKEN, script)

    def test_install_openssh_defaults_to_whether_ssh_provision_exists(self):
        self._windows_vm()
        self.assertFalse(vmctl.windows.install_openssh(self.vm_config))
        self._with_ssh()
        self.assertTrue(vmctl.windows.install_openssh(self.vm_config))
        self.vm_config["windows_config"]["install_openssh"] = False
        self.assertFalse(vmctl.windows.install_openssh(self.vm_config))

    def test_identity_fields_include_windows_config(self):
        self._windows_vm()
        self.vm_config["ssh_provision"] = {"user": "someone-else", "ssh_host_port": 2235}
        _, error = vmctl.config.resolve_vm_user(self.vm_config)
        self.assertIsNotNone(error)
        self.assertIn("windows_config.username", error)


class WindowsMediaTests(BaseVmctlTestCase):
    def _windows_vm(self) -> None:
        self.vm_config["windows_config"] = {"username": "tester", "password": "pw"}

    def test_create_seed_iso_puts_answer_file_and_script_at_the_root(self):
        self._windows_vm()
        with mock.patch.object(shutil, "which", side_effect=lambda name: "/usr/bin/xorriso" if name == "xorriso" else None), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            seed = vmctl.windows.create_windows_seed_iso(self.vm_name, self.vm_config)
        artifact_dir = self.root / "artifacts/testvm/windows"
        self.assertEqual(seed, artifact_dir / "seed.iso")
        self.assertTrue((artifact_dir / "autounattend.xml").exists())
        self.assertTrue((artifact_dir / "vmctl-setup.ps1").exists())
        cmd = run_cmd.call_args.args[0]
        self.assertIn("VMCTLSEED", cmd)
        self.assertIn(str(artifact_dir / "autounattend.xml"), cmd)

    def test_install_media_are_sata_cdroms_with_the_install_iso_after_the_disk(self):
        args = vmctl.windows.install_media_args(Path("/i/win.iso"), Path("/i/virtio.iso"), Path("/a/seed.iso"))
        devices = [args[i + 1] for i, a in enumerate(args) if a == "-device"]
        self.assertEqual(devices, ["ide-cd,drive=wincd0,bus=ide.0,bootindex=2", "ide-cd,drive=wincd1,bus=ide.1", "ide-cd,drive=wincd2,bus=ide.2"])
        drives = [args[i + 1] for i, a in enumerate(args) if a == "-drive"]
        self.assertTrue(all("media=cdrom,readonly=on" in d and "if=none" in d for d in drives))
        self.assertIn("file=/i/win.iso", drives[0])
        self.assertIn("file=/a/seed.iso", drives[2])

    def test_disk_bootindex_turns_the_virtio_drive_into_a_device(self):
        self.create_disk()
        args = vmctl.qemu.disk_args(self.vm_config, bootindex=1)
        self.assertIn("virtio-blk-pci,drive=disk0,bootindex=1", args)
        self.assertIn("if=none", args[1])
        self.assertEqual(vmctl.qemu.disk_args(self.vm_config), ["-drive", f"file={self.root / 'artifacts/testvm/disk.qcow2'},format=qcow2,if=virtio"])
        self.vm_config["disk"]["interface"] = "sata"
        self.assertIn("ide-hd,drive=disk0,bus=ahci0.0,bootindex=1", vmctl.qemu.disk_args(self.vm_config, bootindex=1))

    def test_virtio_iso_spec_defaults_and_overrides(self):
        self._windows_vm()
        spec = vmctl.windows.virtio_iso_spec(self.vm_config)
        self.assertEqual(spec["iso"], "isos/virtio-win.iso")
        self.assertIn("fedorapeople.org", spec["iso_url"])
        self.vm_config["windows_config"]["virtio_iso"] = "/addons/virtio-win-0.1.285.iso"
        with mock.patch.object(vmctl.iso, "ensure_iso", return_value=Path("/addons/virtio-win-0.1.285.iso")) as ensure:
            self.assertEqual(vmctl.windows.ensure_virtio_iso(self.vm_config), Path("/addons/virtio-win-0.1.285.iso"))
        self.assertEqual(ensure.call_args.args[0]["iso"], "/addons/virtio-win-0.1.285.iso")

    def test_noprompt_iso_is_rebuilt_with_efisys_noprompt_and_cached(self):
        source = self.root / "isos" / "Win11.iso"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"iso")
        dest = self.root / "isos" / "Win11-noprompt.iso"
        work = self.root / "isos" / "Win11-noprompt.extract"

        def fake_run(cmd, dry_run=False, **kwargs):
            if cmd[:2] == ["7z", "x"]:
                (work / "efi/microsoft/boot").mkdir(parents=True)
                (work / "efi/microsoft/boot/efisys_noprompt.bin").write_bytes(b"efi")
                (work / "boot").mkdir()
                (work / "boot/bootfix.bin").write_bytes(b"press any key")
                (work / "boot/etfsboot.com").write_bytes(b"bios")
            elif "mkisofs" in cmd:
                self.assertEqual((work / "boot/bootfix.bin").read_bytes(), b"")
                Path(cmd[cmd.index("-o") + 1]).write_bytes(b"rebuilt")

        with mock.patch.object(shutil, "which", side_effect=lambda name: f"/usr/bin/{name}" if name in {"xorriso", "7z"} else None), \
             mock.patch.object(vmctl.runtime, "run", side_effect=fake_run) as run_cmd, \
             mock.patch.object(vmctl.runtime, "run_output", return_value="Volume Id    : CCCOMA_X64FRE_IT-IT_DV9\n"):
            result = vmctl.windows.ensure_noprompt_iso(source)

        self.assertEqual(result, dest)
        self.assertEqual(dest.read_bytes(), b"rebuilt")
        self.assertFalse(work.exists())
        extract_cmd, mkisofs_cmd = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(extract_cmd[:3], ["7z", "x", "-y"])
        self.assertEqual(extract_cmd[-1], str(source))
        self.assertIn("-eltorito-alt-boot", mkisofs_cmd)
        self.assertEqual(mkisofs_cmd[mkisofs_cmd.index("-e") + 1], "efi/microsoft/boot/efisys_noprompt.bin")
        self.assertEqual(mkisofs_cmd[mkisofs_cmd.index("-V") + 1], "CCCOMA_X64FRE_IT-IT_DV9")
        self.assertEqual(mkisofs_cmd[mkisofs_cmd.index("-b") + 1], "boot/etfsboot.com")
        self.assertIn("-iso-level", mkisofs_cmd)

        with mock.patch.object(vmctl.runtime, "run") as run_again:
            self.assertEqual(vmctl.windows.ensure_noprompt_iso(source), dest)
        run_again.assert_not_called()

        # a replaced source ISO (new content, same name) invalidates the cache
        source.write_bytes(b"iso v2 with more bytes")
        with mock.patch.object(shutil, "which", side_effect=lambda name: f"/usr/bin/{name}" if name in {"xorriso", "7z"} else None), \
             mock.patch.object(vmctl.runtime, "run", side_effect=fake_run) as run_rebuild, \
             mock.patch.object(vmctl.runtime, "run_output", return_value="Volume Id    : X\n"):
            self.assertEqual(vmctl.windows.ensure_noprompt_iso(source), dest)
        self.assertEqual(len(run_rebuild.call_args_list), 2)
        self.assertIn(str(source.resolve()), vmctl.windows.noprompt_source_stamp_path(dest).read_text(encoding="utf-8"))

    def test_noprompt_iso_cache_distinguishes_same_named_isos_in_other_directories(self):
        first = self.root / "isos" / "windows11.iso"
        other = self.root / "storage" / "windows11.iso"
        for path in (first, other):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(path.parent.name.encode())
        dest = vmctl.windows.noprompt_iso_path(first)
        self.assertEqual(dest, vmctl.windows.noprompt_iso_path(other))
        dest.write_bytes(b"cached")
        vmctl.windows.noprompt_source_stamp_path(dest).write_text(vmctl.windows._source_stamp(first), encoding="utf-8")
        with mock.patch.object(vmctl.runtime, "run") as run_cmd, \
             mock.patch.object(shutil, "which", return_value="/usr/bin/tool"):
            vmctl.windows.ensure_noprompt_iso(first)
            run_cmd.assert_not_called()
            with mock.patch.object(vmctl.runtime, "run_output", return_value=""):
                vmctl.windows.ensure_noprompt_iso(other, dry_run=True)
            self.assertEqual(len(run_cmd.call_args_list), 2)

    def test_noprompt_iso_needs_7z_for_the_udf_tree(self):
        with mock.patch.object(shutil, "which", side_effect=lambda name: "/usr/bin/xorriso" if name == "xorriso" else None):
            with self.assertRaises(vmctl.errors.VMError) as ctx:
                vmctl.windows.ensure_noprompt_iso(self.root / "isos" / "Win11.iso")
        self.assertIn("7z", str(ctx.exception))

    def test_noprompt_iso_dry_run_touches_nothing(self):
        source = self.root / "isos" / "Win11.iso"
        with mock.patch.object(shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            result = vmctl.windows.ensure_noprompt_iso(source, dry_run=True)
        self.assertEqual(result, self.root / "isos" / "Win11-noprompt.iso")
        self.assertEqual(len(run_cmd.call_args_list), 2)
        self.assertFalse(result.exists())


class Windows7RenderTests(BaseVmctlTestCase):
    """Windows 7 rides the same flow with a BIOS/MBR answer file, PowerShell 2.0 and no OpenSSH."""

    def _win7(self, **extra) -> None:
        self.vm_config["firmware"] = {"type": "bios"}
        self.vm_config["windows_config"] = {
            "username": "lab", "password": "lab", "realname": "Lab User", "edition": "Windows 7 Ultimate",
            "image_index": 4, "language": "it-IT", "timezone": "W. Europe Standard Time", **extra,
        }

    def test_generation_family_and_keys(self):
        self.assertEqual(vmctl.windows.edition_family("Windows 7 ULTIMATE"), "Ultimate")
        self.assertEqual(vmctl.windows.windows_generation({"edition": "Windows 7 Ultimate"}), "7")
        self.assertEqual(vmctl.windows.windows_generation({"edition": "Windows 10 Pro"}), "10")
        self.assertEqual(vmctl.windows.windows_generation({"edition": "Windows 11 Pro"}), "11")
        self.assertEqual(vmctl.windows.windows_generation({"driver_flavor": "w7"}), "7")
        self.assertEqual(vmctl.windows.product_key({"edition": "Windows 7 Ultimate"}), "33PXH-7Y6KF-2VJC9-XBBR8-HVTHH")
        self.assertEqual(vmctl.windows.product_key({"edition": "Windows 7 PROFESSIONAL"}), "FJ82H-XT6CR-J8D7P-XQJJ2-GPDD4")
        self.assertTrue(vmctl.windows.is_legacy_windows({"edition": "Windows 7 Enterprise"}))
        self.assertFalse(vmctl.windows.is_legacy_windows({"edition": "Windows 10 Pro"}))

    def test_autounattend_is_bios_mbr_without_bypass_and_trusts_the_driver_certificate(self):
        self._win7()
        xml = vmctl.windows.render_autounattend(self.vm_name, self.vm_config)
        ET.fromstring(xml)
        self.assertIn("<Label>System Reserved</Label>", xml)
        self.assertIn("<Active>true</Active>", xml)
        self.assertNotIn("<Type>EFI</Type>", xml)
        self.assertNotIn("<Type>MSR</Type>", xml)
        self.assertIn("<PartitionID>2</PartitionID>\n          </InstallTo>", xml)
        self.assertIn("<Key>/IMAGE/INDEX</Key>", xml)
        self.assertIn("<Value>4</Value>", xml)
        self.assertIn("<Key>33PXH-7Y6KF-2VJC9-XBBR8-HVTHH</Key>", xml)
        self.assertNotIn("LabConfig", xml)  # no TPM/CPU bypass on Windows 7
        self.assertIn("D:\\viostor\\w7\\amd64", xml)
        self.assertIn("G:\\NetKVM\\w7\\amd64", xml)
        self.assertIn("<SkipMachineOOBE>true</SkipMachineOOBE>", xml)
        self.assertIn("<SkipUserOOBE>true</SkipUserOOBE>", xml)
        self.assertIn("<NetworkLocation>Work</NetworkLocation>", xml)
        self.assertNotIn("HideLocalAccountScreen", xml)
        self.assertNotIn("HideOnlineAccountScreens", xml)
        # the only specialize command: the certificate import, looping over the CD letters, never failing
        self.assertIn("<Path>cmd.exe /c for %d in (D E F G) do if exist %d:\\vmctl-cert.cmd call %d:\\vmctl-cert.cmd</Path>", xml)
        self.assertIn("<CommandLine>cmd.exe /c for %d in (D E F G) do if exist %d:\\vmctl-setup.ps1", xml)

    def test_windows_11_answer_file_is_untouched_by_the_legacy_branch(self):
        self.vm_config["windows_config"] = {"username": "lab", "password": "lab", "edition": "Windows 11 Pro"}
        xml = vmctl.windows.render_autounattend(self.vm_name, self.vm_config)
        self.assertIn("<Type>EFI</Type>", xml)
        self.assertIn("LabConfig", xml)
        self.assertNotIn("SkipMachineOOBE", xml)
        self.assertNotIn("vmctl-cert.cmd", xml)
        self.assertIn("<HideLocalAccountScreen>true</HideLocalAccountScreen>", xml)

    def test_legacy_setup_script_is_powershell_2_and_has_no_openssh(self):
        self._win7(setup_commands=["Write-Host 'hi'"])
        script = vmctl.windows.render_setup_script(self.vm_name, self.vm_config)
        for forbidden in ("Add-WindowsCapability", "Get-CimInstance", "-notin", "Invoke-WebRequest", "New-NetFirewallRule", "powercfg", "virtio-win-guest-tools"):
            self.assertNotIn(forbidden, script, forbidden)
        self.assertIn("System.IO.Ports.SerialPort 'COM1'", script)
        self.assertIn(vmctl.windows.BOOTSTRAP_COMPLETE_TOKEN, script)
        self.assertIn(vmctl.windows.BOOTSTRAP_FAILED_TOKEN, script)
        self.assertIn("Invoke-Step 'setup command 1'", script)
        self.assertIn("shutdown.exe /s /t 10 /f", script)
        self.assertFalse(vmctl.windows.install_openssh(self.vm_config))
        self.vm_config["ssh_provision"] = {"user": "lab", "ssh_host_port": 2240}
        self.assertFalse(vmctl.windows.install_openssh(self.vm_config))  # even when asked: not available on 7

    def test_legacy_noprompt_iso_removes_bootfix_instead_of_emptying_it(self):
        iso = self.root / "isos/windows7.iso"
        iso.parent.mkdir(parents=True)
        iso.write_bytes(b"ISO")
        work = self.root / "isos/windows7-noprompt.extract"
        dest = vmctl.windows.noprompt_iso_path(iso)
        dest.write_bytes(b"old build with BOOTMGR.;1")
        vmctl.windows.noprompt_source_stamp_path(dest).write_text(
            vmctl.windows._source_stamp(iso) + "bootfix:removed\n", encoding="utf-8"
        )

        def fake_run(cmd, **kwargs):
            if cmd[0] == "7z":
                (work / "boot").mkdir(parents=True, exist_ok=True)
                (work / "boot" / "bootfix.bin").write_bytes(b"press any key")
                (work / "boot" / "etfsboot.com").write_bytes(b"x")
                (work / "efi/microsoft/boot").mkdir(parents=True, exist_ok=True)
                (work / "efi/microsoft/boot/efisys_noprompt.bin").write_bytes(b"x")
            elif cmd[0] == "xorriso":
                for flag in ("-D", "-N", "-d"):
                    self.assertEqual(flag in cmd, self.legacy)
                self.assertNotIn("-udf", cmd)  # unsupported by xorriso
                self.assertFalse((work / "boot" / "bootfix.bin").exists() if self.legacy else (work / "boot" / "bootfix.bin").read_bytes() != b"")
                Path(cmd[cmd.index("-o") + 1]).write_bytes(b"ISO2")

        for self.legacy in (True, False):
            with mock.patch.object(shutil, "which", return_value="/usr/bin/tool"), \
                 mock.patch.object(vmctl.runtime, "run", side_effect=fake_run), \
                 mock.patch.object(vmctl.windows, "_iso_volume_id", return_value="X"):
                dest = vmctl.windows.ensure_noprompt_iso(iso, legacy=self.legacy)
            self.assertTrue(dest.is_file())
            self.assertEqual(dest.read_bytes(), b"ISO2")  # old legacy cache must be rebuilt
            stamp = (dest.with_name(dest.name + ".source")).read_text()
            self.assertEqual("bootfix:removed" in stamp, self.legacy)  # a legacy and a normal build never share the cache
            with mock.patch.object(vmctl.runtime, "run") as cached_run:
                vmctl.windows.ensure_noprompt_iso(iso, legacy=self.legacy)
            cached_run.assert_not_called()

    def _agent_package(self):
        self._win7()
        self.vm_config["guest_agent"] = True
        payload = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1\x00\xffMSI fixture\r\n"
        package = {"path": "isos/legacy-agent.msi", "url": "https://example.invalid/legacy-agent.msi",
                   "sha256": hashlib.sha256(payload).hexdigest()}
        self.vm_config["windows_config"]["guest_agent_msi"] = package
        return package, payload

    def test_legacy_agent_stages_in_specialize_and_installs_after_setup(self):
        self._agent_package()
        xml = vmctl.windows.render_autounattend(self.vm_name, self.vm_config)
        ns = {"u": "urn:schemas-microsoft-com:unattend"}
        root = ET.fromstring(xml)
        commands = root.findall("u:settings[@pass='specialize']//u:RunSynchronousCommand", ns)
        self.assertEqual([c.find('u:Order', ns).text for c in commands], ['1', '2'])
        self.assertIn('vmctl-cert.cmd', commands[0].find('u:Path', ns).text)
        self.assertIn('vmctl-qga.cmd stage', commands[1].find('u:Path', ns).text)
        for letter in vmctl.windows.DRIVER_CD_LETTERS:
            self.assertIn(f"{letter}:\\vioserial\\w7\\amd64", xml)
        script = vmctl.windows.render_qga_script()
        install, stage = script.split('\r\n:stage\r\n')
        self.assertIn('if /i "%~1"=="stage" goto stage', install)
        self.assertIn('msiexec /i "%~dp0vmctl-qga.msi" /qn /norestart', install)
        self.assertIn('> "C:\\vmctl-qga-exit.txt" echo %RESULT%', install)
        self.assertNotIn('msiexec', stage)
        self.assertIn('copy /y "%~dp0vmctl-qga.msi"', stage)
        self.assertIn('copy /y "%~f0" "C:\\Windows\\Setup\\Scripts\\SetupComplete.cmd"', stage)
        for branch in (install, stage):
            self.assertTrue(branch.rstrip().endswith('exit /b 0'))
        self.assertNotIn('msiexec /a', script)
        self.assertNotIn('sc create', script)
        self.assertNotIn('guest-agent\\', script)  # never fall back to the incompatible current ISO MSI
        logon = vmctl.windows.render_legacy_setup_script(self.vm_name, self.vm_config)
        check = logon.index("Invoke-Step 'QEMU guest agent'")
        self.assertIn("Get-Content 'C:\\vmctl-qga-exit.txt' -ErrorAction Stop", logon)
        self.assertIn("Get-Service QEMU-GA -ErrorAction Stop", logon)
        self.assertLess(check, logon.index("if ($script:Failures.Count -eq 0)"))
        self.assertIn(vmctl.windows.BOOTSTRAP_FAILED_TOKEN, logon)
        self.assertLess(logon.index(vmctl.windows.BOOTSTRAP_COMPLETE_TOKEN), logon.index('shutdown.exe /s'))

    def test_legacy_seed_preserves_pinned_msi_bytes_without_external_tools(self):
        package, payload = self._agent_package()
        msi = self.root / package['path']
        msi.parent.mkdir(parents=True)
        msi.write_bytes(payload)
        with mock.patch.object(vmctl.runtime, 'run') as run_cmd, \
             mock.patch.object(shutil, 'which', return_value='/fake/tool'), \
             mock.patch.object(vmctl.iso, 'download_file') as download:
            seed = vmctl.windows.create_windows_seed_iso(self.vm_name, self.vm_config)
        download.assert_not_called()
        self.assertEqual((seed.parent / 'vmctl-qga.msi').read_bytes(), payload)
        self.assertIn(str(seed.parent / 'vmctl-qga.msi'), run_cmd.call_args.args[0])

    def test_legacy_seed_dry_run_needs_no_msi_or_download(self):
        self._agent_package()
        with mock.patch.object(vmctl.runtime, 'run'), \
             mock.patch.object(shutil, 'which', return_value='/fake/tool'), \
             mock.patch('urllib.request.urlopen') as urlopen:
            seed = vmctl.windows.create_windows_seed_iso(self.vm_name, self.vm_config, dry_run=True)
        urlopen.assert_not_called()
        self.assertFalse((seed.parent / 'vmctl-qga.msi').exists())
        self.assertFalse(seed.exists())

    def test_legacy_agent_rejects_missing_pin_and_redownloads_corrupt_cache(self):
        self._win7()
        with self.assertRaisesRegex(vmctl.errors.VMError, 'guest_agent_msi'):
            vmctl.windows.ensure_guest_agent_msi(self.vm_config)
        package, payload = self._agent_package()
        package['sha256'] = 'invalid'
        with self.assertRaisesRegex(vmctl.errors.VMError, 'SHA-256'):
            vmctl.windows.ensure_guest_agent_msi(self.vm_config)
        package['sha256'] = hashlib.sha256(payload).hexdigest()
        msi = self.root / package['path']
        msi.parent.mkdir(parents=True)
        msi.write_bytes(b'wrong package')
        def download(url, destination, **kwargs):
            self.assertEqual(url, package['url'])
            self.assertEqual(kwargs['vm']['iso_sha256'], package['sha256'])
            self.assertFalse(destination.exists())
            destination.write_bytes(payload)
        with mock.patch.object(vmctl.iso, 'download_file', side_effect=download) as fetch:
            self.assertEqual(vmctl.windows.ensure_guest_agent_msi(self.vm_config), msi)
        fetch.assert_called_once()
        self.assertEqual(msi.read_bytes(), payload)

    def test_windows_10_and_11_agent_flags_leave_answer_and_seed_unchanged(self):
        for edition in ('Windows 10 Pro', 'Windows 11 Pro'):
            with self.subTest(edition=edition):
                self.vm_config['windows_config'] = {'username': 'lab', 'password': 'lab', 'edition': edition}
                self.vm_config['guest_agent'] = False
                without_agent = vmctl.windows.render_autounattend(self.vm_name, self.vm_config)
                self.vm_config['guest_agent'] = True
                self.assertFalse(vmctl.windows.installs_guest_agent(self.vm_config))
                self.assertEqual(vmctl.windows.render_autounattend(self.vm_name, self.vm_config), without_agent)
                with mock.patch.object(vmctl.windows, 'ensure_guest_agent_msi') as msi, \
                     mock.patch.object(vmctl.cloud_init, 'create_iso_with_files') as create:
                    vmctl.windows.create_windows_seed_iso(self.vm_name, self.vm_config)
                msi.assert_not_called()
                self.assertEqual(sorted(create.call_args.args[1]), ['autounattend.xml', 'vmctl-setup.ps1'])

    def test_cert_script_and_seed_contents(self):
        cert = vmctl.windows.render_cert_script()
        self.assertIn("certutil -addstore -f Root", cert)
        self.assertIn("certutil -addstore -f TrustedPublisher", cert)
        self.assertIn("Virtio_Win_Red_Hat_CA.cer", cert)
        self.assertTrue(cert.rstrip().endswith("exit /b 0"))
        self.assertIn("\r\n", cert)
        self._win7()
        with mock.patch.object(vmctl.cloud_init, "create_iso_with_files", return_value=self.root / "seed.iso") as create:
            vmctl.windows.create_windows_seed_iso(self.vm_name, self.vm_config)
        self.assertEqual(sorted(create.call_args.args[1]), ["autounattend.xml", "vmctl-cert.cmd", "vmctl-setup.ps1"])
        self.vm_config["windows_config"]["edition"] = "Windows 11 Pro"
        self.vm_config["firmware"] = {"type": "efi", "code": "c", "vars_template": "t", "vars_path": "artifacts/testvm/VARS.fd"}
        with mock.patch.object(vmctl.cloud_init, "create_iso_with_files", return_value=self.root / "seed.iso") as create:
            vmctl.windows.create_windows_seed_iso(self.vm_name, self.vm_config)
        self.assertEqual(sorted(create.call_args.args[1]), ["autounattend.xml", "vmctl-setup.ps1"])


class WindowsBootstrapTests(BaseVmctlTestCase):
    def _windows_vm(self) -> None:
        self.vm_config["windows_config"] = {"username": "tester", "password": "pw"}
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2235}
        self.write_config_dir()

    def test_cmd_bootstrap_windows_boots_media_waits_for_token_and_post_installs(self):
        self.create_disk()
        self._windows_vm()
        args = argparse.Namespace(vm=self.vm_name, timeout=45, dry_run=False)

        with mock.patch.object(vmctl.iso, "ensure_iso", return_value=self.root / "isos/win11.iso"), \
             mock.patch.object(vmctl.windows, "ensure_noprompt_iso", return_value=self.root / "isos/win11-noprompt.iso") as noprompt, \
             mock.patch.object(vmctl.windows, "ensure_virtio_iso", return_value=self.root / "isos/virtio-win.iso"), \
             mock.patch.object(vmctl.lifecycle, "ensure_vm_disk"), \
             mock.patch.object(vmctl.lifecycle, "reset_vm_nvram"), \
             mock.patch.object(vmctl.windows, "create_windows_seed_iso", return_value=self.root / "artifacts/testvm/windows/seed.iso"), \
             mock.patch.object(vmctl.qemu, "common_args", side_effect=[["qemu-system-x86_64"], ["qemu-system-x86_64"]]) as common_args, \
             mock.patch.object(vmctl.qemu, "run_and_expect") as run_and_expect, \
             mock.patch.object(vmctl.lifecycle, "prepare_background_vm_slot", return_value=(self.root / "artifacts/testvm/runtime/bootstrap-start.pid", self.root / "artifacts/testvm/logs/bootstrap-start.log")), \
             mock.patch.object(vmctl.runtime, "run_background", return_value=4321) as run_background, \
             mock.patch.object(vmctl.lifecycle, "run_windows_post_install") as post_install:
            exit_code = self.vmctl.cmd_bootstrap_windows(args)

        self.assertEqual(exit_code, 0)
        noprompt.assert_called_once_with(self.root / "isos/win11.iso", dry_run=False, legacy=False)
        install_kwargs = common_args.call_args_list[0].kwargs
        self.assertTrue(install_kwargs["serial_stdio"])
        self.assertEqual(install_kwargs["disk_bootindex"], 1)
        self.assertNotIn("no_reboot", install_kwargs)  # Setup reboots on its own several times
        install_qemu_cmd = run_and_expect.call_args.args[0]
        self.assertIn("ide-cd,drive=wincd0,bus=ide.0,bootindex=2", install_qemu_cmd)
        self.assertTrue(any("win11-noprompt.iso" in a for a in install_qemu_cmd))
        self.assertTrue(any("virtio-win.iso" in a for a in install_qemu_cmd))
        self.assertTrue(any("windows/seed.iso" in a for a in install_qemu_cmd))
        self.assertNotIn("-cdrom", install_qemu_cmd)
        kwargs = run_and_expect.call_args.kwargs
        self.assertEqual(kwargs["expected_text"], vmctl.windows.BOOTSTRAP_COMPLETE_TOKEN)
        self.assertEqual(kwargs["timeout_sec"], 45)
        self.assertEqual(kwargs["exit_grace_sec"], vmctl.windows.SHUTDOWN_GRACE_SEC)
        self.assertGreaterEqual(vmctl.windows.SHUTDOWN_GRACE_SEC, 300)
        self.assertEqual(kwargs["log_path"], self.root / "artifacts/testvm/logs/bootstrap-serial.log")
        run_qemu_cmd = run_background.call_args.args[0]
        # the background boot puts the serial on a unix socket (vmctl console) that logs to post-install-serial.log
        self.assertEqual(common_args.call_args_list[-1].kwargs["serial_log"], self.root / "artifacts/testvm/logs/post-install-serial.log")
        self.assertEqual(common_args.call_args_list[-1].kwargs["serial_socket"], self.root / "artifacts/testvm/runtime/serial.sock")
        self.assertEqual((self.root / "artifacts/testvm/runtime/bootstrap-start.pid").read_text(encoding="utf-8"), "4321\n")
        post_install.assert_called_once_with(self.vm_name, self.vm_config, 45, dry_run=False)

    def test_cmd_bootstrap_windows_without_ssh_stops_after_the_install(self):
        self.create_disk()
        self.vm_config["windows_config"] = {"username": "tester", "password": "pw"}
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=45, dry_run=False)
        with mock.patch.object(vmctl.iso, "ensure_iso", return_value=self.root / "isos/win11.iso"), \
             mock.patch.object(vmctl.windows, "ensure_noprompt_iso", return_value=self.root / "isos/win11-noprompt.iso"), \
             mock.patch.object(vmctl.windows, "ensure_virtio_iso", return_value=self.root / "isos/virtio-win.iso"), \
             mock.patch.object(vmctl.lifecycle, "ensure_vm_disk"), \
             mock.patch.object(vmctl.lifecycle, "reset_vm_nvram"), \
             mock.patch.object(vmctl.windows, "create_windows_seed_iso", return_value=self.root / "artifacts/testvm/windows/seed.iso"), \
             mock.patch.object(vmctl.qemu, "common_args", return_value=["qemu-system-x86_64"]), \
             mock.patch.object(vmctl.qemu, "run_and_expect"), \
             mock.patch.object(vmctl.runtime, "run_background") as run_background:
            self.assertEqual(self.vmctl.cmd_bootstrap_windows(args), 0)
        run_background.assert_not_called()

    def test_cmd_bootstrap_windows_requires_windows_config(self):
        args = argparse.Namespace(vm=self.vm_name, timeout=45, dry_run=True)
        with self.assertRaises(vmctl.errors.VMError):
            self.vmctl.cmd_bootstrap_windows(args)

    def test_windows_post_install_uses_cmd_semantics(self):
        self._windows_vm()
        self.vm_config["ssh_provision"]["post_install_run"] = ["ver", "type C:\\vmctl\\setup.log"]
        self.vm_config["ssh_provision"]["copy_from_host"] = [{"source": str(self.root / "tools"), "dest": "C:/Users/tester/tools"}]
        (self.root / "tools").mkdir()
        with mock.patch.object(shutil, "which", return_value="/usr/bin/ssh"), \
             mock.patch.object(vmctl.ssh, "wait_for_ssh") as wait, \
             mock.patch.object(vmctl.ssh, "ensure_generated_ssh_keypair", return_value=self.root / "artifacts/testvm/ssh/id_ed25519"), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            self.vmctl.run_windows_post_install(self.vm_name, self.vm_config, 30)
        self.assertEqual(wait.call_args.kwargs["probe_command"], "exit 0")
        cmds = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(cmds[0][0], "scp")
        self.assertEqual(cmds[0][-3:], ["-r", str(self.root / "tools"), "tester@127.0.0.1:C:/Users/tester/tools"])
        self.assertEqual(cmds[1][-1], "ver")
        self.assertEqual(cmds[2][-1], "type C:\\vmctl\\setup.log")
        self.assertFalse(any("sh -lc" in " ".join(c) for c in cmds))

    def test_cmd_post_install_routes_windows_profiles_to_the_windows_flow(self):
        self._windows_vm()
        args = argparse.Namespace(vm=self.vm_name, timeout=30, dry_run=True)
        with mock.patch.object(vmctl.lifecycle, "run_windows_post_install") as win, \
             mock.patch.object(vmctl.lifecycle, "run_post_install") as linux:
            self.assertEqual(self.vmctl.cmd_post_install(args), 0)
        win.assert_called_once_with(self.vm_name, self.vm_config, 30, dry_run=True)
        linux.assert_not_called()

        del self.vm_config["windows_config"]
        self.write_config_dir()
        with mock.patch.object(vmctl.lifecycle, "run_windows_post_install") as win, \
             mock.patch.object(vmctl.lifecycle, "run_post_install") as linux:
            self.vmctl.cmd_post_install(args)
        linux.assert_called_once()
        win.assert_not_called()

    def test_cmd_bootstrap_windows_explains_a_failed_first_logon(self):
        self.create_disk()
        self._windows_vm()
        args = argparse.Namespace(vm=self.vm_name, timeout=45, dry_run=False)
        failure = vmctl.errors.VMError(
            "QEMU exited before emitting '==> Windows installation complete!'. Captured output:\n"
            "[vmctl-windows] ERROR in step 'OpenSSH Server': boom\n"
            f"{vmctl.windows.BOOTSTRAP_FAILED_TOKEN}: OpenSSH Server\n"
        )
        with mock.patch.object(vmctl.iso, "ensure_iso", return_value=self.root / "isos/win11.iso"), \
             mock.patch.object(vmctl.windows, "ensure_noprompt_iso", return_value=self.root / "isos/win11-noprompt.iso"), \
             mock.patch.object(vmctl.windows, "ensure_virtio_iso", return_value=self.root / "isos/virtio-win.iso"), \
             mock.patch.object(vmctl.lifecycle, "ensure_vm_disk"), \
             mock.patch.object(vmctl.lifecycle, "reset_vm_nvram"), \
             mock.patch.object(vmctl.windows, "create_windows_seed_iso", return_value=self.root / "artifacts/testvm/windows/seed.iso"), \
             mock.patch.object(vmctl.qemu, "common_args", return_value=["qemu-system-x86_64"]), \
             mock.patch.object(vmctl.qemu, "run_and_expect", side_effect=failure), \
             mock.patch.object(vmctl.runtime, "run_background") as run_background:
            with self.assertRaises(vmctl.errors.VMError) as ctx:
                self.vmctl.cmd_bootstrap_windows(args)
        self.assertIn("first-logon setup reported failures", str(ctx.exception))
        self.assertIn("OpenSSH Server", str(ctx.exception))
        run_background.assert_not_called()

    def test_windows_profiles_get_a_long_acpi_grace_and_a_cmd_poweroff(self):
        self._windows_vm()
        self.assertEqual(self.vmctl.poweroff_grace_sec(self.vm_config), self.vmctl.ACPI_POWEROFF_GRACE_SEC)
        self.vm_config["acpi_poweroff_grace_sec"] = 300
        self.assertEqual(self.vmctl.poweroff_grace_sec(self.vm_config), 300)
        with mock.patch.object(vmctl.ssh, "ensure_generated_ssh_keypair", return_value=self.root / "artifacts/testvm/ssh/id_ed25519"):
            cmd = self.vmctl.ssh_poweroff_command(self.vm_config)
        self.assertEqual(cmd[-1], "shutdown /s /t 0 /f")
        self.assertNotIn("systemctl", cmd)

    def test_stop_honours_the_profile_grace_period(self):
        import time as _time
        sock_path = self.root / "qmp.sock"; sock_path.write_text("")
        state = {"polls": 0}
        def alive(pid):
            state["polls"] += 1
            return "qemu" if state["polls"] < 3 else None
        with mock.patch.object(vmctl.qemu, "qmp_command", return_value=True), \
             mock.patch.object(vmctl.lifecycle, "ACPI_POWEROFF_GRACE_SEC", 0), \
             mock.patch.object(vmctl.lifecycle, "process_cmdline", side_effect=alive), \
             mock.patch.object(vmctl.lifecycle.time, "sleep"), \
             mock.patch.object(vmctl.lifecycle.os, "kill") as kill:
            rc = self.vmctl.stop_qemu_process(4242, "Stop", "background VM", qmp_socket=sock_path, grace_sec=30)
        self.assertEqual(rc, 0)
        kill.assert_not_called()  # the module default (0 s) would have gone straight to SIGTERM

    def test_copy_raw_rejects_posix_only_options(self):
        self._windows_vm()
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.ssh.post_install_copy_raw(self.vm_config, {"source": "/x", "dest": "C:/y", "dest_mode": "644"}, dry_run=True)

    def test_local_test_mode_picks_bootstrap_windows(self):
        self._windows_vm()
        self.assertEqual(self.vmctl.local_test_mode(self.vm_config)[0], "bootstrap-windows")
        del self.vm_config["ssh_provision"]
        # no SSH server (Windows 7): the matrix still installs, it only skips the post-install
        mode, note = self.vmctl.local_test_mode(self.vm_config)
        self.assertEqual(mode, "bootstrap-windows")
        self.assertIn("install only", note)

    def test_local_test_skips_when_the_iso_is_missing_and_not_downloadable(self):
        self._windows_vm()
        self.vm_config.pop("iso_url")
        note = self.vmctl.local_test_prereq_skip(self.vm_name, self.vm_config)
        self.assertIsNotNone(note)
        self.assertIn("no download source", note)
        self.vm_config["iso_url"] = "https://example.invalid/test.iso"
        self.assertIsNone(self.vmctl.local_test_prereq_skip(self.vm_name, self.vm_config))

    def test_clean_removes_windows_seed_dir(self):
        self._windows_vm()
        seed_dir = self.root / "artifacts/testvm/windows"
        seed_dir.mkdir(parents=True)
        (seed_dir / "autounattend.xml").write_text("x", encoding="utf-8")
        with mock.patch.object(vmctl.lifecycle, "cmd_stop", return_value=0):
            self.vmctl.cmd_clean(argparse.Namespace(vm=self.vm_name, all=False, dry_run=False, yes=True))
        self.assertFalse(seed_dir.exists())


if __name__ == "__main__":
    unittest.main()
