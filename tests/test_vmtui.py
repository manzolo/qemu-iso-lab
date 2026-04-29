import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VMTUI_PATH = ROOT / "bin" / "vmtui"


class VmtuiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.bindir = Path(self.tempdir.name)
        dialog = self.bindir / "dialog"
        dialog.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
        dialog.chmod(0o755)
        self.env = os.environ.copy()
        self.env["PATH"] = f"{self.bindir}:{self.env['PATH']}"
        self.env["VMTUI_TEST_MODE"] = "1"

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

    def test_list_vm_action_items_groups_cloud_init_entries_for_supported_vm(self):
        result = self.run_bash("source bin/vmtui; list_vm_action_items ubuntu-niri")
        output = result.stdout.splitlines()

        self.assertIn("install-flows", output)
        self.assertIn("stop", output)
        self.assertIn("shell", output)
        self.assertIn("start-headless", output)
        self.assertIn("post-install-flows", output)
        self.assertNotIn("full-auto-install", output)
        self.assertNotIn("cloud-init-install", output)
        self.assertNotIn("install-cloud-init", output)
        self.assertNotIn("start-cloud-init", output)
        self.assertNotIn("post-install", output)

    def test_list_install_action_items_include_cloud_init_entries_for_supported_vm(self):
        result = self.run_bash("source bin/vmtui; list_install_action_items ubuntu-niri")
        output = result.stdout.splitlines()

        self.assertIn("full-auto-install", output)
        self.assertIn("bootstrap-unattended", output)
        self.assertIn("cloud-init-install", output)
        self.assertIn("install-cloud-init", output)

    def test_list_post_install_action_items_include_cloud_init_entries_for_supported_vm(self):
        result = self.run_bash("source bin/vmtui; list_post_install_action_items ubuntu-niri")
        output = result.stdout.splitlines()

        self.assertIn("start-cloud-init", output)
        self.assertIn("post-install", output)

    def test_list_vm_action_items_omits_cloud_init_group_for_plain_vm(self):
        result = self.run_bash("source bin/vmtui; list_vm_action_items alpine-ci")
        output = result.stdout.splitlines()

        self.assertNotIn("post-install-flows", output)

    def test_list_install_action_items_omits_cloud_init_entries_for_plain_vm(self):
        result = self.run_bash("source bin/vmtui; list_install_action_items alpine-ci")
        output = result.stdout.splitlines()

        self.assertNotIn("full-auto-install", output)
        self.assertNotIn("bootstrap-unattended", output)
        self.assertNotIn("cloud-init-install", output)
        self.assertNotIn("install-cloud-init", output)

    def test_list_post_install_action_items_omits_cloud_init_entries_for_plain_vm(self):
        result = self.run_bash("source bin/vmtui; list_post_install_action_items alpine-ci")
        output = result.stdout.splitlines()

        self.assertNotIn("start-cloud-init", output)
        self.assertNotIn("post-install", output)


if __name__ == "__main__":
    unittest.main()
