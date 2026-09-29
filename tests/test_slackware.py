"""Slackware from the DVD's own shell: the profile checks, the rendered script, the boot line."""
import argparse
import io
import json
from unittest import mock

from tests._common import BaseVmctlTestCase, ROOT
from vmctl import lifecycle, slackware
from vmctl.errors import VMError


class SlackwareTests(BaseVmctlTestCase):
    def profile(self, name="slackware-15.0"):
        from vmctl import config
        tracked = config.load_tracked(ROOT / "vms" / "profiles")
        return json.loads(json.dumps(tracked[name]))

    def test_the_tracked_profiles_are_the_dvd_script_flow_on_bios_ide(self):
        for name in ("slackware-13.0", "slackware-13.37", "slackware-14.0", "slackware-14.1", "slackware-14.2", "slackware-15.0"):
            with self.subTest(name=name):
                vm = self.profile(name)
                self.assertEqual(lifecycle.local_test_mode(vm)[0], "bootstrap-slackware")
                slackware.check_profile(name, vm)
                self.assertEqual(vm["meta"]["status"], "unattended")  # all six passed live on 2026-09-29
                self.assertNotIn("systemctl", json.dumps(vm))  # sysvinit, no systemd anywhere
                self.assertEqual(vm["ssh_provision"]["key_type" if name < "slackware-14.2" else "user"],
                                 "rsa" if name < "slackware-14.2" else "lab")  # OpenSSH before 7.2: RSA + legacy options

    def test_rejects_incompatible_hardware_and_missing_identity(self):
        for section, key, value in [("firmware", "type", "efi"), ("disk", "interface", "virtio"), (None, "machine", "q35")]:
            with self.subTest(key=key):
                vm = self.profile()
                (vm[section] if section else vm)[key] = value
                with self.assertRaises(VMError):
                    slackware.check_profile("test", vm)
        vm = self.profile()
        vm["slackware_config"]["password"] = ""
        with self.assertRaisesRegex(VMError, "username and password"):
            slackware.check_profile("test", vm)
        vm = self.profile()
        vm["slackware_config"]["series"] = "a ap"
        with self.assertRaisesRegex(VMError, "list of names"):
            slackware.check_profile("test", vm)
        vm = self.profile()
        del vm["ssh_provision"]
        with self.assertRaisesRegex(VMError, "ssh_provision"):
            slackware.check_profile("test", vm)

    def test_the_script_partitions_installs_configures_and_ends_flush_token_poweroff(self):
        vm = self.profile("slackware-13.0")
        vm["slackware_config"].update(username="tester", password="s3cret", hostname="slack", timezone="Europe/Rome",
                                      keymap="it", locale="it_IT.UTF-8")
        script = slackware.render_install_script("test", vm, ["ssh-rsa TESTKEY vmctl"])
        self.assertTrue(script.startswith("#!/bin/bash\n"))
        self.assertIn(f"trap 'echo \"{slackware.BOOTSTRAP_FAILED_TOKEN}: line $LINENO: $BASH_COMMAND\"", script)
        # order: DVD, disk, packages, configuration, LILO, then flush -> token -> poweroff
        order = ["Mounting the install DVD", "printf ',,L,*\\n' | sfdisk", "mkfs.ext4", "Installing packages", "installpkg",
                 "USE_DHCP[0]=\"yes\"", "root:$PASSWORD", "useradd -m -g users", "NOPASSWD: ALL", "ssh-rsa TESTKEY vmctl",
                 "UseDNS no", "agetty -L ttyS0 115200 vt100", "exec /bin/su - $USERNAME", "--autologin",
                 "exec startx >\"$HOME/.startx.log\" 2>&1", "exec /bin/sh /etc/X11/xinit/$XINITRC", "==> LILO", "chroot /mnt /sbin/lilo", "umount /mnt\nsync\n",
                 slackware.BOOTSTRAP_COMPLETE_TOKEN, "vmctl_poweroff\n"]
        positions = [script.index(piece) for piece in order]
        self.assertEqual(positions, sorted(positions), order)
        self.assertIn('SERIES="a ap l n x xap"', script)
        self.assertIn('EXTRA_PACKAGES="sudo iproute2 xfce"', script)  # the base's sudo + iproute2 (OPT on 15.0) plus the child's xfce
        self.assertIn("xscreensaver", script)  # the default exclusions
        self.assertIn("xfce4-pulseaudio-plugin", script)  # no sound server: the plugin would loop-crash with a dialog
        self.assertIn("mozilla-firefox", script)
        self.assertIn('grep -E \':(ADD|REC)$\' "$dir/tagfile"', script)
        self.assertIn('echo "${b%-*-*-*} $f"', script)  # exact names, never a prefix match
        self.assertIn("HOSTNAME_SHORT=slack", script)
        self.assertNotIn("| grep -q", script)  # pipefail + grep -q quitting early = SIGPIPE for ls (iproute2, 2026-09-29)
        self.assertNotIn("| head -1", script)
        self.assertIn('installed_log "$p"', script)
        self.assertIn("[ -x /mnt/usr/bin/xfce4-tips ]", script)  # Xfce 4.6's tips window: hidden through the user's autostart override
        self.assertIn('[ "$XINITRC" = xinitrc.xfce ] && [ -f /mnt/etc/xdg/xfce4/panel/default.xml ]', script)  # Xfce 4.10/4.12: no first-start panel question
        self.assertIn("loadkeys %s.map", script)
        self.assertIn("export LANG=$LOCALE", script)
        self.assertIn('console=tty0 console=ttyS0,115200', script)  # LILO append: the serial getty and the log
        self.assertNotIn("systemctl", script)

    def test_kde_on_14_2_and_plasma_on_15_0(self):
        for name, xinitrc, process in (("slackware-14.2", "xinitrc.kde", "plasma-desktop"), ("slackware-15.0", "xinitrc.kde", "plasmashell"),
                                       ("slackware-14.1", "xinitrc.xfce", "xfce4-session")):
            with self.subTest(name=name):
                vm = self.profile(name)
                script = slackware.render_install_script(name, vm, [])
                self.assertIn(f'XINITRC={xinitrc}', script)
                self.assertIn('FULL_SERIES="l"' if xinitrc == "xinitrc.kde" else 'FULL_SERIES=""', script)  # KDE needs the whole l series
                self.assertIn(" kde" if xinitrc == "xinitrc.kde" else " xfce", script.split('SERIES="', 1)[1].split('"', 1)[0] + " ")
                self.assertIn(f"-x {process} ", vm["ssh_provision"]["post_install_run"][0])

    def test_the_seed_and_the_boot_line(self):
        vm = self.profile()
        self.assertEqual(slackware.seed_iso_drive_args(self.root / "seed.iso"),
                         ["-drive", f"file={self.root / 'seed.iso'},format=raw,if=ide,index=3,media=cdrom,readonly=on"])
        trigger = slackware.live_trigger_command()
        self.assertTrue(trigger.endswith("sh /vmctl-seed/run.sh"))  # run.sh finds a real bash (13.x initrds call busybox ash "bash")
        run = slackware.render_run_script()
        self.assertTrue(run.startswith("#!/bin/sh\n"))
        self.assertIn('bash -c \'echo "${BASH_VERSION:-}"\' </dev/null', run)  # busybox takes `set -E` from -c and opens a shell on --version
        self.assertIn("/slackware64/a/bash-*.t?z", run)
        self.assertIn("exec \"$b\" /vmctl-seed/install.sh", run)
        self.assertNotIn("[[", run)
        self.assertIn("/dev/sr1 /dev/sr0", trigger)
        self.assertTrue(slackware.LIVE_KERNEL_APPEND.endswith("console=tty0 console=ttyS0,115200"))  # the shell on the serial port
        self.assertIn("SLACK_KERNEL=huge.s", slackware.LIVE_KERNEL_APPEND)
        self.assertEqual(vm["installer_boot"], {"kernel": "kernels/huge.s/bzImage", "initrd": "isolinux/initrd.img"})
        self.assertEqual(slackware.LIVE_LOGIN_PROMPT, "slackware login: ")
        self.assertEqual(slackware.LIVE_SHELL_PROMPT, ":/# ")

    def test_dry_run_boots_the_dvd_kernel_answers_the_login_and_runs_the_seed(self):
        profile = self.profile("slackware-15.0")
        profile.pop("extends", None)  # already resolved over its base
        self.vm_config.update(profile)
        self.vm_config["disk"]["path"] = f"artifacts/{self.vm_name}/disk.qcow2"
        self.write_config_dir()
        out = io.StringIO()
        with mock.patch.object(lifecycle.iso, "ensure_iso", return_value=self.root / "isos" / "dvd.iso"), \
             mock.patch.object(lifecycle.runtime, "require_command"), \
             mock.patch.object(lifecycle, "ensure_vm_disk"), \
             mock.patch.object(slackware, "create_seed_iso", return_value=self.root / "seed.iso"), \
             mock.patch.object(slackware, "extract_boot_artifacts", return_value=(self.root / "bzImage", self.root / "initrd.img")), \
             mock.patch.object(lifecycle.qemu, "run_and_expect") as run, \
             mock.patch.object(lifecycle, "start_installed_vm_headless"), \
             mock.patch.object(lifecycle, "run_post_install"), \
             mock.patch("sys.stdout", out):
            lifecycle.cmd_bootstrap_slackware(argparse.Namespace(vm=self.vm_name, dry_run=True, timeout=1800))
        command = run.call_args.args[0]
        self.assertIn("-cdrom", command)
        self.assertIn("if=ide,index=3,media=cdrom", " ".join(command))
        self.assertEqual(command[command.index("-append") + 1], slackware.LIVE_KERNEL_APPEND)
        self.assertEqual(run.call_args.kwargs["expected_text"], slackware.BOOTSTRAP_COMPLETE_TOKEN)
        prompts = [prompt for prompt, _ in run.call_args.kwargs["auto_inputs"]]
        self.assertEqual(prompts, [slackware.LIVE_KEYMAP_PROMPT, slackware.LIVE_LOGIN_PROMPT, slackware.LIVE_SHELL_PROMPT])
        self.assertEqual(run.call_args.kwargs["auto_inputs"][0][1], "\n")  # Enter: the US map in the installer
        self.assertFalse((self.root / "artifacts" / self.vm_name / "disk.qcow2").exists())  # a dry run writes nothing
