#!/usr/bin/env bash
# Exercise 6 — look at the server from the client the way a scan would, and find the trace in its log.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 6 — what a scan sees${RESET}"; echo ""

# Open SSH from the segment first (the knocking exercise may have left it closed), so the scan has something to find.
on ssh-lab-client "knock 172.20.2.1 7000 8000 9000" >/dev/null 2>&1 || true; sleep 2
close() { on ssh-lab-client "knock 172.20.2.1 9000 8000 7000" >/dev/null 2>&1 || true; }
trap close EXIT

assert "nmap is on the client" on ssh-lab-client command -v nmap
scan=$(on ssh-lab-client "nmap -Pn -p 22 172.20.2.1" 2>/dev/null || true)
assert_contains "the scan reports port 22" "$scan" "22/tcp"
ver=$(on ssh-lab-client "nmap -Pn -sV -p 22 172.20.2.1" 2>/dev/null || true)
assert_contains "service detection names OpenSSH" "$ver" "[Oo]pen[Ss][Ss][Hh]|ssh"
log=$(on ssh-lab-server "sudo journalctl -u ssh --no-pager -n 50" 2>/dev/null || true)
assert_contains "the server's journal shows sshd activity" "$log" "sshd|Server listening|Accepted|Connection"

report_results "Exercise 6"
