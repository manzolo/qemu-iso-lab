import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VMTUI_PATH = ROOT / "bin" / "vmtui"


class VmtuiTests(unittest.TestCase):
    def setUp(self):
        # A throwaway repository root: vmtui resolves every relative profile
        # path (isos/, artifacts/, PID files) against VMTUI_ROOT_DIR, so the
        # state of the developer's host never leaks into the menu facts. The
        # code itself is reached through symlinks to the real checkout.
        self.tempdir = tempfile.TemporaryDirectory()
        self.bindir = Path(self.tempdir.name)
        (self.bindir / "vmctl").symlink_to(ROOT / "vmctl", target_is_directory=True)
        (self.bindir / "bin").symlink_to(ROOT / "bin", target_is_directory=True)
        self.config_dir = self.bindir / "vms"
        profiles_dir = self.config_dir / "profiles"
        profiles_dir.mkdir(parents=True)
        for profile_path in sorted((ROOT / "vms" / "profiles").glob("*.json")):
            if profile_path.name == "local.json":
                continue
            (profiles_dir / profile_path.name).write_text(profile_path.read_text(encoding="utf-8"), encoding="utf-8")
        ssh_vm = {
            "vms": {
                "test-ssh": {
                    "name": "Test SSH VM",
                    "iso": "isos/test.iso",
                    "disk": {
                        "path": "artifacts/test-ssh/disk.qcow2",
                        "size": "16G",
                        "format": "qcow2",
                        "interface": "virtio",
                    },
                    "firmware": {
                        "type": "efi",
                        "code": "/usr/share/OVMF/OVMF_CODE_4M.fd",
                        "vars_template": "/usr/share/OVMF/OVMF_VARS_4M.fd",
                        "vars_path": "artifacts/test-ssh/OVMF_VARS.fd",
                    },
                    "memory_mb": 2048,
                    "cpus": 2,
                    "network": "user",
                    "video": {
                        "default": "std",
                        "variants": {"std": ["-vga", "std", "-display", "gtk"]},
                    },
                    "ssh_provision": {
                        "hostname": "test-ssh",
                        "user": "tester",
                        "ssh_key": "~/.ssh/id_ed25519",
                        "ssh_host_port": 2293,
                    },
                }
            }
        }
        (profiles_dir / "test-ssh.json").write_text(json.dumps(ssh_vm), encoding="utf-8")
        remotes = {
            "remotes": {
                "i9": {
                    "label": "i9.lan",
                    "ssh_target": "lab@host.lan",
                    "project_dir": "/home/lab/qemu-iso-lab",
                    "local_spice_port": 5930,
                    "remote_spice_port": 5930,
                }
            }
        }
        (self.config_dir / "remotes.json").write_text(json.dumps(remotes), encoding="utf-8")
        dialog = self.bindir / "dialog"
        dialog.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
        dialog.chmod(0o755)
        # Built from scratch rather than copied from os.environ: no inherited
        # VMTUI_* knobs, no ~/.profile PATH additions, no host fzf.
        self.env = {
            "PATH": os.pathsep.join([str(self.bindir), str(Path(sys.executable).parent), "/usr/local/bin", os.defpath]),
            "HOME": str(self.bindir / "home"),
            "LC_ALL": "C.UTF-8",
            "TERM": "dumb",
            "VMTUI_TEST_MODE": "1",
            "VMTUI_UI": "dialog",
            "VMTUI_ROOT_DIR": str(self.bindir),
            "VMTUI_CONFIG_DIR": str(self.config_dir),
            "VMTUI_STATE_DIR": str(self.bindir / "state"),
        }
        (self.bindir / "home").mkdir()

    def mark_installed(self, vm_name: str) -> None:
        # enough allocated data to count as an installed OS (> 16 MiB)
        disk = self.bindir / "artifacts" / vm_name / "disk.qcow2"
        disk.parent.mkdir(parents=True, exist_ok=True)
        disk.write_bytes(b"\x01" * (24 * 1024 * 1024))

    def mark_prepared(self, vm_name: str) -> None:
        # a freshly created, still empty image: a sparse file with no allocated
        # blocks, so the test does not need qemu-img (the CI test job has none)
        disk = self.bindir / "artifacts" / vm_name / "disk.qcow2"
        disk.parent.mkdir(parents=True, exist_ok=True)
        with disk.open("wb") as handle:
            handle.truncate(1024 * 1024 * 1024)

    def tearDown(self):
        self.tempdir.cleanup()

    def run_bash(self, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-lc", script],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )

    def _unified_menu(self, vm_name: str) -> list[str]:
        result = self.run_bash(f"source bin/vmtui; list_vm_menu_items_unified {vm_name}")
        return result.stdout.splitlines()

    def test_all_automatic_install_commands_detach(self):
        commands = [
            "bootstrap-windows", "bootstrap-alpine", "bootstrap-archinstall",
            "bootstrap-omarchy", "bootstrap-preseed", "bootstrap-kickstart",
            "bootstrap-autoyast", "bootstrap-pfsense", "bootstrap-unattended",
            "install-unattended", "install-omarchy",
        ]
        script = (
            "source bin/vmtui; current_vm=test-ssh; "
            "run_vmctl_background() { printf 'detached %s\\n' \"$*\"; }; "
            + "; ".join(f"run_vmctl {command} test-ssh" for command in commands)
        )
        self.assertEqual(self.run_bash(script).stdout.splitlines(),
                         [f"detached {command} test-ssh" for command in commands])

    def test_detached_installer_does_not_prompt_for_premature_first_boot(self):
        result = self.run_bash(
            "source bin/vmtui; current_vm=test-ssh; "
            "confirm_box() { return 0; }; run_vmctl_video() { return 0; }; "
            "prompt_post_install() { echo premature; }; "
            "run_installer_flow title message install-unattended test-ssh"
        )
        self.assertNotIn("premature", result.stdout)

    def test_installing_vm_menu_offers_monitoring_without_conflicting_actions(self):
        for running in (0, 1):
            with self.subTest(running=running):
                result = self.run_bash(
                    "source bin/vmtui; current_vm=test-ssh; load_vm_facts test-ssh; "
                    f"FACTS[job_status]=running; FACTS[running]={running}; "
                    "RECOMMENDED=$(recommended_action); echo \"$RECOMMENDED\"; build_vm_menu_items"
                )
                lines = result.stdout.splitlines()
                self.assertEqual(lines[0], "Installation Log")
                self.assertIn("Attach Display", lines)
                self.assertIn("Cancel Installation", lines)
                self.assertIn("Back", lines)
                for action in ("Clean VM", "Stop VM", "Boot Desktop", "Guided Provision", "Post-Install"):
                    self.assertNotIn(action, lines)

    def test_cancel_installation_requires_confirmation_and_uses_cli(self):
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed):
                result = self.run_bash(
                    "source bin/vmtui; current_vm=test-ssh; "
                    f"confirm_box() {{ return {0 if confirmed else 1}; }}; "
                    "run_vmctl() { printf '%s\\n' \"$*\"; }; "
                    "run_action 'Cancel Installation'"
                )
                self.assertEqual(result.stdout.strip(), "cancel-install test-ssh" if confirmed else "")
        self.assertNotIn("Cancel Installation", self._unified_menu("test-ssh"))

    def test_install_attach_checks_current_vm_state_before_opening_viewer(self):
        for running in (0, 1):
            with self.subTest(running=running):
                result = self.run_bash(
                    "source bin/vmtui; current_vm=test-ssh; "
                    f"FACTS[running]={1 - running}; "
                    "load_vm_facts() { FACTS[job_status]=running; "
                    f"FACTS[running]={running}; "
                    "}; msg_box() { printf 'message: %s\\n' \"$1\"; }; "
                    "run_vmctl() { printf 'command: %s\\n' \"$*\"; }; "
                    "run_action 'Attach Display'"
                )
                expected = ("command: attach test-ssh" if running
                            else "message: Display Not Ready")
                self.assertEqual(result.stdout.strip(), expected)

    def _description_of(self, output: list[str], tag: str) -> str:
        for i, line in enumerate(output):
            if line == tag and i + 1 < len(output):
                return output[i + 1]
        return ""

    def test_unified_menu_has_all_sections(self):
        output = self._unified_menu("ubuntu-niri")
        self.assertIn("__sep_INSTALL", output)
        self.assertIn("__sep_RUN", output)
        self.assertIn("__sep_MAINTENANCE", output)
        self.assertIn("__sep_ADVANCED", output)
        self.assertNotIn("__sep_OTHER", output)
        # Back is the last selectable row
        self.assertEqual(output[-2], "Back")

    def test_unified_menu_for_autoinstall_plus_cloud_init_vm(self):
        output = self._unified_menu("ubuntu-niri")
        self.assertIn("Full Bootstrap", output)
        self.assertIn("Unattended Install", output)
        self.assertIn("Cloud-Init Flow", output)
        self.assertIn("Post-Install", output)
        # ubuntu-niri has no disk in the test tree: RUN hides boot entries and
        # the first install action is the suggested one
        self.assertNotIn("Boot Desktop", output)
        self.assertNotIn("Boot Headless", output)
        self.assertNotIn("First Boot", output)
        self.assertTrue(self._description_of(output, "Full Bootstrap").startswith("▶ "))

    def test_unified_menu_installed_vm_shows_run_entries(self):
        self.mark_installed("test-ssh")
        output = self._unified_menu("test-ssh")
        self.assertIn("Boot Desktop", output)
        self.assertIn("Boot Headless", output)
        self.assertIn("SSH Console", output)
        self.assertNotIn("Stop VM", output)  # not running
        self.assertTrue(self._description_of(output, "Boot Desktop").startswith("▶ "))
        self.assertTrue(self._description_of(output, "Boot Headless").startswith("  "))

    def test_running_vm_on_empty_disk_still_offers_stop_and_ssh(self):
        # installer in progress: running=1, prepared=1, installed=0
        self.mark_prepared("test-ssh")
        script = (
            "source bin/vmtui; load_vm_facts test-ssh; "
            "FACTS[running]=1; FACTS[runtime]=tracked:4242; RECOMMENDED=$(recommended_action); "
            "echo \"$RECOMMENDED\"; build_vm_menu_items"
        )
        result = self.run_bash(script)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "SSH Console")
        menu = lines[1:]
        self.assertIn("Stop VM", menu)
        self.assertIn("SSH Console", menu)
        self.assertNotIn("Boot Headless", menu)
        self.assertTrue(self._description_of(menu, "SSH Console").startswith("▶ "))
        # the recommended entry always exists in the menu it is rendered for
        self.assertIn(lines[0], menu)

    def test_recommended_action_follows_state(self):
        result = self.run_bash("source bin/vmtui; load_vm_facts test-ssh; recommended_action")
        self.assertEqual(result.stdout.strip(), "Guided Provision")
        self.mark_installed("test-ssh")
        result = self.run_bash("source bin/vmtui; load_vm_facts test-ssh; recommended_action")
        self.assertEqual(result.stdout.strip(), "Boot Desktop")
        # state-independent: the flow's main install action for an Arch profile
        result = self.run_bash("source bin/vmtui; load_vm_facts arch-noctalia; primary_install_action")
        self.assertEqual(result.stdout.strip(), "Arch Bootstrap")

    def test_vm_facts_exposes_flags(self):
        result = self.run_bash("source bin/vmtui; vm_facts test-ssh")
        facts = dict(line.split("\t", 1) for line in result.stdout.splitlines())
        self.assertEqual(facts["has_ssh"], "1")
        self.assertEqual(facts["ssh_port"], "2293")
        self.assertEqual(facts["installed"], "0")
        self.assertEqual(facts["running"], "0")
        self.assertEqual(facts["video_default"], "std")
        self.assertEqual(facts["label"], "Test SSH VM")

    def test_prepared_empty_disk_is_not_installed(self):
        self.mark_prepared("test-ssh")
        result = self.run_bash("source bin/vmtui; vm_status_summary test-ssh")
        self.assertEqual(result.stdout.strip(), "prepared, empty disk")
        result = self.run_bash("source bin/vmtui; load_vm_facts test-ssh; recommended_action; vm_status_header")
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "Guided Provision")
        self.assertTrue(lines[1].startswith("□ disk prepared but empty"))
        output = self._unified_menu("test-ssh")
        self.assertNotIn("Boot Desktop", output)
        result = self.run_bash("source bin/vmtui; list_dashboard_items installed ''")
        self.assertTrue(self._description_of(result.stdout.splitlines()[1:], "test-ssh").startswith("□ test-ssh"))
        self.assertIn("empty disk", result.stdout)

    def test_state_key_is_a_safe_single_path_component(self):
        result = self.run_bash(
            "source bin/vmtui; state_key ubuntu24.04; state_key '../../../.bashrc'; state_key 'a/b'; state_key 'a_b'; state_key ''"
        )
        keys = result.stdout.splitlines()
        self.assertEqual(keys[0], "ubuntu24.04")
        self.assertRegex(keys[1], r"^_\.\._\.\._\.bashrc-\d+$")
        self.assertRegex(keys[2], r"^a_b-\d+$")
        self.assertEqual(keys[3], "a_b")
        self.assertRegex(keys[4], r"^_-\d+$")
        self.assertEqual(len(set(keys)), len(keys))
        for key in keys:
            self.assertNotIn("/", key)
            self.assertFalse(key.startswith("."))
        result = self.run_bash(
            "source bin/vmtui; video_pref_set '../../escape' std; find \"$VMTUI_STATE_DIR\" -type f"
        )
        files = result.stdout.splitlines()
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].startswith(self.env["VMTUI_STATE_DIR"] + "/video/"), files[0])

    def test_fzf_is_usable_requires_minimum_version(self):
        fake = self.bindir / "fzf"
        fake.write_text("#!/usr/bin/env sh\necho '0.44.1 (debian)'\n", encoding="utf-8")
        fake.chmod(0o755)
        # auto-detection: no VMTUI_UI in the environment
        auto_env = {key: value for key, value in self.env.items() if key != "VMTUI_UI"}
        result = subprocess.run(["bash", "-lc", "source bin/vmtui; echo $UI_BACKEND"], cwd=ROOT, env=auto_env,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "fzf")
        fake.write_text("#!/usr/bin/env sh\necho '0.29.0 (debian)'\n", encoding="utf-8")
        result = subprocess.run(["bash", "-lc", "source bin/vmtui; echo $UI_BACKEND"], cwd=ROOT, env=auto_env,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "dialog")
        env = dict(self.env, VMTUI_UI="fzf")
        result = subprocess.run(["bash", "-lc", "source bin/vmtui"], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("0.36.0", result.stderr)

    def test_vm_status_header_installed(self):
        self.mark_installed("test-ssh")
        result = self.run_bash("source bin/vmtui; load_vm_facts test-ssh; vm_status_header")
        self.assertTrue(result.stdout.startswith("■ stopped, disk has data"))
        self.assertIn("SSH port 2293", result.stdout)

    def test_dashboard_refresh_key_is_reported_not_swallowed(self):
        # A letter would collide with the filter, so the refresh lives on Ctrl-R / F5 and
        # fzf must report it instead of consuming it.
        fake = self.bindir / "fzf"
        fake.write_text(
            "#!/usr/bin/env sh\n"
            "case \"$1\" in --version) echo '0.67.0 (debian)'; exit 0 ;; esac\n"
            "for arg in \"$@\"; do case \"$arg\" in --expect=*) echo \"${arg#--expect=}\" >&2 ;; esac; done\n"
            "cat >/dev/null\n"
            "printf 'ctrl-r\\n__filter\\tFilter row\\n'\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = dict(self.env, VMTUI_UI="fzf")
        result = subprocess.run(
            ["bash", "-lc", "source bin/vmtui; MENU_REFRESH=1 fzf_pick title header '' tag desc; echo \"highlighted=$MENU_HIGHLIGHTED\""],
            cwd=ROOT, env=env, capture_output=True, text=True, check=True,
        )
        self.assertIn("ctrl-r,f5", result.stderr)  # the keys fzf was asked to report
        self.assertIn("__refresh", result.stdout)  # the sentinel the dashboard loops on
        self.assertIn("highlighted=__filter", result.stdout)  # cursor position survives

    def test_dashboard_selection_still_returns_the_tag_with_refresh_enabled(self):
        fake = self.bindir / "fzf"
        fake.write_text(
            "#!/usr/bin/env sh\n"
            "case \"$1\" in --version) echo '0.67.0 (debian)'; exit 0 ;; esac\n"
            "cat >/dev/null\nprintf '\\ntest-ssh\\tTest row\\n'\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = dict(self.env, VMTUI_UI="fzf")
        result = subprocess.run(
            ["bash", "-lc", "source bin/vmtui; MENU_REFRESH=1 fzf_pick title header '' tag desc"],
            cwd=ROOT, env=env, capture_output=True, text=True, check=True,
        )
        # Enter leaves the key line empty: the selected tag must still come back alone.
        self.assertEqual(result.stdout.strip(), "test-ssh")

    def test_vm_refresh_reloads_state_and_preserves_cursor_for_both_keys(self):
        fake = self.bindir / "fzf"
        fake.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    print('0.36.0'); raise SystemExit\n"
            "root = Path(os.environ['VMTUI_ROOT_DIR'])\n"
            "log = root / 'picker.jsonl'\n"
            "rows = sys.stdin.read().splitlines()\n"
            "first = not log.exists()\n"
            "with log.open('a') as out:\n"
            "    out.write(json.dumps({'args': sys.argv[1:], 'rows': rows}) + '\\n')\n"
            "if first:\n"
            "    disk = root / 'artifacts/test-ssh/disk.qcow2'\n"
            "    disk.parent.mkdir(parents=True, exist_ok=True)\n"
            "    disk.write_bytes(b'x' * (24 * 1024 * 1024))\n"
            "    print(os.environ['TEST_REFRESH_KEY'])\n"
            "    print('Profile Details\\tProfile Details')\n"
            "else:\n"
            "    print('\\nBack\\tBack')\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        for key in ("ctrl-r", "f5"):
            with self.subTest(key=key):
                log = self.bindir / "picker.jsonl"
                log.unlink(missing_ok=True)
                (self.bindir / "artifacts/test-ssh/disk.qcow2").unlink(missing_ok=True)
                result = subprocess.run(
                    ["bash", "-lc", "source bin/vmtui; current_vm=test-ssh; "
                     "run_action() { echo unexpected-action; }; vm_menu_loop"],
                    cwd=ROOT, env=dict(self.env, VMTUI_UI="fzf", TEST_REFRESH_KEY=key),
                    capture_output=True, text=True, check=True, timeout=15,
                )
                self.assertEqual(result.stdout, "")
                first, second = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertTrue(any(arg.startswith("--expect=ctrl-r,f5") for arg in first["args"]), first["args"])
                self.assertFalse(any(row.startswith("Boot Desktop\t") for row in first["rows"]))
                self.assertTrue(any(row.startswith("Boot Desktop\t") for row in second["rows"]))
                header = next(arg for arg in second["args"] if arg.startswith("--header="))
                self.assertIn("DISK WITH DATA", header)
                self.assertIn("Ctrl-R / F5", header)
                position = next(i for i, row in enumerate(second["rows"], 1)
                                if row.startswith("Profile Details\t"))
                self.assertIn(f"start:pos({position})", second["args"])

    def test_vm_menu_alt_shortcuts_pick_their_action_or_redraw(self):
        fake = self.bindir / "fzf"
        fake.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    print('0.36.0'); raise SystemExit\n"
            "root = Path(os.environ['VMTUI_ROOT_DIR'])\n"
            "log = root / 'picker.jsonl'\n"
            "rows = sys.stdin.read().splitlines()\n"
            "calls = len(log.read_text().splitlines()) if log.exists() else 0\n"
            "with log.open('a') as out:\n"
            "    out.write(json.dumps({'args': sys.argv[1:], 'rows': rows}) + '\\n')\n"
            "if calls == 0:\n"
            "    print(os.environ['TEST_HOTKEY'])\n"
            "    print('Profile Details\\tProfile Details')\n"
            "elif calls == 1 and os.environ.get('TEST_SECOND_HOTKEY'):\n"
            "    print(os.environ['TEST_SECOND_HOTKEY'])\n"
            "    print('Profile Details\\tProfile Details')\n"
            "else:\n"
            "    print('\\nBack\\tBack')\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        log = self.bindir / "picker.jsonl"
        # Alt-D on a VM whose menu offers "Boot Desktop": the action runs.
        (self.bindir / "artifacts/test-ssh/disk.qcow2").parent.mkdir(parents=True, exist_ok=True)
        (self.bindir / "artifacts/test-ssh/disk.qcow2").write_bytes(b"x" * (24 * 1024 * 1024))
        result = subprocess.run(
            ["bash", "-lc", "source bin/vmtui; current_vm=test-ssh; "
             "is_na_action() { return 1; }; run_action() { echo \"action=$1\"; }; vm_menu_loop"],
            cwd=ROOT, env=dict(self.env, VMTUI_UI="fzf", TEST_HOTKEY="alt-d"),
            capture_output=True, text=True, check=True, timeout=15,
        )
        self.assertEqual(result.stdout.strip(), "action=Boot Desktop")
        first = json.loads(log.read_text().splitlines()[0])
        expect = next(arg for arg in first["args"] if arg.startswith("--expect="))
        for key in ("ctrl-r", "f5", "alt-d", "alt-u", "alt-s", "alt-a", "alt-c", "alt-x", "alt-p", "alt-enter"):
            self.assertIn(key, expect)
        header = next(arg for arg in first["args"] if arg.startswith("--header="))
        self.assertIn("Alt", header)
        self.assertIn("desktop", header)
        self.assertIn("install", header)
        # Alt-X while the VM is not running: "Stop VM" is not in the menu, so the shortcut
        # only redraws it (cursor kept) and the next pick is honoured.
        log.unlink()
        result = subprocess.run(
            ["bash", "-lc", "source bin/vmtui; current_vm=test-ssh; "
             "is_na_action() { return 1; }; run_action() { echo \"action=$1\"; }; vm_menu_loop"],
            cwd=ROOT, env=dict(self.env, VMTUI_UI="fzf", TEST_HOTKEY="alt-x", TEST_SECOND_HOTKEY="alt-p"),
            capture_output=True, text=True, check=True, timeout=15,
        )
        self.assertEqual(result.stdout.strip(), "action=Post-Install")
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(len(calls), 3)  # redraw after Alt-X, the Alt-P pick, then the menu again after the action
        self.assertFalse(any(row.startswith("Stop VM\t") for row in calls[0]["rows"]))
        position = next(i for i, row in enumerate(calls[1]["rows"], 1) if row.startswith("Profile Details\t"))
        self.assertIn(f"start:pos({position})", calls[1]["args"])

    def test_vm_refresh_falls_back_when_highlighted_action_disappears(self):
        result = self.run_bash(
            "source bin/vmtui; current_vm=test-ssh; "
            "load_vm_facts() { FACTS[label]=Test; }; "
            "recommended_action() { echo 'SSH Console'; }; "
            "build_vm_menu_items() { printf 'SSH Console\\nOpen shell\\nBack\\nBack\\n'; }; "
            "vm_status_header() { echo running; }; "
            "menu_choose_fit() { "
            "if [[ ! -e $VMTUI_ROOT_DIR/refreshed ]]; then "
            "touch \"$VMTUI_ROOT_DIR/refreshed\"; printf '__refresh\\tBoot Desktop\\n'; "
            "else echo \"cursor=$MENU_DEFAULT_ITEM\" >&2; echo Back; fi; }; "
            "run_action() { echo unexpected-action; }; vm_menu_loop"
        )
        self.assertEqual(result.stdout, "")
        self.assertIn("cursor=SSH Console", result.stderr)

    def test_dashboard_refresh_preserves_cursor_across_command_substitution(self):
        result = self.run_bash(
            "source bin/vmtui; "
            "list_dashboard_items() { printf '__summary\\t1\\t0\\t0\\t1\\ntest-ssh\\nTest SSH\\n'; }; "
            "catalog_has_network_lab() { return 1; }; clear() { :; }; "
            "menu_choose_fit() { "
            "if [[ ! -e $VMTUI_ROOT_DIR/refreshed ]]; then "
            "touch \"$VMTUI_ROOT_DIR/refreshed\"; printf '__refresh\\ttest-ssh\\n'; "
            "else echo \"cursor=$MENU_DEFAULT_ITEM\" >&2; echo __quit; fi; }; dashboard_loop"
        )
        self.assertIn("cursor=test-ssh", result.stderr)

    def test_dashboard_alt_shortcut_runs_the_action_on_the_highlighted_vm(self):
        # fzf reports the key and the highlighted row: the dashboard runs that VM's action
        # without opening its menu, skips non-VM rows, and explains an action not offered.
        disk = self.bindir / "artifacts/test-ssh/disk.qcow2"
        disk.parent.mkdir(parents=True, exist_ok=True)
        disk.write_bytes(b"x" * (24 * 1024 * 1024))
        result = self.run_bash(
            "source bin/vmtui; "
            "list_dashboard_items() { printf '__summary\\t1\\t1\\t0\\t1\\ntest-ssh\\nTest SSH\\n'; }; "
            "catalog_has_network_lab() { return 1; }; clear() { :; }; "
            "is_na_action() { return 1; }; run_action() { echo \"action=$1 vm=$current_vm\"; }; "
            "msg_box() { echo \"msg=$2\"; }; "
            "menu_choose_fit() { "
            "echo \"hotkeys=$MENU_HOTKEYS_RAW\" >&2; "
            "n=$(cat $VMTUI_ROOT_DIR/step 2>/dev/null || echo 0); echo $((n + 1)) > $VMTUI_ROOT_DIR/step; "
            "case $n in "
            "0) printf '__hotkey\\talt-d\\ttest-ssh\\n' ;; "
            "1) printf '__hotkey\\talt-x\\ttest-ssh\\n' ;; "
            "2) printf '__hotkey\\talt-d\\t__filter\\n' ;; "
            "*) echo \"cursor=$MENU_DEFAULT_ITEM\" >&2; echo __quit ;; esac; }; dashboard_loop"
        )
        self.assertEqual(result.stdout.splitlines(), [
            "action=Boot Desktop vm=test-ssh",
            "msg='Stop VM' is not available for this VM right now (Test SSH VM).",
        ])
        self.assertIn("hotkeys=alt-d,alt-u,alt-s,alt-a,alt-c,alt-x,alt-p", result.stderr)
        self.assertIn("cursor=__filter", result.stderr)

    def test_fzf_pick_reports_raw_hotkeys_with_the_highlighted_row(self):
        result = self.run_bash(
            "source bin/vmtui; "
            "fzf() { printf 'alt-d\\ntest-ssh\\tTest SSH\\n'; }; "
            "MENU_HOTKEYS_RAW=alt-d,alt-x MENU_REFRESH=1 fzf_pick T H '' test-ssh 'Test SSH' __quit Quit"
        )
        self.assertEqual(result.stdout, "__hotkey\talt-d\ttest-ssh\n")

    def test_dashboard_columns_fit_terminal_width(self):
        for width in (60, 80, 100, 140):
            with self.subTest(width=width):
                result = self.run_bash(
                    f"source bin/vmtui; term_cols() {{ echo {width}; }}; "
                    "list_dashboard_items __header ''; list_dashboard_items all ''"
                )
                lines = result.stdout.splitlines()
                self.assertIn("RAM/CPU", lines[0])
                self.assertIn("SSH", lines[0])
                for row in [lines[0], *lines[3::2]]:
                    self.assertLessEqual(len(row), width - 8)

    def test_dashboard_summary_and_rows(self):
        self.mark_installed("test-ssh")
        result = self.run_bash("source bin/vmtui; list_dashboard_items all ''")
        lines = result.stdout.splitlines()
        summary = lines[0].split("\t")
        self.assertEqual(summary[0], "__summary")
        total, installed, running, shown = map(int, summary[1:])
        self.assertEqual(total, shown)
        self.assertGreaterEqual(installed, 1)
        self.assertGreaterEqual(running, 0)
        tags = lines[1::2]
        self.assertIn("test-ssh", tags)
        row = self._description_of(lines[1:], "test-ssh")
        self.assertTrue(row.startswith("■ test-ssh"), row)
        self.assertIn(":2293", row)
        # installed VMs sort before the rest: no ○ row may precede a ■ row
        glyphs = [lines[1:][i + 1][0] for i, tag in enumerate(lines[1:]) if tag in tags and i % 2 == 0]
        self.assertGreater(glyphs.index("○") if "○" in glyphs else len(glyphs), glyphs.index("■"))

    def test_dashboard_job_status_shows_problems_but_never_masks_a_finished_install(self):
        self.mark_installed("test-ssh")
        job = self.bindir / "artifacts" / "test-ssh" / "runtime" / "tui-job"
        job.mkdir(parents=True, exist_ok=True)
        (job / "lock").touch()
        for recorded, expected in [("running\n", "interrupted"), ("failed (1)\n", "failed (1)"),
                                   ("completed\n", "disk ")]:
            with self.subTest(recorded=recorded):
                (job / "status").write_text(recorded, encoding="utf-8")
                result = self.run_bash("source bin/vmtui; list_dashboard_items all ''")
                row = self._description_of(result.stdout.splitlines()[1:], "test-ssh")
                self.assertIn(expected, row)

    def test_dashboard_filters(self):
        result = self.run_bash("source bin/vmtui; list_dashboard_items find niri")
        tags = result.stdout.splitlines()[1::2]
        self.assertIn("ubuntu-niri", tags)
        self.assertNotIn("alpine-ci", tags)
        result = self.run_bash("source bin/vmtui; list_dashboard_items family alpine")
        tags = result.stdout.splitlines()[1::2]
        self.assertIn("alpine-ci", tags)
        self.assertNotIn("ubuntu-niri", tags)
        result = self.run_bash("source bin/vmtui; list_family_menu_items")
        self.assertIn("alpine", result.stdout.splitlines()[0::2])

    def test_video_preference_is_remembered_and_validated(self):
        result = self.run_bash(
            "source bin/vmtui; current_vm=test-ssh; load_vm_facts test-ssh; "
            "video_args | wc -l; video_pref_set test-ssh std; video_args; "
            "video_pref_set test-ssh bogus; video_args | wc -l; video_pref_clear test-ssh; video_args | wc -l"
        )
        self.assertEqual(result.stdout.split("\n")[:5], ["0", "--video", "std", "0", "0"])

    def test_unified_menu_for_arch_bootstrap_vm(self):
        # SSH Console is offered only once the disk holds an OS
        self.mark_installed("arch-noctalia")
        output = self._unified_menu("arch-noctalia")
        self.assertIn("Arch Bootstrap", output)
        self.assertIn("Arch Install (Interactive)", output)
        self.assertIn("SSH Console", output)
        self.assertNotIn("Full Bootstrap", output)
        self.assertNotIn("Debian Preseed Bootstrap", output)

    def test_unified_menu_offers_attach_display_only_while_running(self):
        self.mark_installed("test-ssh")
        self.assertNotIn("Attach Display", self._unified_menu("test-ssh"))
        # A "running" VM is a tracked PID whose /proc cmdline names qemu-system-x86_64:
        # a renamed sleep is enough, no QEMU needed (the CI runner has none).
        fake_qemu = subprocess.Popen(["bash", "-c", "exec -a qemu-system-x86_64 sleep 120"])
        self.addCleanup(fake_qemu.kill)
        pid_file = self.bindir / "artifacts" / "test-ssh" / "runtime" / "bootstrap-start.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(f"{fake_qemu.pid}\n", encoding="utf-8")
        output = self._unified_menu("test-ssh")
        self.assertIn("Attach Display", output)
        self.assertIn("Stop VM", output)
        self.assertIn("VNC viewer", self._description_of(output, "Attach Display"))

    def test_unified_menu_for_omarchy_bootstrap_vm(self):
        self.mark_installed("arch-omarchy-nvidia")
        output = self._unified_menu("arch-omarchy-nvidia")
        self.assertIn("Omarchy Bootstrap", output)
        self.assertIn("Omarchy Unattended Install", output)
        self.assertIn("SSH Console", output)
        self.assertNotIn("Arch Bootstrap", output)

    def test_unified_menu_for_preseed_vm(self):
        output = self._unified_menu("debian-server")
        self.assertIn("Debian Preseed Bootstrap", output)
        self.assertIn("Guided Provision", output)
        self.assertIn("Post-Install", output)
        self.assertNotIn("Arch Bootstrap", output)

    def test_unified_menu_for_kickstart_vm(self):
        output = self._unified_menu("almalinux-server")
        self.assertIn("Kickstart Bootstrap", output)
        self.assertIn("Guided Provision", output)
        self.assertIn("Post-Install", output)

    def test_unified_menu_for_windows_unattended_vm(self):
        output = self._unified_menu("windows11-unattended")
        self.assertIn("Windows Bootstrap", output)
        self.assertNotIn("Alpine Bootstrap", output)
        result = self.run_bash("source bin/vmtui; load_vm_facts windows11-unattended; recommended_action")
        self.assertEqual(result.stdout.strip(), "Windows Bootstrap")
        # the import templates keep the manual flow
        self.assertNotIn("Windows Bootstrap", self._unified_menu("windows11-template"))

    def test_network_lab_menu_exists_when_a_router_profile_is_defined(self):
        self.assertEqual(self.run_bash("source bin/vmtui; catalog_has_network_lab && echo yes").stdout.strip(), "yes")
        items = self.run_bash("source bin/vmtui; list_lab_menu_items").stdout.splitlines()
        tags = items[0::2]
        for tag in ("Plan", "Install", "Install + Export", "Up", "Down", "Check", "Export to libvirt", "Unexport", "Libvirt Round Trip", "Clean"):
            self.assertIn(tag, tags)
        self.assertEqual(len(items) % 2, 0)

    def test_unified_menu_for_pfsense_router(self):
        output = self._unified_menu("pfsense-lab")
        self.assertIn("pfSense Bootstrap", output)
        self.assertIn("Network Lab Plan", output)
        self.assertNotIn("Windows Bootstrap", output)
        result = self.run_bash("source bin/vmtui; load_vm_facts pfsense-lab; recommended_action")
        self.assertEqual(result.stdout.strip(), "pfSense Bootstrap")
        # the Linux members ride the Ubuntu autoinstall flow
        self.assertIn("Full Bootstrap", self._unified_menu("pihole-lab"))

    def test_unified_menu_for_alpine_vm(self):
        output = self._unified_menu("alpine-niri")
        self.assertIn("Alpine Bootstrap", output)
        self.assertIn("Guided Provision", output)
        self.assertIn("Post-Install", output)
        self.assertNotIn("Kickstart Bootstrap", output)
        # no disk yet: the bootstrap is the suggested first step
        result = self.run_bash("source bin/vmtui; load_vm_facts alpine-niri; recommended_action")
        self.assertEqual(result.stdout.strip(), "Alpine Bootstrap")

    def test_unified_menu_for_fedora_dms_vm_uses_kickstart(self):
        output = self._unified_menu("fedora-niri-dms")
        self.assertIn("Kickstart Bootstrap", output)
        self.assertNotIn("Alpine Bootstrap", output)

    def test_unified_menu_for_plain_vm_hides_inapplicable_entries(self):
        output = self._unified_menu("alpine-ci")
        self.assertNotIn("Full Bootstrap", output)
        self.assertNotIn("Arch Bootstrap", output)
        self.assertIn("Guided Provision", output)
        self.assertIn("Installer Only", output)
        self.assertNotIn("SSH Console", output)
        self.assertNotIn("Post-Install", output)
        self.assertNotIn("Stop VM", output)
        self.assertIn("Video Profile", output)

    def test_unified_menu_for_ssh_only_vm_shows_active_ssh(self):
        self.mark_installed("test-ssh")
        output = self._unified_menu("test-ssh")
        self.assertIn("SSH Console", output)
        self.assertIn(
            "Open a shell inside the VM",
            self._description_of(output, "SSH Console"),
        )
        self.assertIn("Post-Install", output)
        self.assertIn(
            "Run configured SSH provisioning tasks",
            self._description_of(output, "Post-Install"),
        )

    def test_unified_menu_includes_advanced_entries(self):
        output = self._unified_menu("alpine-ci")
        self.assertIn("Flash Empty Disk", output)
        self.assertIn("Force Flash", output)
        self.assertIn("Import Disk", output)

    def test_unified_menu_includes_maintenance_entries(self):
        output = self._unified_menu("alpine-ci")
        self.assertIn("Boot Check", output)
        self.assertIn("Clean VM", output)
        self.assertIn("Delete ISO", output)
        self.assertIn("Profile Details", output)
        self.assertIn("Fetch ISO", output)
        self.assertIn("Prepare VM", output)

    def test_is_na_action_ssh_when_no_ssh_provision(self):
        result = subprocess.run(
            ["bash", "-lc", "source bin/vmtui; is_na_action 'SSH Console' alpine-ci"],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)

    def test_is_na_action_ssh_when_ssh_provision_present(self):
        result = subprocess.run(
            ["bash", "-lc", "source bin/vmtui; is_na_action 'SSH Console' test-ssh"],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_is_na_action_boot_desktop_when_disk_missing(self):
        # freebsd has no disk in the temp root (plain VM, never installed)
        result = subprocess.run(
            ["bash", "-lc", "source bin/vmtui; is_na_action 'Boot Desktop' freebsd"],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)

    def test_vm_status_summary_not_installed_when_no_disk(self):
        result = self.run_bash("source bin/vmtui; vm_status_summary freebsd")
        self.assertEqual(result.stdout.strip(), "not installed")

    def test_libvirt_menu_and_actions(self):
        self.mark_installed("test-ssh")
        output = self._unified_menu("test-ssh")
        for label, command in (("Export to libvirt", "export-libvirt"), ("Remove from libvirt", "unexport-libvirt")):
            self.assertIn(label, output)
            result = self.run_bash(f"source bin/vmtui; resolve_action {label!r}")
            self.assertEqual(result.stdout.strip(), command)

    def test_resolve_action_separator_returns_noop(self):
        result = self.run_bash("source bin/vmtui; resolve_action __sep_INSTALL")
        self.assertEqual(result.stdout.strip(), "noop")

    def test_resolve_action_other_separators_also_noop(self):
        for sep in ("__sep_RUN", "__sep_MAINTENANCE", "__sep_ADVANCED"):
            result = self.run_bash(f"source bin/vmtui; resolve_action {sep}")
            self.assertEqual(result.stdout.strip(), "noop", f"separator {sep} not mapped to noop")

    def test_resolve_action_maps_remote_hosts(self):
        result = self.run_bash("source bin/vmtui; resolve_action 'Remote Hosts'")
        self.assertEqual(result.stdout.strip(), "remote-hosts")

    def test_resolve_action_maps_bootstrap_entries(self):
        for entry, expected in (
            ("Full Bootstrap", "bootstrap-unattended"),
            ("Arch Bootstrap", "bootstrap-archinstall"),
            ("Omarchy Bootstrap", "bootstrap-omarchy"),
            ("Omarchy Unattended Install", "install-omarchy"),
            ("Debian Preseed Bootstrap", "bootstrap-preseed"),
            ("Kickstart Bootstrap", "bootstrap-kickstart"),
            ("Alpine Bootstrap", "bootstrap-alpine"),
            ("Windows Bootstrap", "bootstrap-windows"),
            ("pfSense Bootstrap", "bootstrap-pfsense"),
            ("Network Lab Plan", "lab-plan"),
            ("Attach Display", "attach"),
            ("Serial Console", "console"),
            ("Unattended Install", "full-auto-install"),
            ("Cloud-Init Flow", "cloud-init-install"),
            ("Flash Empty Disk", "flash"),
            ("Force Flash", "flash-force"),
            ("Import Disk", "import-device"),
            ("Video Profile", "video-profile"),
        ):
            result = self.run_bash(f"source bin/vmtui; resolve_action {entry!r}")
            self.assertEqual(result.stdout.strip(), expected, f"{entry!r} did not resolve to {expected}")

    def test_import_modes_forward_the_selected_flags(self):
        for mode, flags in (("full", []), ("allocated", ["--allocated-only"]),
                            ("resume", ["--allocated-only", "--resume"])):
            with self.subTest(mode=mode):
                script = """
source bin/vmtui
current_vm=test-ssh
list_target_device_menu_items() { printf '%s\\n' /dev/fake 'Test disk'; }
menu_choose_fit() {
    if [[ $1 == 'Import Disk' ]]; then printf '%s\\n' /dev/fake;
    else printf '%s\\n' "$IMPORT_TEST_MODE"; fi
}
confirm_box() { return 0; }
confirm_device_path() { return 0; }
run_vmctl() { printf '%s\\n' "$@"; }
run_action 'Import Disk'
"""
                result = subprocess.run(["bash", "-c", script], cwd=ROOT,
                                        env={**self.env, "IMPORT_TEST_MODE": mode}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), ["import-device", "test-ssh", "--device", "/dev/fake",
                                                               "--confirm-device", "/dev/fake", *flags])

    def test_list_remote_menu_items_reads_remotes_json(self):
        result = self.run_bash("source bin/vmtui; list_remote_menu_items")
        output = result.stdout.splitlines()
        self.assertEqual(output[0], "i9")
        self.assertIn("lab@host.lan", output[1])
        self.assertIn("5930->5930", output[1])

    def test_install_command_for_remote_viewer_detects_apt(self):
        apt_get = self.bindir / "apt-get"
        apt_get.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
        apt_get.chmod(0o755)
        result = self.run_bash("source bin/vmtui; install_command_for remote-viewer")
        self.assertEqual(result.stdout.strip(), "sudo apt-get install -y virt-viewer")

    def test_ui_backend_env_override(self):
        env = dict(self.env, VMTUI_UI="dialog")
        result = subprocess.run(["bash", "-lc", "source bin/vmtui; echo $UI_BACKEND"], cwd=ROOT, env=env,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "dialog")
        env = dict(self.env, VMTUI_UI="bogus")
        result = subprocess.run(["bash", "-lc", "source bin/vmtui"], cwd=ROOT, env=env,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("VMTUI_UI", result.stderr)

    def test_list_vm_menu_items_main_lists_profiles(self):
        result = self.run_bash("source bin/vmtui; list_vm_menu_items")
        output = result.stdout.splitlines()
        # tags are even-indexed lines (0, 2, 4, ...)
        tags = output[::2]
        self.assertIn("alpine-ci", tags)
        self.assertIn("ubuntu-niri", tags)
        self.assertIn("arch-noctalia", tags)


if __name__ == "__main__":
    unittest.main()
