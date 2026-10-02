#!/usr/bin/env bash
# Exercise 1 — the engine, the CLI and why no sudo is needed. Read-only.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 1 — Docker anatomy${RESET}"; echo ""

assert "the docker service is active" on docker-lab-server systemctl is-active docker
ver=$(on docker-lab-server docker version --format '{{.Server.Version}}' 2>/dev/null || true)
assert_contains "the CLI talks to the engine without sudo" "$ver" "^[0-9]+\.[0-9]+"
assert "the lab user is in the docker group" on docker-lab-server "id -nG | tr ' ' '\n' | grep -qx docker"
assert "the Compose plugin is installed" on docker-lab-server docker compose version
assert "the lab images are already pulled" on docker-lab-server docker image inspect alpine:3.20 nginx:1.27-alpine

report_results "Exercise 1"
