#!/bin/sh
# The voice (XTTS-v2, docs/TOUR.md), on the host, under artifacts/tts/ (gitignored, ~7 GB with the
# model): coqui-tts needs Python <= 3.12, so uv brings its own 3.11; PyTorch comes separately (with
# CUDA when there is a GPU), transformers must stay below 5 and torchcodec does the audio I/O.
# The model downloads on first use (1.9 GB) under the Coqui Public Model License: non-commercial.
set -eu
T="$(cd "$(dirname "$0")/../.." && pwd)/artifacts/tts"
mkdir -p "$T"
[ -x "$T/uvenv/bin/uv" ] || { python3 -m venv "$T/uvenv" && "$T/uvenv/bin/pip" install -q uv; }
[ -x "$T/xtts/bin/python" ] || UV_PYTHON_INSTALL_DIR="$T/python" "$T/uvenv/bin/uv" venv -q --python 3.11 "$T/xtts"
VIRTUAL_ENV="$T/xtts" "$T/uvenv/bin/uv" pip install -q torch torchaudio "coqui-tts[codec]" "transformers>=4.43,<5"
"$T/xtts/bin/python" -c "import torch; print('torch', torch.__version__, 'cuda' if torch.cuda.is_available() else 'CPU only (slow)')"
