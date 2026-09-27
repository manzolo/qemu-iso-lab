#!/usr/bin/env python3
"""Copy the newest recordings into docs/media/<vm>/, what the catalog site shows on each card.

    tools/collect_media.py                 # every VM with artifacts/<vm>/recording/latest/recording.gif
    tools/collect_media.py reactos kali    # only these
    tools/collect_media.py --mp4           # also recording.mp4 + poster.png (a video player on the card)
    tools/collect_media.py --max-kb 800    # refuse a GIF above this size (default 1024)
    tools/collect_media.py --report        # from the newest check-vms report (check-vms --record); --report-dir DIR for another

The GIF is what the repository carries; an MP4 is opt-in and heavy, keep it for the profiles
whose install is worth a player. Prints one line per profile with the size, and a total.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vms", nargs="*", help="profiles to collect (default: every one with a recording)")
    parser.add_argument("--mp4", action="store_true", help="also copy recording.mp4 and poster.png when present")
    parser.add_argument("--max-kb", type=int, default=1024, help="largest GIF accepted, in KiB (default: 1024)")
    parser.add_argument("--report", action="store_true", help="take the recordings of the newest check-vms --record run")
    parser.add_argument("--report-dir", type=Path, metavar="DIR", help="take the recordings of this check-vms report")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    artifacts = args.root / "artifacts"
    if args.report or args.report_dir:
        base = args.root / "artifacts" / "check-vms"
        if args.report_dir:
            report = args.report_dir
        else:
            reports = sorted((d for d in base.glob("*") if (d / "recordings").is_dir()), key=lambda d: d.name)
            if not reports:
                print(f"no check-vms report with recordings under {base}", file=sys.stderr)
                return 1
            report = reports[-1]
        source_of = lambda name: report / "recordings" / name
        print(f"recordings from {report}")
        names = args.vms or sorted(p.name for p in (report / "recordings").iterdir() if (p / "recording.gif").is_file())
    else:
        source_of = lambda name: artifacts / name / "recording" / "latest"
        names = args.vms or sorted(p.name for p in artifacts.iterdir() if (p / "recording" / "latest" / "recording.gif").is_file())
    total = 0
    copied = 0
    for name in names:
        source = source_of(name)
        gif = source / "recording.gif"
        if not gif.is_file():
            print(f"{name:<32} no recording (vmctl record {name}, or check-vms --record)")
            continue
        size = gif.stat().st_size
        if size > args.max_kb * 1024:
            print(f"{name:<32} GIF is {size // 1024} KiB, above --max-kb {args.max_kb}: re-encode with a shorter --gif-seconds")
            continue
        target = args.root / "docs" / "media" / name
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(gif, target / "recording.gif")
        note = f"gif {size // 1024} KiB"
        total += size
        if args.mp4:
            for extra in ("recording.mp4", "poster.png"):
                if (source / extra).is_file():
                    shutil.copy2(source / extra, target / extra)
                    total += (source / extra).stat().st_size
                    note += f", {extra.split('.')[1]} {(source / extra).stat().st_size // 1024} KiB"
        else:
            for extra in ("recording.mp4", "poster.png"):  # GIF-only by default: an old MP4 does not linger
                (target / extra).unlink(missing_ok=True)
        copied += 1
        print(f"{name:<32} {note}")
    print(f"{copied} profile(s) into docs/media, {total / 1024 / 1024:.1f} MiB")
    return 0 if copied or not names else 1


if __name__ == "__main__":
    sys.exit(main())
