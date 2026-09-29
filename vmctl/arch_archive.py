"""The Arch Linux Archive: what a dated snapshot installs, whether the archive still serves it, and a
host-side package cache for snapshots the guest cannot download reliably.

Snapshot directories (archive.archlinux.org/repos/<date>/) redirect each package to
/packages/<letter>/<name>/<file>, and files older than 2019 once more to archive.org, whose data
nodes answer HTTP 500 now and then for a file they do have (one file: 500 five times, then 200 after
90 s; arch-2014 failed three installs on them, 2026-09-29). ``prefetch`` downloads the whole install
set on the host, as patiently as needed, into ``isos/arch-archive/<date>/`` (reused by every later
run), and the seed CD carries it: the live system installs from it and asks the network only for
what is missing.
"""
from __future__ import annotations

import concurrent.futures
import io
import os
import re
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from vmctl import state, ui
from vmctl.errors import VMError

ARCHIVE = "https://archive.archlinux.org/repos"
REPOS = ("core", "extra", "community")
USER_AGENT = "qemu-iso-lab (Arch Linux Archive snapshot)"
BASE_PACKAGES = ("base", "base-devel", "git", "linux-firmware", "networkmanager", "openssh")
DOWNLOAD_ATTEMPTS = 40  # 15 s apart: ten minutes of archive.org before a file counts as gone
RETRY_DELAY_SEC = 15.0

Packages = dict[str, dict[str, list[str]]]


def fetch(url: str, timeout: float = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return bytes(response.read())


def read_db(date: str, repo: str) -> Packages:
    """name -> {FILENAME, CSIZE, DEPENDS, PROVIDES, GROUPS, REPO} of one repository database."""
    data = fetch(f"{ARCHIVE}/{date}/{repo}/os/x86_64/{repo}.db")
    packages: Packages = {}
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


def snapshot(date: str) -> Packages:
    """Every package of the snapshot, core before extra before community (pacman.conf's order)."""
    packages: Packages = {}
    for repo in REPOS:
        try:
            for name, entry in read_db(date, repo).items():
                packages.setdefault(name, entry)
        except urllib.error.HTTPError:
            continue  # community was merged into extra in 2023
    if not packages:
        raise VMError(f"The Arch Linux Archive has no repository databases for {date}")
    return packages


def bare(dependency: str) -> str:
    return re.split(r"[<>=:]", dependency, maxsplit=1)[0].strip()


def resolve(wanted: list[str], packages: Packages) -> tuple[list[str], list[str]]:
    """The install closure of *wanted* (groups, dependencies, provides; the first provider wins)
    and the names found nowhere."""
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


def package_set(cfg: dict[str, Any]) -> list[str]:
    """What the bootstrap script hands pacstrap (render_bootstrap_script's package line)."""
    names = {*BASE_PACKAGES, *(cfg.get("kernels") or ["linux"]), *(cfg.get("packages") or [])}
    if str(cfg.get("bootloader") or "Grub").strip().lower() == "grub":
        names |= {"efibootmgr", "grub"}
    return sorted(names)


def package_url(date: str, entry: dict[str, list[str]]) -> str:
    return f"{ARCHIVE}/{date}/{entry['REPO'][0]}/os/x86_64/{entry['FILENAME'][0]}"


def cache_dir(date: str) -> Path:
    return state.ROOT / "isos" / "arch-archive" / date.replace("/", "-")


def _download(url: str, target: Path, size: int, attempts: int, delay: float) -> str | None:
    """None once *target* holds the file (of the database's size when it says one), else why not."""
    if target.is_file() and (not size or target.stat().st_size == size):
        return None
    partial = target.with_name(target.name + ".part")
    reason = "no attempt"
    for attempt in range(attempts):
        try:
            data = fetch(url, timeout=120)
            if size and len(data) != size:
                raise OSError(f"{len(data)} bytes instead of {size}")
            partial.write_bytes(data)
            os.replace(partial, target)
            return None
        except urllib.error.HTTPError as exc:
            reason = f"HTTP {exc.code}"
            if exc.code == 404:
                break
        except OSError as exc:
            reason = str(exc)
        if attempt + 1 < attempts:
            time.sleep(delay)
    partial.unlink(missing_ok=True)
    return reason


def prefetch(date: str, cfg: dict[str, Any], *, attempts: int = DOWNLOAD_ATTEMPTS, delay: float = RETRY_DELAY_SEC,
             workers: int = 6, dry_run: bool = False) -> Path:
    """Download the install set of *cfg* at *date* into the host cache and return its directory."""
    directory = cache_dir(date)
    if dry_run:
        print(f"  would cache the {date} install set under {ui.pretty_path(directory)}")
        return directory
    packages = snapshot(date)
    closure, missing = resolve(package_set(cfg), packages)
    if missing:
        raise VMError(f"Not in the {date} snapshot: {', '.join(missing)}")
    directory.mkdir(parents=True, exist_ok=True)
    jobs = {name: (package_url(date, packages[name]), directory / packages[name]["FILENAME"][0],
                   int((packages[name].get("CSIZE") or ["0"])[0])) for name in closure}
    cached = sum(1 for _, target, size in jobs.values() if target.is_file() and (not size or target.stat().st_size == size))
    ui.print_note(f"Arch Linux Archive {date}: {len(jobs)} packages, {cached} already in {ui.pretty_path(directory)}")
    failures: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_download, url, target, size, attempts, delay): name for name, (url, target, size) in jobs.items()}
        for done, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            reason = future.result()
            if reason:
                failures[futures[future]] = reason
            if done % 50 == 0:
                ui.print_note(f"  {done} of {len(jobs)} packages checked")
    if failures:
        raise VMError(f"The archive did not serve {len(failures)} package(s) of {date}: "
                      + ", ".join(f"{name} ({why})" for name, why in sorted(failures.items())))
    ui.print_status("ok", f"{len(jobs)} packages of {date} cached")
    return directory
