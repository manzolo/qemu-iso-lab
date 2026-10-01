#!/usr/bin/env python3
"""Copy the newest recordings into docs/media/<vm>/, what the catalog site shows on each card.

    tools/collect_media.py                 # every VM with artifacts/<vm>/recording/latest/recording.gif
    tools/collect_media.py reactos kali    # only these
    tools/collect_media.py --mp4           # also recording.mp4 + poster.png (a video player on the card)
    tools/collect_media.py --max-kb 800    # refuse a GIF above this size (default 1024)
    tools/collect_media.py --report        # from the newest check-vms report (check-vms --record); --report-dir DIR for another
    tools/collect_media.py ... --publish   # then replace the single commit of the "media" branch and force-push it
    tools/collect_media.py ... --force     # copy even when the last frame did not change

docs/media/ is a worktree of the orphan branch "media", not part of main: every refresh of 130 clips
added ~15 MB to main's history for good. The branch holds one commit, amended and force-pushed by
--publish, and the Pages workflow checks it out into docs/media/. A clip whose last frame looks the
same as the one already there (UNCHANGED_DIFF) is kept: re-encoding the same desktop changes bytes
every run.

The GIF is what the repository carries; an MP4 is opt-in and heavy, keep it for the profiles
whose install is worth a player. Prints one line per profile with the size, and a total.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA_BRANCH = "media"
UNCHANGED_DIFF = 4.0  # mean absolute difference (0-255) of the last frames, 64x40 grey, under which a clip is "the same"


def last_frame(gif: Path) -> bytes | None:
    """The last frame as 64x40 grey bytes, or None without ffmpeg / an unreadable file."""
    try:
        out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(gif), "-update", "1", "-vf", "scale=64:40,format=gray",
                              "-f", "rawvideo", "-"], capture_output=True, stdin=subprocess.DEVNULL, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return out[-64 * 40:] if len(out) >= 64 * 40 else None


def same_clip(new: Path, old: Path) -> bool:
    if not old.is_file():
        return False
    a, b = last_frame(new), last_frame(old)
    if a is None or b is None:
        return False
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) < UNCHANGED_DIFF


def publish(media: Path) -> int:
    """Replace the single commit of the media branch with the worktree's content and force-push it."""
    git = ["git", "-C", str(media)]
    branch = subprocess.run(git + ["rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True).stdout.strip()
    if branch != MEDIA_BRANCH:
        print(f"{media} is not a worktree of the {MEDIA_BRANCH!r} branch (git worktree add docs/media {MEDIA_BRANCH})", file=sys.stderr)
        return 1
    subprocess.run(git + ["add", "-A"], check=True)
    if subprocess.run(git + ["diff", "--cached", "--quiet"]).returncode == 0:
        print("media: nothing changed, nothing to publish")
        return 0
    subprocess.run(git + ["commit", "-q", "--amend", "--no-edit"], check=True)
    subprocess.run(git + ["push", "-q", "--force", "origin", MEDIA_BRANCH], check=True)
    print(f"media: published (one commit on {MEDIA_BRANCH}, force-pushed)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vms", nargs="*", help="profiles to collect (default: every one with a recording)")
    parser.add_argument("--mp4", action="store_true", help="also copy recording.mp4 and poster.png when present")
    parser.add_argument("--max-kb", type=int, default=1024, help="largest GIF accepted, in KiB (default: 1024)")
    parser.add_argument("--report", action="store_true", help="take the recordings of the newest check-vms --record run")
    parser.add_argument("--report-dir", type=Path, metavar="DIR", help="take the recordings of this check-vms report")
    parser.add_argument("--force", action="store_true", help="copy even when the last frame looks the same as the clip already there")
    parser.add_argument("--publish", action="store_true", help="then amend the media branch's single commit and force-push it")
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
        if not args.force and not args.mp4 and same_clip(gif, target / "recording.gif"):
            print(f"{name:<32} unchanged (same last frame), kept")
            continue
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
    if args.publish:
        return publish(args.root / "docs" / "media")
    return 0


if __name__ == "__main__":
    sys.exit(main())
