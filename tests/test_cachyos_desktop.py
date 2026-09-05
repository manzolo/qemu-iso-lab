import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "vms/profile-files/cachyos-local/bin/cachyos-post-install"


class CachyosDesktopTests(unittest.TestCase):
    def run_check(self, *, missing_noctalia=False, invalid_config=False, ready_after=1, visible=True,
                  inhibitors=""):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commands = root / "bin"
            commands.mkdir()
            config = root / ".config/niri/config.kdl"
            config.parent.mkdir(parents=True)
            config.write_text('// Test configuration\n', encoding="utf-8")
            mock_command = '''#!/bin/bash
printf '%s %s\n' "${0##*/}" "$*" >> "$TRACE"
case "${0##*/}" in
  niri) exit "$INVALID_CONFIG" ;;
  systemd-run)
    count=0
    if [[ -f "$COUNTER" ]]; then read -r count < "$COUNTER"; fi
    count=$((count + 1))
    printf '%s\n' "$count" > "$COUNTER"
    if ((count < READY_AFTER)); then exit 1; fi
    printf '{"barVisible": %s}\n' "$BAR_VISIBLE"
    ;;
  systemd-inhibit) printf '%s\n' "$INHIBITORS" ;;
esac
'''
            for command in (
                "niri", "noctalia", "alacritty", "firefox", "nautilus",
                "xwayland-satellite", "systemctl", "systemd-run",
                "xdg-user-dirs-update", "sleep", "qs", "systemd-inhibit",
            ):
                if command == "noctalia" and missing_noctalia:
                    continue
                path = commands / command
                path.write_text(mock_command, encoding="utf-8")
                path.chmod(0o755)
            trace = root / "trace"
            result = subprocess.run(
                ["/bin/bash", str(SCRIPT)], capture_output=True, text=True,
                env={
                    **os.environ, "HOME": tmp, "PATH": str(commands),
                    "TRACE": str(trace), "COUNTER": str(root / "counter"),
                    "INVALID_CONFIG": str(int(invalid_config)),
                    "READY_AFTER": str(ready_after),
                    "BAR_VISIBLE": str(visible).lower(),
                    "INHIBITORS": inhibitors,
                },
            )
            return result, trace.read_text() if trace.exists() else ""

    def test_desktop_check_uses_graphical_environment_and_current_noctalia(self):
        result, trace = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("niri validate", trace)
        self.assertIn("systemctl --user is-active --quiet niri.service", trace)
        self.assertIn("systemd-run --user --quiet --wait --pipe timeout 5 noctalia msg status", trace)
        self.assertIn("systemd-inhibit --list --no-pager", trace)
        self.assertIn("CachyOS desktop ready", result.stdout)

    def test_quickshell_alone_does_not_satisfy_noctalia_check(self):
        result, _ = self.run_check(missing_noctalia=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing CachyOS desktop component: noctalia", result.stderr)

    def test_invalid_niri_config_fails_before_runtime_probe(self):
        result, trace = self.run_check(invalid_config=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("systemd-run", trace)

    def test_desktop_check_waits_for_shell_startup(self):
        result, trace = self.run_check(ready_after=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trace.count("noctalia msg status"), 3)

    def test_installed_but_unresponsive_shell_fails_provisioning(self):
        result, trace = self.run_check(ready_after=100)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(trace.count("noctalia msg status"), 15)
        self.assertIn("Remove host niri/DankMaterialShell imports", result.stderr)

    def test_responsive_shell_without_visible_bar_fails_provisioning(self):
        result, _ = self.run_check(visible=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("without a usable GPU/monitor", result.stderr)

    def test_compositor_holding_the_power_key_fails_provisioning(self):
        # niri's default: logind ignores the ACPI power button, so `vmctl stop` would suspend.
        result, _ = self.run_check(
            inhibitors="niri 1000 lab 486 niri handle-power-key Power key handling block")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inhibits the power key", result.stderr)
        self.assertIn("disable-power-key-handling", result.stderr)
        self.assertNotIn("CachyOS desktop ready", result.stdout)

    def test_bootstrap_leaves_the_power_key_to_logind(self):
        profiles = json.loads((ROOT / "vms/profiles/arch.json").read_text())["vms"]
        for name in ("cachyos-local", "cachyos-nvidia-local"):
            with self.subTest(profile=name):
                commands = profiles[name]["archinstall_config"]["bootstrap_chroot_commands"]
                matching = [c for c in commands if "disable-power-key-handling" in c]
                self.assertEqual(len(matching), 1)
                self.assertIn("/home/{{user}}/.config/niri/cfg/vm-power.kdl", matching[0])
                self.assertIn("include \"./cfg/vm-power.kdl\"", matching[0])

    def test_shipped_niri_configs_leave_the_power_key_to_logind(self):
        for profile in ("arch-noctalia-local", "arch-dms-local", "alpine-niri"):
            with self.subTest(profile=profile):
                config = (ROOT / "vms/profile-files" / profile / "niri/config.kdl").read_text()
                input_block = config[config.index("input {"):]
                input_block = input_block[:input_block.index("\n}\n")]
                self.assertIn("disable-power-key-handling", input_block)

    def test_both_profiles_deliver_and_run_desktop_check(self):
        profiles = json.loads((ROOT / "vms/profiles/arch.json").read_text())["vms"]
        for name in ("cachyos-local", "cachyos-nvidia-local"):
            with self.subTest(profile=name):
                self.assertEqual(profiles[name]["video"]["headless"], [
                    "-device", "virtio-vga-gl", "-display", "egl-headless",
                ])
                provision = profiles[name]["ssh_provision"]
                self.assertTrue(any(
                    entry["source"] == str(SCRIPT.relative_to(ROOT))
                    for entry in provision["copy_from_host"]
                ))
                command = provision["post_install_run"][-1]
                if name == "cachyos-nvidia-local":
                    nvidia_script = ROOT / "vms/profile-files/cachyos-nvidia-local/bin/cachyos-nvidia-post-install"
                    self.assertIn('bash "$HOME/bin/cachyos-post-install"', nvidia_script.read_text())
                else:
                    self.assertEqual(command, "~/bin/cachyos-post-install")

    def test_example_keeps_cachyos_desktop_defaults(self):
        example = json.loads((ROOT / "vms/profiles/local.json.example").read_text())
        self.assertNotIn("copy_from_host", example["vms"]["cachyos-local"]["ssh_provision"])


if __name__ == "__main__":
    unittest.main()
