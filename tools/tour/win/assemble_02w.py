#!/usr/bin/env python3
"""02w-windows take 2: the kept ranges of the two recorded segments -> raw.mkv, and cues.json
with cue starts and fast-forwards mapped onto the new timeline (build.py then cuts the ff)."""
import json
import subprocess
import sys
from pathlib import Path

SRC = Path("artifacts/tour/out/02w-windows.take2")
OUT = Path("artifacts/tour/out/02w-windows")
DRAFT = Path(sys.argv[1])

# (segment, start, end) in source seconds, in order.
RANGES = [
    ("a", 2, 20), ("a", 80, 104), ("a", 306, 360), ("a", 360, 425), ("a", 596, 608),
    ("b", 6, 24), ("b", 36, 60), ("b", 72, 90), ("b", 90, 345), ("b", 345, 372), ("b", 400, 440),
    ("b", 440, 575), ("b", 575, 750), ("b", 750, 762), ("b", 800, 835), ("b", 885, 940),
    ("b", 1132, 1150), ("b", 1195, 1205), ("b", 1243, 1251),
]
# Fast-forwards in source coordinates (segment, start, end, factor): the installs.
FF = [("a", 364, 412, 8), ("b", 96, 326, 8), ("b", 445, 515, 6), ("b", 580, 740, 12)]
# Cue id -> (segment, source second).
CUES = {
    "intro": ("a", 2), "site": ("a", 14), "keep": ("a", 84), "warn": ("a", 312), "wsl": ("a", 350),
    "reboot": ("a", 598), "ubuntu": ("b", 78), "user": ("b", 346), "third": ("b", 402),
    "confirm": ("b", 522), "apt": ("b", 578), "welcome": ("b", 752), "kvm": ("b", 812),
    "icon": ("b", 896), "identity": ("b", 1133), "catalog": ("b", 1198), "end": ("b", 1244),
}


def mapped(seg: str, t: float) -> float:
    """A source moment -> its second in raw.mkv (the moment must fall in a kept range)."""
    acc = 0.0
    for s, a, b in RANGES:
        if s == seg and a <= t <= b:
            return acc + (t - a)
        acc += b - a
    raise SystemExit(f"{seg}:{t} is not in a kept range")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, (seg, a, b) in enumerate(RANGES):
        part = SRC / f"part-{i:02}.mkv"
        if not part.exists():
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(a), "-to", str(b), "-i", str(SRC / f"02w-{seg}.mkv"),
                            "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "25", str(part)], check=True)
        parts.append(part)
    lst = SRC / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(OUT / "raw.mkv")], check=True)
    meta = json.loads(DRAFT.read_text())
    for c in meta["cues"]:
        seg, t = CUES[c["id"]]
        c["start"] = round(mapped(seg, t), 2)
    meta["cues"].sort(key=lambda c: c["start"])
    total = sum(b - a for _, a, b in RANGES)
    for i, c in enumerate(meta["cues"]):
        c["end"] = meta["cues"][i + 1]["start"] if i + 1 < len(meta["cues"]) else round(total, 2)
        c["top"] = False
    meta["ff"] = [{"start": round(mapped(s, a), 2), "end": round(mapped(s, b), 2), "factor": f} for s, a, b, f in FF]
    meta["steps"] = []
    (OUT / "cues.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(b - a for _, a, b in RANGES)
    print(f"raw.mkv {total:.0f}s from {len(RANGES)} ranges; cues: " + ", ".join(f"{c['id']}@{c['start']}" for c in meta["cues"]))


main()
