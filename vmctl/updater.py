"""`vmctl update`: bring the checkout to the latest main and finish what setup.sh would.

The one-line installer leaves a git clone in ~/qemu-iso-lab (`install.sh` never pulls on a rerun,
so an update needs a command of its own): fetch, fast-forward the current branch to its upstream,
then the steps of setup.sh that an update can need again: the symlinks in ~/.local/bin (a new
launcher), the application-menu entry, and the host tools a new release depends on (asked first,
like setup). Local edits to tracked files stop it before anything moves: a pull over them would
fail halfway or merge, and local.json is not tracked, so a plain checkout never has any.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

from vmctl import host_setup, runtime, state, ui
from vmctl.errors import VMError

LINKED = ("vmctl", "vmtui", "qemu-iso-lab")  # what setup.sh links into ~/.local/bin


def git(*args: str, cwd: Path | None = None) -> str:
    """One git command, its stdout; a failure is a VMError with git's own words."""
    proc = subprocess.run(["git", *args], cwd=str(cwd or state.ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        raise VMError(f"git {' '.join(args)}: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def checkout_version(root: Path) -> str:
    """The version string of the tree on disk (not of the module already imported)."""
    match = re.search(r'__version__\s*=\s*"([^"]+)"', (root / "vmctl" / "__init__.py").read_text(encoding="utf-8"))
    return match.group(1) if match else "?"


def local_changes(root: Path) -> list[str]:
    """Tracked files edited or staged in the checkout (untracked files are nobody's business)."""
    return [line[3:] for line in git("status", "--porcelain", "--untracked-files=no", cwd=root).splitlines() if line.strip()]


def relink(root: Path, prefix: Path) -> list[str]:
    """setup.sh's symlinks again, only where that prefix already has them: a new release can add a launcher."""
    bindir = prefix / "bin"
    if not any((bindir / name).is_symlink() for name in LINKED):
        return []
    done = []
    for name in LINKED:
        target = root / "bin" / name
        if target.exists():
            link = bindir / name
            if link.is_symlink() or not link.exists():
                if link.is_symlink():
                    link.unlink()
                link.symlink_to(target)
                done.append(name)
    return done


def cmd_update(args: argparse.Namespace) -> int:
    root = Path(state.ROOT)
    dry_run = bool(getattr(args, "dry_run", False))
    check_only = bool(getattr(args, "check", False))
    if not (root / ".git").exists():
        raise VMError(f"{root} is not a git checkout: update it the way it was installed")
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=root)
    if branch == "HEAD":
        raise VMError("the checkout is on a detached commit: `git switch main` first")
    changed = local_changes(root)
    if changed and not check_only:
        raise VMError("local changes to tracked files would be overwritten: " + ", ".join(changed[:6])
                      + (" ..." if len(changed) > 6 else "") + "\n  commit or `git stash` them, then `vmctl update` again")
    ui.print_header("Update")
    before = git("rev-parse", "--short", "HEAD", cwd=root)
    version_before = checkout_version(root)
    git("fetch", "--quiet", "origin", branch, cwd=root)
    upstream = f"origin/{branch}"
    behind = int(git("rev-list", "--count", f"HEAD..{upstream}", cwd=root) or "0")
    ahead = int(git("rev-list", "--count", f"{upstream}..HEAD", cwd=root) or "0")
    if behind == 0:
        ui.print_status("ok", f"already up to date: vmctl {version_before} ({before} on {branch})"
                        + (f", {ahead} local commit(s) not on origin" if ahead else ""))
        return 0
    titles = git("log", "--oneline", f"HEAD..{upstream}", cwd=root).splitlines()
    ui.print_status("info", f"{behind} new commit(s) on {upstream}" + (f", {ahead} local one(s) ahead" if ahead else ""))
    for line in titles[:12]:
        print(f"    {line}")
    if len(titles) > 12:
        print(f"    ... and {len(titles) - 12} more")
    if check_only:
        print("  Run `vmctl update` to apply.")
        return 0
    if ahead:
        raise VMError(f"the checkout has {ahead} commit(s) origin does not: `git pull --rebase` or `git merge` by hand")
    runtime.run(["git", "-C", str(root), "pull", "--ff-only", "--quiet", "origin", branch], dry_run=dry_run)
    if dry_run:
        return 0
    after = git("rev-parse", "--short", "HEAD", cwd=root)
    version_after = checkout_version(root)
    ui.print_status("ok", f"vmctl {version_before} -> {version_after} ({before} -> {after})")

    # What setup.sh did at install time and a new release can need again.
    prefix = Path(os.environ.get("PREFIX") or Path.home() / ".local")
    linked = relink(root, prefix)
    if linked:
        ui.print_status("ok", f"linked {', '.join(linked)} in {prefix / 'bin'}")
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    if (data_home / "applications" / "qemu-iso-lab.desktop").exists():
        runtime.run(["python3", str(root / "tools" / "install_launchers.py"), str(root)], show_command=False)
    missing = host_setup.missing_tools()
    if missing:
        ui.print_status("info", "new release, tools still missing: " + ", ".join(missing))
        host_setup.install_tools(missing, assume_yes=bool(getattr(args, "yes", False)), dry_run=False)
    print("  A dashboard or TUI already open keeps running the previous code: start it again.")
    return 0
