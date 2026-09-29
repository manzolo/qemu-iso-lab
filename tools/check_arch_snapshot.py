#!/usr/bin/env python3
"""Check that an Arch Linux Archive snapshot still serves every package a profile would install.

    tools/check_arch_snapshot.py arch-2014                 # the profile's archive_date and package set
    tools/check_arch_snapshot.py arch-2014 --date 2014/02/01

The snapshot directories under archive.archlinux.org/repos/<date>/ redirect each package to
/packages/<letter>/<name>/<file>, and some old files are gone from there: the archive then answers
HTTP 500 and pacstrap fails after minutes of downloads (arch-2019's libusbmuxd, 2026-09-29); files
older than 2019 are redirected once more, to archive.org, whose nodes also fail now and then. This reads the snapshot's own databases, resolves the install set the way pacman
would (groups, dependencies, provides; the first provider wins), and asks the archive for every
file. Nothing is installed and no VM runs.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import io
import re
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vmctl import archinstall, config  # noqa: E402

ARCHIVE = "https://archive.archlinux.org/repos"
REPOS = ("core", "extra", "community")
USER_AGENT = "qemu-iso-lab check_arch_snapshot"


def fetch(url: str, timeout: float = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return bytes(response.read())


def read_db(date: str, repo: str) -> dict[str, dict[str, list[str]]]:
    """name -> {FILENAME, DEPENDS, PROVIDES, GROUPS, ...} of one repository database."""
    data = fetch(f"{ARCHIVE}/{date}/{repo}/os/x86_64/{repo}.db")
    packages: dict[str, dict[str, list[str]]] = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            if not member.name.endswith(("/desc", "/depends")):
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            fields: dict[str, list[str]] = {}
            key = ""
            for line in handle.read().decode("utf-8", "replace").splitlines():
                if line.startswith("%") and line.endswith("%"):
                    key = line.strip("%")
                    fields[key] = []
                elif line and key:
                    fields[key].append(line)
            name = member.name.split("/")[0].rsplit("-", 2)[0]
            entry = packages.setdefault(name, {"REPO": [repo]})
            entry.update(fields)
    return packages


def bare(dependency: str) -> str:
    return re.split(r"[<>=:]", dependency, maxsplit=1)[0].strip()


def resolve(wanted: list[str], packages: dict[str, dict[str, list[str]]]) -> tuple[list[str], list[str]]:
    """The install closure of *wanted* and what could not be found at all."""
    groups: dict[str, list[str]] = {}
    providers: dict[str, str] = {}
    for name, entry in packages.items():
        for group in entry.get("GROUPS", []):
            groups.setdefault(group, []).append(name)
        for provided in entry.get("PROVIDES", []):
            providers.setdefault(bare(provided), name)
    closure: list[str] = []
    missing: list[str] = []
    queue = list(wanted)
    while queue:
        item = bare(queue.pop(0))
        if item in closure:
            continue
        if item in packages:
            closure.append(item)
            queue.extend(packages[item].get("DEPENDS", []))
        elif item in groups:
            queue.extend(sorted(groups[item]))
        elif item in providers:
            queue.append(providers[item])
        elif item not in missing:
            missing.append(item)
    return closure, missing


def status(url: str, attempts: int = 4) -> int:
    """HTTP status after redirects (old files end on archive.org), retried: its data nodes answer
    500 now and then for a file they do have, which is a flake, not a missing package."""
    code = 0
    for attempt in range(attempts):
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return int(response.status)
        except urllib.error.HTTPError as exc:
            code = int(exc.code)
        except OSError:
            code = 0
        if attempt + 1 < attempts:
            time.sleep(10)
    return code


def package_set(vm: dict) -> list[str]:
    cfg = archinstall.archinstall_config(vm) or {}
    base = {"base", "base-devel", "git", "linux-firmware", "networkmanager", "openssh", *cfg.get("kernels", ["linux"])}
    if str(cfg.get("bootloader") or "Grub").lower() == "grub":
        base |= {"efibootmgr", "grub"}
    return sorted(base | set(cfg.get("packages") or []))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vm", help="an Arch profile with archinstall_config.archive_date")
    parser.add_argument("--date", help="another snapshot date (YYYY/MM/DD) for the same package set")
    args = parser.parse_args(argv)
    vm = config.get_vm(config.load_config(), args.vm)
    date = args.date or str((archinstall.archinstall_config(vm) or {}).get("archive_date") or "")
    if not date:
        raise SystemExit(f"{args.vm} has no archinstall_config.archive_date: pass --date")
    packages: dict[str, dict[str, list[str]]] = {}
    for repo in REPOS:
        try:
            for name, entry in read_db(date, repo).items():
                packages.setdefault(name, entry)  # core before extra before community, like pacman.conf
        except urllib.error.HTTPError:
            continue
    closure, missing = resolve(package_set(vm), packages)
    urls = {name: f"{ARCHIVE}/{date}/{packages[name]['REPO'][0]}/os/x86_64/{packages[name]['FILENAME'][0]}" for name in closure}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        codes = dict(zip(urls, pool.map(status, urls.values())))
    broken = sorted(name for name, code in codes.items() if code != 200)
    print(f"{args.vm} on {date}: {len(closure)} packages to install")
    for name in missing:
        print(f"  not in the snapshot: {name}")
    for name in broken:
        print(f"  HTTP {codes[name]}: {packages[name]['FILENAME'][0]}")
    if not broken and not missing:
        print("  every file is served")
    return 1 if broken or missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
