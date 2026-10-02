#!/usr/bin/env bash
# Exercise 6 — an image built from a Dockerfile on top of alpine, run, and removed.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 6 — build an image${RESET}"; echo ""

cleanup() { on docker-lab-server "docker image rm -f lab-hello >/dev/null 2>&1; rm -rf ~/build-demo" >/dev/null 2>&1 || true; }
cleanup; trap cleanup EXIT

on docker-lab-server "mkdir -p ~/build-demo && cat > ~/build-demo/Dockerfile" <<'EOF_DF'
FROM alpine:3.20
RUN echo built-in-the-lab > /message
CMD ["cat", "/message"]
EOF_DF
assert "docker build makes the image" on docker-lab-server "cd ~/build-demo && docker build -q -t lab-hello ."
out=$(on docker-lab-server docker run --rm lab-hello 2>/dev/null || true)
assert_contains "the image runs its CMD" "$out" "^built-in-the-lab$"
layers=$(on docker-lab-server docker image history -q lab-hello 2>/dev/null | wc -l || true)
assert_contains "the new image has layers on top of alpine" "$layers" "^[2-9]|^[1-9][0-9]"

report_results "Exercise 6"
