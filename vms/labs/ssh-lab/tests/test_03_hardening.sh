#!/usr/bin/env bash
# Exercise 3 — tighten sshd_config, validate, reload, and put it back. The server stays reachable.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 3 — hardening sshd_config${RESET}"; echo ""

restore() { on ssh-lab-server "test -f /etc/ssh/sshd_config.bak && sudo cp /etc/ssh/sshd_config.bak /etc/ssh/sshd_config && sudo systemctl reload ssh" >/dev/null 2>&1 || true; }
trap restore EXIT

on ssh-lab-server "sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak" >/dev/null
on ssh-lab-server "sudo sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config" >/dev/null
on ssh-lab-server "sudo sed -i 's/^#\\?MaxAuthTries.*/MaxAuthTries 3/' /etc/ssh/sshd_config" >/dev/null
assert "the tightened config passes sshd -t" on ssh-lab-server "sudo sshd -t"
assert "PermitRootLogin is now no" on ssh-lab-server "grep -q '^PermitRootLogin no' /etc/ssh/sshd_config"
assert "MaxAuthTries is now 3" on ssh-lab-server "grep -q '^MaxAuthTries 3' /etc/ssh/sshd_config"
assert "the server reloads cleanly" on ssh-lab-server "sudo systemctl reload ssh"
sleep 1
assert "the server is still reachable after the reload" on ssh-lab-server true
restore
assert "the original config is back and valid" on ssh-lab-server "sudo sshd -t && grep -q -v '^MaxAuthTries 3' /etc/ssh/sshd_config"

report_results "Exercise 3"
