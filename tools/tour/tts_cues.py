"""Runs in artifacts/tts/xtts (Python 3.11 + coqui-tts, tools/tour/setup-tts.sh): one WAV per cue and language.

    tts_cues.py jobs.json
jobs.json: {"speaker": "Claribel Dervla", "jobs": [{"text": ..., "lang": "it", "out": "/abs/x.wav"}, ...]}
A job whose output already exists is skipped (the file name carries a hash of text + voice).
"""
import json
import os
import sys

os.environ["COQUI_TOS_AGREED"] = "1"
spec = json.load(open(sys.argv[1]))
todo = [j for j in spec["jobs"] if not os.path.exists(j["out"])]
if todo:
    from TTS.api import TTS
    tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cuda")
    for job in todo:
        os.makedirs(os.path.dirname(job["out"]), exist_ok=True)
        kwargs = {"speaker_wav": spec["speaker_wav"]} if spec.get("speaker_wav") else {"speaker": spec["speaker"]}
        tts.tts_to_file(job["text"], language=job["lang"], file_path=job["out"] + ".tmp.wav", **kwargs)
        os.replace(job["out"] + ".tmp.wav", job["out"])
print(f"{len(todo)} generated, {len(spec['jobs']) - len(todo)} cached")
