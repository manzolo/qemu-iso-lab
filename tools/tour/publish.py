#!/usr/bin/env python3
"""The narrated clips -> docs/media/tour/ (the media branch's worktree), what the catalog site's
tour.html plays: per clip one MP4 per language (video + that voice: tour.html switches the file
with the language), WebVTT subtitles per language, a poster, and tour.json. Then publish the media branch and rebuild the site (docs/TOUR.md):

    tools/tour/publish.py [clip ...]          # default: every artifacts/tour/out/<clip> with a .voice.mp4
    git -C docs/media add -A && git -C docs/media commit --amend --no-edit && git -C docs/media push --force origin media
    gh workflow run pages.yml --ref main      # a push to media alone does not rebuild the site
"""
import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "tour" / "out"
DEST = ROOT / "docs" / "media" / "tour"
LANGS = ("it", "en")  # the order of the audio tracks narrate.py writes


def vtt(srt: str) -> str:
    out = ["WEBVTT", ""]
    for block in srt.strip().split("\n\n"):
        lines = block.split("\n")
        timing, text = lines[1].replace(",", "."), "\n".join(lines[2:])
        if text.startswith("{\\an8}"):  # a cue placed on top (it would hide something at the bottom)
            text, timing = text[len("{\\an8}"):], timing + " line:6%"
        out += [timing, text, ""]
    return "\n".join(out)


def duration(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True, check=True).stdout)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="*")
    ap.add_argument("--index-only", action="store_true", help="only rewrite tour.json's order, no clip re-encoded")
    ap.add_argument("--poster-at", type=float, default=30.0, help="second of the poster frame (default 30)")
    args = ap.parse_args()
    names = [] if args.index_only else args.clips or sorted(d.name for d in OUT.iterdir() if (d / f"{d.name}.voice.mp4").is_file())
    if not DEST.parent.is_dir():
        raise SystemExit(f"{DEST.parent} is missing: git fetch origin media && git worktree add docs/media media")
    DEST.mkdir(exist_ok=True)
    index = DEST / "tour.json"
    clips = {c["id"]: c for c in json.loads(index.read_text())["clips"]} if index.is_file() else {}
    for name in names:
        d = OUT / name
        meta = json.loads((d / "cues.json").read_text())
        src = d / f"{name}.voice.mp4"
        for i, lang in enumerate(LANGS):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:v", "-map", f"0:a:{i}",
                            "-c", "copy", "-movflags", "+faststart", str(DEST / f"{name}.{lang}.mp4")], check=True)
            (DEST / f"{name}.{lang}.vtt").write_text(vtt((d / f"{name}.voice.{lang}.srt").read_text(encoding="utf-8")), encoding="utf-8")
        (DEST / f"{name}.mp4").unlink(missing_ok=True)  # the single Italian-only file of 2026-10-05
        length = duration(src)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(min(args.poster_at, length / 2)), "-i", str(src),
                        "-frames:v", "1", "-vf", "scale=640:-1", "-q:v", "4", str(DEST / f"{name}.jpg")], check=True)
        steps_file = d / f"{name}.voice.steps.json"
        steps = json.loads(steps_file.read_text(encoding="utf-8")) if steps_file.is_file() else []
        clips[name] = {"id": name, "title": meta["title"], "duration": round(length, 1), "lab": meta.get("lab"), "series": meta.get("series") or "tour",
                       "order": meta.get("order", 1),  # several lessons on one lab: the beginners' one (order 0) first
                       "steps": [{"t": st["start"], "cmd": st["cmd"]} for st in steps],
                       "video": {lang: f"{name}.{lang}.mp4" for lang in LANGS}, "poster": f"{name}.jpg",
                       "subtitles": {lang: f"{name}.{lang}.vtt" for lang in ("en", "it")}}
        print(f"{name}: {length:.1f}s")
    index.write_text(json.dumps({"clips": [clips[k] for k in ordered(clips)]}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{index}: {len(clips)} clips. Now publish the media branch and run the Pages workflow (see the docstring).")


# The lab lessons in the order of a learning path (Manzolo, 2026-10-07: alphabetical by lab was a
# jumble): one heading per track on tour.html, the labs of a track in this order, a lab's own
# lessons by their `order`. A lab missing here goes last, with a warning: add it where it belongs.
LAB_TRACKS = [
    ("basics", ["git-lab", "ssh-lab"]),
    ("storage", ["lvm-lab", "mdadm-lab", "zfs-lab"]),
    ("network", ["netlab", "vpn-lab"]),
    ("services", ["mysql-lab", "docker-lab", "k8s-lab"]),
    ("virtualization", ["proxmox-lab"]),
]
SERIES = ("tour", "labs", "courses")


def track_of(clip: dict) -> str | None:
    """The heading a clip sits under: the lab's track for a lesson, `<lab>-course` for a course
    (zfs-lab -> zfs-course), none for the tour's chapters."""
    series, lab = clip.get("series", "tour"), clip.get("lab") or ""
    if series == "labs":
        return next((track for track, labs in LAB_TRACKS if lab in labs), "other")
    if series == "courses":
        return clip.get("track") or f"{lab.removesuffix('-lab')}-course"
    return None


def ordered(clips: dict) -> list:
    """The tour's chapters first, by id (05-lab records on vpn-lab: its lab must not move it after
    07-map, as it did on 2026-10-06), then the lab lessons along LAB_TRACKS (a lab's own lessons by
    order), then the courses, each by its episodes' order. Sets every clip's `track`."""
    position = {lab: (t, i) for t, (_, labs) in enumerate(LAB_TRACKS) for i, lab in enumerate(labs)}

    def key(k: str) -> tuple:
        clip = clips[k]
        clip["track"] = track_of(clip)
        series = clip.get("series", "tour")
        if series == "tour":
            return (0, 0, 0, k, 0)
        if series == "labs":
            lab = clip.get("lab") or k
            if lab not in position:
                print(f"warning: {k}: lab {lab} is in no track of LAB_TRACKS (tools/tour/publish.py): listed last")
            t, i = position.get(lab, (len(LAB_TRACKS), 0))
            return (1, t, i, lab, clip.get("order", 1))
        return (2 + (SERIES.index(series) if series in SERIES else len(SERIES)), 0, 0, clip.get("track") or k, clip.get("order", 1))
    return sorted(clips, key=key)


if __name__ == "__main__":
    main()
