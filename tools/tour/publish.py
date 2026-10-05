#!/usr/bin/env python3
"""The narrated clips -> docs/media/tour/ (the media branch's worktree), what the catalog site's
tour.html plays: per clip one MP4 (the video with the Italian voice: the voice is Italian only
since 2026-10-05, and two files with the same video doubled the media branch), WebVTT subtitles per
language, a poster, and tour.json. Then publish the media branch and rebuild the site (docs/TOUR.md):

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
    ap.add_argument("--poster-at", type=float, default=30.0, help="second of the poster frame (default 30)")
    args = ap.parse_args()
    names = args.clips or sorted(d.name for d in OUT.iterdir() if (d / f"{d.name}.voice.mp4").is_file())
    if not DEST.parent.is_dir():
        raise SystemExit(f"{DEST.parent} is missing: git fetch origin media && git worktree add docs/media media")
    DEST.mkdir(exist_ok=True)
    index = DEST / "tour.json"
    clips = {c["id"]: c for c in json.loads(index.read_text())["clips"]} if index.is_file() else {}
    for name in names:
        d = OUT / name
        meta = json.loads((d / "cues.json").read_text())
        src = d / f"{name}.voice.mp4"
        # One file, the first audio track (Italian); both languages of tour.json play it.
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:v", "-map", "0:a:0",
                        "-c", "copy", "-movflags", "+faststart", str(DEST / f"{name}.mp4")], check=True)
        for lang in LANGS:
            (DEST / f"{name}.{lang}.mp4").unlink(missing_ok=True)  # the per-language files of before
            (DEST / f"{name}.{lang}.vtt").write_text(vtt((d / f"{name}.voice.{lang}.srt").read_text(encoding="utf-8")), encoding="utf-8")
        length = duration(src)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(min(args.poster_at, length / 2)), "-i", str(src),
                        "-frames:v", "1", "-vf", "scale=640:-1", "-q:v", "4", str(DEST / f"{name}.jpg")], check=True)
        steps_file = d / f"{name}.voice.steps.json"
        steps = json.loads(steps_file.read_text(encoding="utf-8")) if steps_file.is_file() else []
        clips[name] = {"id": name, "title": meta["title"], "duration": round(length, 1), "lab": meta.get("lab"), "series": meta.get("series") or "tour",
                       "steps": [{"t": st["start"], "cmd": st["cmd"]} for st in steps],
                       "video": f"{name}.mp4", "poster": f"{name}.jpg",
                       "subtitles": {lang: f"{name}.{lang}.vtt" for lang in ("en", "it")}}
        print(f"{name}: {length:.1f}s")
    # The tour's chapters first, in order, then the lab lessons.
    ordered = sorted(clips, key=lambda k: (clips[k].get("series") != "tour", k))
    index.write_text(json.dumps({"clips": [clips[k] for k in ordered]}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{index}: {len(clips)} clips. Now publish the media branch and run the Pages workflow (see the docstring).")


main()
