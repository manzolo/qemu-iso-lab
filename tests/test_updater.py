"""`vmctl update` against real git repositories in a temp dir: an origin, a clone that stands for the
user's checkout, a release pushed to origin. No network, no host tool installed (those are mocked)."""
import argparse
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vmctl import state, updater  # noqa: E402
from vmctl.errors import VMError  # noqa: E402

GIT_ID = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *GIT_ID, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        work = self.tmp / "work"
        (work / "vmctl" / "web").mkdir(parents=True)
        (work / "bin").mkdir()
        (work / "tools").mkdir()
        self.write_tree(work, "0.20.0", launcher=False)
        git(work, "init", "-q", "-b", "main")
        git(work, "add", "-A")
        git(work, "commit", "-q", "-m", "v0.20.0")
        self.origin = self.tmp / "origin.git"
        git(work, "clone", "-q", "--bare", str(work), str(self.origin))
        self.checkout = self.tmp / "home" / "qemu-iso-lab"
        self.checkout.parent.mkdir()
        git(self.tmp, "clone", "-q", str(self.origin), str(self.checkout))
        # The release: a version bump and a new launcher, pushed to origin.
        self.write_tree(work, "0.21.0", launcher=True)
        git(work, "add", "-A")
        git(work, "commit", "-q", "-m", "vmctl 0.21.0")
        git(work, "push", "-q", str(self.origin), "main")
        self.prefix = self.tmp / "home" / ".local"
        (self.prefix / "bin").mkdir(parents=True)
        (self.prefix / "bin" / "vmctl").symlink_to(self.checkout / "bin" / "vmctl")
        self.data = self.tmp / "home" / "share"
        (self.data / "applications").mkdir(parents=True)
        (self.data / "applications" / "qemu-iso-lab.desktop").write_text("[Desktop Entry]\nExec=old\n")
        self.env = mock.patch.dict(os.environ, {"PREFIX": str(self.prefix), "XDG_DATA_HOME": str(self.data), "HOME": str(self.tmp / "home")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.root = mock.patch.object(state, "ROOT", self.checkout)
        self.root.start()
        self.addCleanup(self.root.stop)

    @staticmethod
    def write_tree(work: Path, version: str, launcher: bool) -> None:
        (work / "vmctl" / "__init__.py").write_text(f'__version__ = "{version}"\n')
        (work / "vmctl" / "web" / "qemu-iso-lab.svg").write_text("<svg/>")
        for name in ("vmctl", "vmtui") + (("qemu-iso-lab",) if launcher else ()):
            (work / "bin" / name).write_text("#!/bin/sh\n")
            (work / "bin" / name).chmod(0o755)
        shutil.copy2(ROOT / "tools" / "install_launchers.py", work / "tools" / "install_launchers.py")

    def update(self, **kw):
        args = argparse.Namespace(check=kw.get("check", False), yes=True, dry_run=kw.get("dry_run", False))
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                mock.patch.object(updater.host_setup, "missing_tools", return_value=kw.get("missing", [])), \
                mock.patch.object(updater.host_setup, "install_tools") as install:
            code = updater.cmd_update(args)
        return code, out.getvalue(), install

    def test_fast_forwards_relinks_and_refreshes_the_menu_entry(self):
        before = git(self.checkout, "rev-parse", "HEAD")
        code, out, install = self.update(missing=["xorriso"])
        self.assertEqual(code, 0)
        self.assertNotEqual(git(self.checkout, "rev-parse", "HEAD"), before)
        self.assertIn("0.20.0 -> 0.21.0", out)
        for name in ("vmctl", "vmtui", "qemu-iso-lab"):  # the new launcher is linked too
            self.assertEqual((self.prefix / "bin" / name).resolve(), self.checkout / "bin" / name)
        self.assertIn("qemu-iso-lab/bin/qemu-iso-lab", (self.data / "applications" / "qemu-iso-lab.desktop").read_text())
        install.assert_called_once()
        self.assertEqual(install.call_args.args[0], ["xorriso"])

    def test_check_lists_the_release_and_moves_nothing(self):
        before = git(self.checkout, "rev-parse", "HEAD")
        code, out, install = self.update(check=True)
        self.assertEqual(code, 0)
        self.assertIn("1 new commit(s)", out)
        self.assertIn("vmctl 0.21.0", out)
        self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), before)
        install.assert_not_called()
        self.assertEqual(self.update()[0], 0)
        code, out, _ = self.update()
        self.assertIn("already up to date: vmctl 0.21.0", out)

    def test_local_edits_and_local_commits_stop_it(self):
        (self.checkout / "vmctl" / "__init__.py").write_text('__version__ = "mine"\n')
        with self.assertRaises(VMError) as ctx:
            self.update()
        self.assertIn("local changes", str(ctx.exception))
        git(self.checkout, "checkout", "--", "vmctl/__init__.py")
        (self.checkout / "NOTES").write_text("x")
        git(self.checkout, "add", "NOTES")
        git(self.checkout, "commit", "-q", "-m", "local work")
        with self.assertRaises(VMError) as ctx:
            self.update()
        self.assertIn("origin does not", str(ctx.exception))

    def test_not_a_checkout(self):
        shutil.rmtree(self.checkout / ".git")
        with self.assertRaises(VMError):
            self.update()


if __name__ == "__main__":
    unittest.main()
