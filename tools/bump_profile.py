#!/usr/bin/env python3
"""Bump a tracked profile's version and record what changed.

    tools/bump_profile.py ubuntu-26.04 patch -m "gdm autologin drop-in"
    tools/bump_profile.py --check              # what the test suite checks: catalog and lock agree
    tools/bump_profile.py --init               # first run, or a new profile: 1.0.0 + lock entry
    tools/bump_profile.py --history ubuntu-26.04
    tools/bump_profile.py --prune              # forget lock entries of profiles that left the catalog

patch: a fix to the recipe (reinstalling is optional). minor: something more (packages, a
shared folder). major: an installed VM is no longer comparable (disk layout, user, port,
release). A new release of a distribution is a new profile, not a major bump.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vmctl import profile_versions  # noqa: E402
from vmctl.errors import VMError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", nargs="?", help="the tracked profile to bump")
    parser.add_argument("part", nargs="?", choices=profile_versions.PARTS, help="which number to raise")
    parser.add_argument("-m", "--message", default="", help="what changed (goes into the lock's history)")
    parser.add_argument("--check", action="store_true", help="report every disagreement between catalog and lock, exit 1 if any")
    parser.add_argument("--init", action="store_true", help="stamp 1.0.0 on unversioned profiles and record missing lock entries")
    parser.add_argument("--history", metavar="NAME", help="print a profile's version history")
    parser.add_argument("--prune", action="store_true", help="drop lock entries whose profile is gone")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.check:
            problems = profile_versions.check(args.root)
            for line in problems:
                print(line)
            print("catalog and lock agree" if not problems else f"{len(problems)} problem(s)")
            return 1 if problems else 0
        if args.init:
            touched = profile_versions.init(args.message or "first versioned catalog", args.root)
            print(f"{len(touched)} profile(s) recorded" + (": " + " ".join(touched) if touched else ""))
            return 0
        if args.history:
            lines = profile_versions.history(args.history, args.root)
            if not lines:
                print(f"{args.history}: no history (not a tracked profile, or not in the lock yet)")
                return 1
            for item in lines:
                print(f"{item['version']:<10} {item['date']}  {item['note']}")
            return 0
        if args.prune:
            gone = profile_versions.prune(args.root)
            print(f"{len(gone)} lock entr{'y' if len(gone) == 1 else 'ies'} removed" + (": " + " ".join(gone) if gone else ""))
            return 0
        if not args.name or not args.part:
            parser.error("name and part are required (or one of --check, --init, --history, --prune)")
        old, new = profile_versions.bump(args.name, args.part, args.message, args.root)
        print(f"{args.name}: {old} -> {new}")
        return 0
    except VMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
