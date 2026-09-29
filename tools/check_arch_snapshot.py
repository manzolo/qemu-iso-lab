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
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vmctl import arch_archive, archinstall, config  # noqa: E402

USER_AGENT = arch_archive.USER_AGENT


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vm", help="an Arch profile with archinstall_config.archive_date")
    parser.add_argument("--date", help="another snapshot date (YYYY/MM/DD) for the same package set")
    args = parser.parse_args(argv)
    vm = config.get_vm(config.load_config(), args.vm)
    date = args.date or str((archinstall.archinstall_config(vm) or {}).get("archive_date") or "")
    if not date:
        raise SystemExit(f"{args.vm} has no archinstall_config.archive_date: pass --date")
    packages = arch_archive.snapshot(date)
    closure, missing = arch_archive.resolve(arch_archive.package_set(archinstall.archinstall_config(vm) or {}), packages)
    urls = {name: arch_archive.package_url(date, packages[name]) for name in closure}
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
