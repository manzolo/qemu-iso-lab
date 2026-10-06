"""App launchers work outside the checkout and respect per-user install paths."""
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.install_launchers import desktop_exec, install

ROOT = Path(__file__).resolve().parents[1]


class Executed(Exception):
    pass


class LauncherTests(unittest.TestCase):
    def run_launcher(self, *, tty: bool, env: dict[str, str]):
        """bin/qemu-iso-lab copied into a temp checkout and called through a symlink, with stdin a
        terminal or not: returns (os.execv call, subprocess.Popen call, the temp checkout)."""
        tmp = Path(self.temp_dir())
        repo = tmp / "checkout"
        (repo / "bin").mkdir(parents=True)
        shutil.copy2(ROOT / "bin/qemu-iso-lab", repo / "bin/qemu-iso-lab")
        link = tmp / "qemu-iso-lab"
        link.symlink_to(repo / "bin/qemu-iso-lab")
        with mock.patch.object(sys, "argv", [str(link), "--port", "9000"]), \
             mock.patch.object(sys.stdin, "isatty", return_value=tty), \
             mock.patch.dict(os.environ, env), \
             mock.patch("subprocess.Popen") as popen, \
             mock.patch("os.execv", side_effect=Executed) as execute:
            for name in ("DISPLAY", "WAYLAND_DISPLAY"):
                if name not in env:
                    os.environ.pop(name, None)
            try:
                runpy.run_path(str(link), run_name="__main__")
            except Executed:  # a real execv never returns
                pass
        return execute, popen, repo

    def temp_dir(self) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name

    def web(self, repo: Path) -> list[str]:
        return [sys.executable, "-u", str(repo / "bin/vmctl"), "web", "--open", "--port", "9000"]

    def test_from_a_terminal_it_runs_in_the_foreground(self):
        execute, popen, repo = self.run_launcher(tty=True, env={"WAYLAND_DISPLAY": "wayland-1"})
        execute.assert_called_once_with(sys.executable, self.web(repo))
        popen.assert_not_called()

    def test_from_the_app_menu_it_starts_in_the_background_with_no_window(self):
        # No Terminal=true (DankMaterialShell runs it in an xterm that may not exist) and no
        # terminal window left open: the server goes to the background and stops when idle.
        execute, popen, repo = self.run_launcher(tty=False, env={"WAYLAND_DISPLAY": "wayland-1"})
        execute.assert_not_called()
        self.assertEqual(popen.call_args.args[0], [*self.web(repo), "--idle-exit", "15"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertTrue((repo / "artifacts/.web/server.log").is_file())

    def test_without_a_desktop_it_stays_in_the_foreground(self):
        execute, popen, repo = self.run_launcher(tty=False, env={})
        execute.assert_called_once_with(sys.executable, self.web(repo))
        popen.assert_not_called()

    def test_setup_installs_command_and_menu_entry_in_custom_user_locations(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "checkout with spaces"
            (repo / "bin").mkdir(parents=True)
            (repo / "tools").mkdir()
            for name in ("qemu-iso-lab", "vmtui"):
                shutil.copy2(ROOT / "bin" / name, repo / "bin" / name)
            shutil.copy2(ROOT / "setup.sh", repo / "setup.sh")
            shutil.copy2(ROOT / "tools/install_launchers.py", repo / "tools/install_launchers.py")
            (repo / "vmctl/web").mkdir(parents=True)
            shutil.copy2(ROOT / "vmctl/web/qemu-iso-lab.svg", repo / "vmctl/web/qemu-iso-lab.svg")
            vmctl = repo / "bin/vmctl"
            vmctl.write_text(f"#!{sys.executable}\nimport sys\nprint(' '.join(sys.argv[1:]))\n")
            vmctl.chmod(0o755)
            prefix = base / "user prefix"
            data = base / "xdg data"
            env = {**os.environ, "PREFIX": str(prefix), "XDG_DATA_HOME": str(data)}
            result = subprocess.run(["sh", str(repo / "setup.sh"), "--yes"], cwd=base,
                                    env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in ("qemu-iso-lab", "vmctl", "vmtui"):
                self.assertEqual((prefix / "bin" / name).resolve(), repo / "bin" / name)
            desktop = data / "applications/qemu-iso-lab.desktop"
            self.assertIn(f'Exec="{repo}/bin/qemu-iso-lab"', desktop.read_text())
            self.assertIn("Terminal=false", desktop.read_text())
            self.assertIn("Icon=qemu-iso-lab\n", desktop.read_text())
            icon = data / "icons/hicolor/scalable/apps/qemu-iso-lab.svg"
            self.assertEqual(icon.read_bytes(), (ROOT / "vmctl/web/qemu-iso-lab.svg").read_bytes())
            self.assertIn("setup --install --yes", result.stdout)
            if shutil.which("desktop-file-validate"):
                subprocess.run(["desktop-file-validate", str(desktop)], check=True, capture_output=True)

    def test_desktop_entry_escapes_special_characters_in_checkout_path(self):
        self.assertEqual(desktop_exec(Path('/tmp/lab $x "quoted" 100%/bin/qemu-iso-lab')),
                         '"/tmp/lab \\\\$x \\\\"quoted\\\\" 100%%/bin/qemu-iso-lab"')

    def test_menu_install_is_repeatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            roots = []
            for name in ("old checkout", "new checkout"):
                root = Path(tmp) / name
                (root / "vmctl/web").mkdir(parents=True)
                shutil.copy2(ROOT / "vmctl/web/qemu-iso-lab.svg", root / "vmctl/web/qemu-iso-lab.svg")
                roots.append(root)
            desktop = install(roots[0], data)
            install(roots[1], data)
            self.assertIn(f"{roots[1]}/bin/qemu-iso-lab", desktop.read_text())
            self.assertNotIn(str(roots[0]), desktop.read_text())
