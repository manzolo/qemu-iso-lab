#!/usr/bin/env python3
"""out/<clip> -> <clip>.voice.mp4: the cut video with a spoken track per language (XTTS-v2), the
subtitles moved with it. Where a sentence is longer than its cue's slot, the last frame of the slot
is held (tpad clone) so the voice never overlaps the next one; both languages share one video, so a
slot grows to the longer of the two. Usage: tools/tour/narrate.py artifacts/tour/out/01-catalog
[--speaker "Claribel Dervla" | --speaker-wav my-voice.wav]. Needs artifacts/tts/xtts (docs/TOUR.md)."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402

ROOT = HERE.parents[1]
XTTS_PY = ROOT / "artifacts" / "tts" / "xtts" / "bin" / "python"
MODELS = ROOT / "artifacts" / "tts" / "models"
GAP = 0.45  # silence after a sentence before the next one may start
LANGS = ("it", "en")

# What the subtitles write and the voice should say instead.
SPOKEN = {
    "en": [(r"qemu-iso-lab", "QEMU ISO Lab"), (r"\bvmctl\b", "vm control"), (r"Ctrl\+Alt\+Del", "Control Alt Delete"),
           (r"…", ","), (r"\bVMs\b", "V Ms"), (r"\bVM\b", "V M")],
    # Italian words that XTTS reads the English way: console -> consolle (con-SOL-le), never the
    # buttons' own names (Open console, Consoles), which are English on the screen too.
    "it": [(r"(?<!Open )\bconsole\b", "consolle"), (r"qemu-iso-lab", "QEMU ISO Lab"), (r"\bvmctl\b", "vm control"), (r"Ctrl\+Alt\+Canc", "Control Alt Canc"),
           (r"…", ","), (r"\bVM\b", "V M"), (r"\bMy VMs\b", "My V Ms")],
}


def spoken(text: str, lang: str) -> str:
    for pattern, repl in SPOKEN[lang]:
        text = re.sub(pattern, repl, text)
    # XTTS reads a sentence-ending full stop aloud ("punto", "dot"): none at the end, a comma
    # between sentences; a dot inside a number (24.04) is followed by a digit and stays.
    text = re.sub(r"\.(?=\s|$)", ",", text)
    return text.strip(" ,")


def duration(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True, check=True).stdout)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--speaker", default="Claribel Dervla")
    ap.add_argument("--speaker-wav", default=None, help="clone this voice instead of a built-in speaker")
    args = ap.parse_args()
    d = Path(args.clip)
    meta = json.loads((d / "cues.json").read_text())
    name = meta["clip"]
    src = build.cut(d, meta)  # cut.mkv and the cue times on its timeline
    total = duration(src)
    voice_id = args.speaker_wav or args.speaker
    jobs, files = [], {}
    for i, c in enumerate(meta["cues"]):
        for lang in LANGS:
            text = spoken(c[lang], lang)
            key = hashlib.sha256(f"{voice_id}|{lang}|{text}".encode()).hexdigest()[:12]
            out = (d / "voice" / f"{i:02}-{lang}-{key}.wav").resolve()
            files[(i, lang)] = out
            jobs.append({"text": text, "lang": lang, "out": str(out)})
    spec = d / "voice" / "jobs.json"
    spec.parent.mkdir(exist_ok=True)
    spec.write_text(json.dumps({"speaker": args.speaker, "speaker_wav": args.speaker_wav, "jobs": jobs}, ensure_ascii=False))
    subprocess.run([str(XTTS_PY), str(HERE / "tts_cues.py"), str(spec)], check=True,
                   env={"TTS_HOME": str(MODELS), "PATH": "/usr/bin:/bin", "HOME": str(Path.home())})

    cues = meta["cues"]
    starts = [c["start"] for c in cues]
    bounds = starts[1:] + [total]
    extra, new_start, shift = [], [], 0.0
    for i, c in enumerate(cues):
        slot = bounds[i] - starts[i]
        need = max(duration(files[(i, lang)]) for lang in LANGS) + GAP
        extra.append(max(0.0, need - slot))
        new_start.append(starts[i] + shift)
        shift += extra[-1]
    new_total = total + shift

    # Video: one piece per cue slot (plus whatever precedes the first cue), the slot's last frame held.
    pieces, chains = [], []
    if starts[0] > 0:
        pieces.append((0.0, starts[0], 0.0))
    pieces += [(starts[i], bounds[i], extra[i]) for i in range(len(cues))]
    for k, (a, b, hold) in enumerate(pieces):
        pad = f",tpad=stop_mode=clone:stop_duration={hold:.3f}" if hold > 0 else ""
        chains.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS{pad}[v{k}]")
    graph = ";".join(chains) + ";" + "".join(f"[v{k}]" for k in range(len(pieces))) + f"concat=n={len(pieces)}:v=1:a=0[vout]"

    # Audio: every sentence at its cue's new start, mixed over silence of the new length.
    inputs: list[str] = []
    for lang in LANGS:
        labels = []
        for i in range(len(cues)):
            inputs += ["-i", str(files[(i, lang)])]
            idx = len(inputs) // 2  # input 0 is the video
            ms = int(round(new_start[i] * 1000))
            graph += f";[{idx}:a]aresample=48000,adelay={ms}|{ms}[{lang}{i}]"
            labels.append(f"[{lang}{i}]")
        graph += f";{''.join(labels)}amix=inputs={len(labels)}:normalize=0:dropout_transition=0,volume=-3dB,alimiter=limit=0.8,apad,atrim=0:{new_total:.3f}[a{lang}]"

    # A moment x of the cut timeline moves past every hold that ends before it.
    def shifted(x: float) -> float:
        return x + sum(extra[i] for i in range(len(cues)) if bounds[i] <= x)

    steps = [{"start": round(shifted(st["start"]), 2), "cmd": st["cmd"]} for st in meta.get("steps") or []]
    (d / f"{name}.voice.steps.json").write_text(json.dumps(steps, ensure_ascii=False, indent=1), encoding="utf-8")
    for i, c in enumerate(cues):
        c["start"] = new_start[i]
        c["end"] = (new_start[i + 1] if i + 1 < len(cues) else new_total) - 0.1
    for lang in LANGS:
        lines = []
        for i, c in enumerate(cues, 1):
            text = ("{\\an8}" if c.get("top") else "") + c[lang]
            lines += [str(i), f"{build.ts(c['start'])} --> {build.ts(c['end'])}", text, ""]
        (d / f"{name}.voice.{lang}.srt").write_text("\n".join(lines), encoding="utf-8")
    sub_inputs = ["-i", str(d / f"{name}.voice.it.srt"), "-i", str(d / f"{name}.voice.en.srt")]
    n_audio_inputs = 1 + len(inputs) // 2
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), *inputs, *sub_inputs,
                    "-filter_complex", graph, "-map", "[vout]", "-map", "[ait]", "-map", "[aen]",
                    "-map", f"{n_audio_inputs}", "-map", f"{n_audio_inputs + 1}",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-c:s", "mov_text",
                    "-metadata:s:a:0", "language=ita", "-metadata:s:a:0", "title=Italiano",
                    "-metadata:s:a:1", "language=eng", "-metadata:s:a:1", "title=English",
                    "-metadata:s:s:0", "language=ita", "-metadata:s:s:1", "language=eng",
                    "-disposition:a:0", "default", "-disposition:a:1", "0",
                    "-movflags", "+faststart", str(d / f"{name}.voice.mp4")], check=True)
    # Phone preview: Italian voice + Italian subtitles burned in.
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(d / f"{name}.voice.mp4"), "-map", "0:v", "-map", "0:a:0",
                    "-vf", f"subtitles={d / (name + '.voice.it.srt')}:force_style='FontSize=22,Outline=2'",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-c:a", "copy",
                    "-movflags", "+faststart", str(d / f"{name}.voice.it-burned.mp4")], check=True)
    print(f"{d / (name + '.voice.mp4')}: {total:.1f}s -> {new_total:.1f}s (held {shift:.1f}s)")


main()
