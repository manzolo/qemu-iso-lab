#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m unittest discover -s tests -q
./bin/vmctl --help >/dev/null
./bin/vmctl list --json | python3 -m json.tool >/dev/null
./bin/vmctl list-empty-devices >/dev/null
./bin/vmctl list-target-devices >/dev/null
bash -n bin/vmtui
make help >/dev/null
echo "ok"
