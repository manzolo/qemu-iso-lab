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
           (r"…", ","), (r"\bVM\b", "V M"), (r"\bMy VMs\b", "My V Ms"),
           (r"(?i)\bmicrok8s\b", "micro k8s"),  # then the mixed-token rule spells k8s
           # English loanwords read letter by letter in Italian: "file" came out as "fi-le" (Manzolo, 2026-10-05).
           (r"\bfiles?\b", "fàil"), (r"\b[Cc]lients?\b", "claient"),  # read as Italian letters otherwise (2026-10-05)
           (r"/proc\b", "proc"), (r"\by\b", "ipsilon"), (r"\bkvm\b", "KVM"),
           (r"\bv(\d+(?:\.\d+)*)\b", r"vu \1"),  # a tag v1.0: "vu uno punto zero" (the version rule takes the rest)
           # Enclitic imperatives get the stress wrong ("aprìlo"): say them in two words (Manzolo, 2026-10-05).
           (r"\baprilo\b", "apri il fàil"), (r"\bchiudilo\b", "chiudi il fàil")],
}


# File extensions the Italian voice must spell: "setup.sh" read raw came out as a made-up word.
EXTENSIONS_IT = {"sh": "esse acca", "json": "jason", "iso": "iso", "md": "emme di", "exe": "exe", "cmd": "ci emme di",
                 "ps1": "pi esse uno", "qcow2": "qcow due", "py": "pi greco", "txt": "ti ics ti", "yaml": "yaml", "toml": "toml",
                 "conf": "conf", "cfg": "ci effe gi", "log": "log", "service": "service", "img": "img", "pdf": "pi di effe",
                 "html": "acca ti emme elle", "csv": "ci esse vu", "gif": "gif", "mp4": "emme pi quattro"}

_UNITS_IT = ["zero", "uno", "due", "tre", "quattro", "cinque", "sei", "sette", "otto", "nove", "dieci", "undici", "dodici",
             "tredici", "quattordici", "quindici", "sedici", "diciassette", "diciotto", "diciannove"]
_TENS_IT = ["", "", "venti", "trenta", "quaranta", "cinquanta", "sessanta", "settanta", "ottanta", "novanta"]


