#!/usr/bin/env python3
"""out/<clip>/raw.mkv + cues.json -> <clip>.en.srt, <clip>.it.srt, <clip>.mp4 (H.264 + two subtitle
tracks, English default), an Italian burned-in preview for phones and a review sheet.
Usage: tools/tour/build.py artifacts/tour/out/01-catalog"""
import json
import shutil
import subprocess
import sys
from pathlib import Path


def ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def cut(d: Path, meta: dict) -> Path:
    """raw.mkv -> cut.mkv with the fast-forward segments sped up (and a "▶▶ N×" badge on them);
    the cue times are mapped onto the new timeline in place."""
    segs = sorted((s for s in meta.get("ff", []) if s.get("end")), key=lambda s: s["start"])
    out = d / "cut.mkv"
    if not segs:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(d / "raw.mkv"), "-c", "copy", str(out)], check=True)
        return out
    duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                     str(d / "raw.mkv")], capture_output=True, text=True, check=True).stdout)
    pieces, t = [], 0.0
    for s in segs:
        if s["start"] > t:
            pieces.append((t, s["start"], 1))
        pieces.append((s["start"], s["end"], s["factor"]))
        t = s["end"]
    if t < duration:
        pieces.append((t, duration, 1))

    def mapped(x: float) -> float:
        acc = 0.0
        for a, b, f in pieces:
            if x <= b:
                return acc + (max(x, a) - a) / f
            acc += (b - a) / f
        return acc

    chains, badges, acc = [], [], 0.0
    for i, (a, b, f) in enumerate(pieces):
        chains.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=(PTS-STARTPTS)/{f}[v{i}]")
        if f != 1:
            badges.append(f"drawtext=fontfile={FONT}:text='▶▶ {f}×':fontsize=34:fontcolor=white:box=1:boxcolor=black@0.55:"
                          f"boxborderw=12:x=w-tw-30:y=30:enable='between(t,{acc:.3f},{acc + (b - a) / f:.3f})'")
        acc += (b - a) / f
    graph = ";".join(chains) + ";" + "".join(f"[v{i}]" for i in range(len(pieces))) + \
        f"concat=n={len(pieces)}:v=1:a=0[c];[c]{','.join(badges) or 'null'}[out]"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(d / "raw.mkv"), "-filter_complex", graph,
                    "-map", "[out]", "-c:v", "libx264", "-preset", "fast", "-crf", "14", str(out)], check=True)
    for c in meta["cues"]:
        c["start"] = mapped(c["start"])
        if c["end"] is not None:
            c["end"] = mapped(c["end"])
    return out


def main() -> None:
    d = Path(sys.argv[1])
    meta = json.loads((d / "cues.json").read_text())
    name = meta["clip"]
    src = cut(d, meta)
    for lang in ("en", "it"):
        lines = []
        for i, c in enumerate(meta["cues"], 1):
            end = c["end"] if c["end"] is not None else c["start"] + 4
            text = ("{\\an8}" if c.get("top") else "") + c[lang]
            lines += [str(i), f"{ts(c['start'])} --> {ts(end - 0.05)}", text, ""]
        (d / f"{name}.{lang}.srt").write_text("\n".join(lines), encoding="utf-8")
    subprocess.run([
        "ffmpeg", "-loglevel", "error", "-y", "-i", str(src),
        "-i", str(d / f"{name}.en.srt"), "-i", str(d / f"{name}.it.srt"),
        "-map", "0:v", "-map", "1", "-map", "2",
        "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:s", "mov_text",
        "-metadata:s:s:0", "language=eng", "-metadata:s:s:0", "title=English",
        "-metadata:s:s:1", "language=ita", "-metadata:s:s:1", "title=Italiano",
        "-disposition:s:0", "default", "-disposition:s:1", "0",
        "-metadata", f"title={meta['title']['en']}",
        str(d / f"{name}.mp4"),
    ], check=True)
    # Review sheet: one frame in the middle of each cue, subtitles burned in (Italian).
    sheet = d / "review"
    shutil.rmtree(sheet, ignore_errors=True)
    sheet.mkdir()
    for i, c in enumerate(meta["cues"], 1):
        end = c["end"] if c["end"] is not None else c["start"] + 4
        t = (c["start"] + end) / 2
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", str(d / f"{name}.mp4"),
                        "-vf", f"subtitles={d / (name + '.it.srt')}:force_style='FontSize=20',scale=800:-1",
                        "-copyts", "-frames:v", "1", str(sheet / f"{i:02}-{c['id']}.png")], check=True)
    # The phone preview: Italian burned in (soft subtitles rarely show on a phone).
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src),
                    "-vf", f"subtitles={d / (name + '.it.srt')}:force_style='FontSize=22,Outline=2'",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(d / f"{name}.it-burned.mp4")], check=True)
    print(d / f"{name}.mp4")


if __name__ == "__main__":
    main()
