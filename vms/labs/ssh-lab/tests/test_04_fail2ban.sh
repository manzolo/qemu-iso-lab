#!/usr/bin/env bash
# Exercise 4 — fail2ban bans the client after wrong passwords on the lab segment, then it is cleared.
# The attempts are meant to fail auth, so no real credential is needed; the ban is on 172.20.2.0/24
# only (the NAT side is in ignoreip), so vmctl's own SSH is never affected. Always unban at the end.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 4 — fail2ban${RESET}"; echo ""

unban()  { on ssh-lab-server "sudo fail2ban-client set sshd unbanip 172.20.2.2" >/dev/null 2>&1 || true; }
# SSH from the segment is behind the knock chain; open it so the failed logins reach sshd and are logged,
# close it again at the end (the knocking exercise expects it closed).
close()  { on ssh-lab-client "knock 172.20.2.1 9000 8000 7000" >/dev/null 2>&1 || true; }
finish() { unban; close; }
unban
on ssh-lab-client "knock 172.20.2.1 7000 8000 9000" >/dev/null 2>&1 || true; sleep 2
trap finish EXIT

status=$(on ssh-lab-server "sudo fail2ban-client status sshd" 2>/dev/null || true)
assert_contains "the sshd jail is active" "$status" "Banned IP list|Currently banned|Total banned"
assert "the jail ignores the host NAT network" on ssh-lab-server "sudo fail2ban-client get sshd ignoreip | grep -q '10.0.2.0/24'"

# A handful of wrong-password attempts from the client over the segment.
on ssh-lab-client "for i in \$(seq 1 5); do sshpass -p deliberately-wrong ssh -o StrictHostKeyChecking=no -o PreferredAuthentications=password -o PubkeyAuthentication=no -o ConnectTimeout=5 nobody@172.20.2.1 true 2>/dev/null || true; done" >/dev/null 2>&1 || true

banned=""
for _ in $(seq 1 10); do
    banned=$(on ssh-lab-server "sudo fail2ban-client status sshd" 2>/dev/null || true)
    grep -q "172.20.2.2" <<<"$banned" && break
    sleep 2
done
assert_contains "the client is banned after the failures" "$banned" "172\.20\.2\.2"
unban
cleared=$(on ssh-lab-server "sudo fail2ban-client status sshd" 2>/dev/null || true)
assert_not_contains "the ban is cleared again" "$cleared" "172\.20\.2\.2"

report_results "Exercise 4"