def number_it(digits: str) -> str:
    """An integer in Italian words. A leading zero is read digit by digit ("04" -> "zero quattro",
    the way Ubuntu's 24.04 is said); five digits or more are read digit by digit too."""
    if digits.startswith("0") and len(digits) > 1 or len(digits) > 4:
        return " ".join(_UNITS_IT[int(c)] for c in digits)
    n = int(digits)
    if n < 20:
        return _UNITS_IT[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        word = _TENS_IT[tens]
        if unit in (1, 8):
            word = word[:-1]  # ventuno, ventotto
        return word + (_UNITS_IT[unit] if unit else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        word = ("cento" if hundreds == 1 else _UNITS_IT[hundreds] + "cento")
        if rest and rest // 10 == 8:
            word = word[:-1]  # centottanta
        return word + (number_it(str(rest)) if rest else "")
    thousands, rest = divmod(n, 1000)
    word = "mille" if thousands == 1 else number_it(str(thousands)) + "mila"
    return word + (number_it(str(rest)) if rest else "")


_LETTERS_IT = {"a": "a", "b": "bi", "c": "ci", "d": "di", "e": "e", "f": "effe", "g": "gi", "h": "acca", "i": "i", "j": "i lunga",
               "k": "cappa", "l": "elle", "m": "emme", "n": "enne", "o": "o", "p": "pi", "q": "cu", "r": "erre", "s": "esse",
               "t": "ti", "u": "u", "v": "vu", "w": "doppia vu", "x": "ics", "y": "ipsilon", "z": "zeta"}


CONSONANT_ACRONYM = re.compile(r"\b[B-DF-HJ-NP-TV-Z]{2,5}\b")  # KVM, WSL, SSH, TCP, DNS, VPN, ZFS, LVM, DHCP, HTTP...


def spell_it(letters: str) -> str:
    """Letter names, the Italian way."""
    return " ".join(_LETTERS_IT[c] for c in letters.lower())


def mixed_token_it(token: str) -> str:
    """A word with digits inside, the way an Italian would say it: `microk8s` -> "micro kappa otto
    esse", `ext4` -> "ext quattro", `x86_64` -> "ics ottantasei sessantaquattro", `ttyS0` -> "tty esse
    zero". Runs of three letters or more stay words, shorter ones are spelled with the Italian letter
    names; XTTS given "microk8s" raw produced something unintelligible (Manzolo, 2026-10-05)."""
    parts = []
    # Letter runs split where the case changes too: MicroK8s -> Micro, K, 8, s; ttyS0 -> tty, S, 0.
    for run in re.findall(r"[A-Z]?[a-z]+|[A-Z]+|\d+", token):
        if run.isdigit():
            parts.append(number_it(run))
        elif len(run) >= 3 and not CONSONANT_ACRONYM.fullmatch(run):
            parts.append(run)
        else:
            parts.append(spell_it(run))  # WSL2 -> vu esse elle due
    return " ".join(parts)


def spoken(text: str, lang: str) -> str:
    """What XTTS is given for a cue: the subtitle text rewritten the way the voice should say it.

    `narrate.py --show <clip>` prints it per cue, to be read before anything is synthesized: the
    Italian voice read a sentence-ending full stop aloud and invented words for "24.04" and
    "setup.sh" (Manzolo, 2026-10-05), so every number, dotted version and file name is spelled
    out in words here, and no full stop is left for the model to pronounce."""
    for pattern, repl in SPOKEN[lang]:
        text = re.sub(pattern, repl, text)
    if lang == "it":
        text = re.sub(r"\b(\w+)\.(" + "|".join(EXTENSIONS_IT) + r")\b",
                      lambda m: f"{m.group(1)} punto {EXTENSIONS_IT[m.group(2)]}", text)
        # An upper-case acronym without a vowel cannot be read as a word: KVM, WSL, SSH, TCP, DNS, VPN
        # ("KVM" and "WSL" came out unintelligible, Manzolo 2026-10-05); QEMU, ISO, NAT stay words.
        text = CONSONANT_ACRONYM.sub(lambda m: spell_it(m.group(0)), text)
        # Letters and digits in one word: microk8s, k8s, ext4, x86_64, ttyS0, md0.
        text = re.sub(r"\b(?=[A-Za-z0-9_]*\d)(?=[A-Za-z0-9_]*[A-Za-z])[A-Za-z0-9_]+\b",
                      lambda m: mixed_token_it(m.group(0)), text)
        # Versions and decimals: 24.04 -> ventiquattro punto zero quattro, 3.0.1 -> tre punto zero punto uno.
        text = re.sub(r"\b\d+(?:\.\d+)+\b", lambda m: " punto ".join(number_it(p) for p in m.group(0).split(".")), text)
        text = re.sub(r"\b\d+\b", lambda m: number_it(m.group(0)), text)
    # XTTS reads a sentence-ending full stop aloud ("punto", "dot"): a comma instead, between
    # sentences and before a closing quote or bracket. The cue still ends with a soft mark (a comma
    # if it has none), the convention of manzolo/poetry-voice's ensure_soft_punctuation.
    text = re.sub(r"\.(?=[\s\"”»')\]]|$)", ",", text)
    text = re.sub(r"\s+,", ",", text).strip()
    text = text.strip(",").strip()  # a cue that continues the previous one starts with "…"
    return text if text.endswith((";", ":", "!", "?")) else text + ","


def duration(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True, check=True).stdout)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--speaker", default="Claribel Dervla")
    ap.add_argument("--speaker-wav", default=None, help="clone this voice instead of a built-in speaker")
    ap.add_argument("--music", default=None, help="a background track (public domain or CC0: see artifacts/tour/music/CREDITS.md), "
                    "looped under the whole clip, faded in and out, ducked under the voice")
    ap.add_argument("--music-db", type=float, default=-8.0, help="gain on the track before ducking (default -8 dB: the public-domain piano of CREDITS.md sits about 17 dB under the voice in the pauses)")
    ap.add_argument("--voice", default="it", help="languages to synthesize, comma-separated (default: it only, the "
                    "decision of 2026-10-05; the English track then carries the Italian voice under English subtitles; "
                    "--voice it,en for both)")
    ap.add_argument("--show", action="store_true", help="print what the voice will be given, per cue and language, and stop: "
                    "read it before synthesizing (numbers, versions and file names are spelled out)")
    args = ap.parse_args()
    d = Path(args.clip)
    meta = json.loads((d / "cues.json").read_text())
    name = meta["clip"]
    voice_langs = tuple(lang for lang in LANGS if lang in args.voice.split(","))
    if args.show:
        for i, c in enumerate(meta["cues"]):
            for lang in voice_langs:
                print(f"{i:02} {lang}  {c[lang]}\n       -> {spoken(c[lang], lang)}")
        return
    src = build.cut(d, meta)  # cut.mkv and the cue times on its timeline
    total = duration(src)
    voice_id = args.speaker_wav or args.speaker
    jobs, files = [], {}
    for i, c in enumerate(meta["cues"]):
        for lang in voice_langs:
            text = spoken(c[lang], lang)
            key = hashlib.sha256(f"{voice_id}|{lang}|{text}".encode()).hexdigest()[:12]
            out = (d / "voice" / f"{i:02}-{lang}-{key}.wav").resolve()
            files[(i, lang)] = out
            jobs.append({"text": text, "lang": lang, "out": str(out)})
        for lang in LANGS:  # a language without its own voice gets the first synthesized one
            files.setdefault((i, lang), files[(i, voice_langs[0])])
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
        mixed = f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0,volume=-3dB,apad,atrim=0:{new_total:.3f}"
        # The voice's chain is the same with or without music (alimiter also auto-levels: the
        # voice must come out of it exactly as in the clips without a bed).
        if args.music:
            graph += f";{''.join(labels)}{mixed},alimiter=limit=0.8,asplit=2[v{lang}][sc{lang}]"
        else:
            graph += f";{''.join(labels)}{mixed},alimiter=limit=0.8[a{lang}]"
    music_inputs: list[str] = []
    if args.music:
        # The bed: looped to the clip's length, quiet, faded in and out, then pressed down further
        # whenever the voice speaks (the voice is the sidechain) and let back up in the pauses.
        music_idx = 1 + len(inputs) // 2
        music_inputs = ["-stream_loop", "-1", "-i", str(Path(args.music).resolve())]
        fade_out = max(0.0, new_total - 4)
        graph += (f";[{music_idx}:a]aresample=48000,aformat=channel_layouts=mono,atrim=0:{new_total:.3f},"
                  f"volume={args.music_db}dB,afade=t=in:d=3,afade=t=out:st={fade_out:.3f}:d=4,asplit={len(LANGS)}"
                  + "".join(f"[m{lang}]" for lang in LANGS))
        for lang in LANGS:
            graph += (f";[m{lang}][sc{lang}]sidechaincompress=threshold=0.02:ratio=6:attack=30:release=600[d{lang}]"
                      f";[v{lang}][d{lang}]amix=inputs=2:normalize=0:dropout_transition=0,alimiter=limit=0.95:level=false[a{lang}]")

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
    n_audio_inputs = 1 + len(inputs) // 2 + (1 if args.music else 0)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), *inputs, *music_inputs, *sub_inputs,
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


if __name__ == "__main__":
    main()
