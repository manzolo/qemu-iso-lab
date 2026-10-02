#!/usr/bin/env bash
# Exercise 5 — SSH from the segment is closed until the knock, then open, then closed again.
# vmctl reaches the server over NAT, outside the KNOCKD_SSH chain, so it is never locked out.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 5 — port knocking${RESET}"; echo ""

close() { on ssh-lab-client "knock 172.20.2.1 9000 8000 7000" >/dev/null 2>&1 || true; }
close; trap close EXIT

assert "knockd is active on the server" on ssh-lab-server systemctl is-active knockd
assert "the KNOCKD_SSH chain exists and drops by default" on ssh-lab-server "sudo iptables -S KNOCKD_SSH | grep -q '^-A KNOCKD_SSH -j DROP'"

reach() { on ssh-lab-client "timeout 4 bash -c 'echo > /dev/tcp/172.20.2.1/22'"; }
assert_fail "SSH from the segment is closed before the knock" reach
on ssh-lab-client "knock 172.20.2.1 7000 8000 9000" >/dev/null 2>&1 || true
sleep 2
opened=no
for _ in $(seq 1 5); do if reach >/dev/null 2>&1; then opened=yes; break; fi; sleep 1; done
assert "the knock opens SSH from the segment" test "$opened" = yes
close; sleep 2
assert_fail "the reverse knock closes it again" reach

report_results "Exercise 5"
