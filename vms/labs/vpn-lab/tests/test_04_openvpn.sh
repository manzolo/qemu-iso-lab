#!/usr/bin/env bash
# Exercise 2 — an OpenVPN static-key tunnel 10.20.0.0/24, server and client as daemons, then stopped.
# OpenVPN 2.6 on OpenSSL 3: the static-key default cipher BF-CBC is gone ("Cipher BF-CBC not supported",
# first live run 2026-10-02), so both ends name AES-256-CBC.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 4 — OpenVPN static key${RESET}"; echo ""

cleanup() { for vm in vpn-lab-server vpn-lab-client; do on "$vm" "sudo pkill -x openvpn; sudo rm -f /etc/openvpn/static.key /etc/openvpn/server.conf /etc/openvpn/client.ovpn" >/dev/null 2>&1 || true; done; }
cleanup
trap cleanup EXIT

version=$(on vpn-lab-server openvpn --version 2>/dev/null | head -1 || true)
assert_contains "OpenVPN is installed on the server" "$version" "^OpenVPN 2"
on vpn-lab-server "sudo openvpn --genkey secret /etc/openvpn/static.key"
key=$(on vpn-lab-server sudo cat /etc/openvpn/static.key)
assert_contains "a static key was generated" "$key" "BEGIN OpenVPN Static key"
printf '%s\n' "$key" | on vpn-lab-client "sudo tee /etc/openvpn/static.key >/dev/null && sudo chmod 600 /etc/openvpn/static.key"
on vpn-lab-server "sudo tee /etc/openvpn/server.conf >/dev/null" <<'EOF_SRV'
dev tun
ifconfig 10.20.0.1 10.20.0.2
secret /etc/openvpn/static.key
cipher AES-256-CBC
auth SHA256
port 1194
proto udp
keepalive 10 60
persist-tun
persist-key
verb 3
EOF_SRV
on vpn-lab-client "sudo tee /etc/openvpn/client.ovpn >/dev/null" <<'EOF_CLI'
dev tun
remote 172.20.1.1 1194 udp
ifconfig 10.20.0.2 10.20.0.1
secret /etc/openvpn/static.key
cipher AES-256-CBC
auth SHA256
persist-tun
persist-key
verb 3
EOF_CLI
assert "the server daemon starts" on vpn-lab-server sudo openvpn --config /etc/openvpn/server.conf --daemon --log /tmp/openvpn-server.log
assert "the client daemon starts" on vpn-lab-client sudo openvpn --config /etc/openvpn/client.ovpn --daemon --log /tmp/openvpn-client.log
up=""
for _ in $(seq 1 15); do
    if on vpn-lab-client ping -c1 -W1 10.20.0.1 >/dev/null 2>&1; then up=yes; break; fi
    sleep 1
done
assert "the client reaches the server through tun0" test "$up" = yes
log=""
for _ in $(seq 1 10); do   # the line lands a moment after the first packet goes through
    log=$(on vpn-lab-client sudo cat /tmp/openvpn-client.log 2>/dev/null || true)
    grep -q "Initialization Sequence Completed" <<<"$log" && break
    sleep 1
done
assert_contains "the client log says Initialization Sequence Completed" "$log" "Initialization Sequence Completed"

report_results "Exercise 4"
