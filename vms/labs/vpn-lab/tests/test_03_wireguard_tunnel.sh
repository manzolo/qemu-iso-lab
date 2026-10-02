#!/usr/bin/env bash
# Exercise 1 — the WireGuard tunnel 10.10.0.0/24 between the two machines, then torn down.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 3 — WireGuard tunnel${RESET}"; echo ""

cleanup() { for vm in vpn-lab-server vpn-lab-client; do on "$vm" "sudo wg-quick down wg0 >/dev/null 2>&1; sudo rm -f /etc/wireguard/wg0.conf" >/dev/null 2>&1 || true; done; }
cleanup
trap cleanup EXIT

server_priv=$(on vpn-lab-server wg genkey); server_pub=$(printf '%s\n' "$server_priv" | on vpn-lab-server wg pubkey)
client_priv=$(on vpn-lab-client wg genkey); client_pub=$(printf '%s\n' "$client_priv" | on vpn-lab-client wg pubkey)

on vpn-lab-server "sudo tee /etc/wireguard/wg0.conf >/dev/null && sudo chmod 600 /etc/wireguard/wg0.conf" <<EOF_WG
[Interface]
Address = 10.10.0.1/24
ListenPort = 51820
PrivateKey = ${server_priv}

[Peer]
PublicKey = ${client_pub}
AllowedIPs = 10.10.0.2/32
EOF_WG
on vpn-lab-client "sudo tee /etc/wireguard/wg0.conf >/dev/null && sudo chmod 600 /etc/wireguard/wg0.conf" <<EOF_WG
[Interface]
Address = 10.10.0.2/24
PrivateKey = ${client_priv}

[Peer]
PublicKey = ${server_pub}
Endpoint = 172.20.1.1:51820
AllowedIPs = 10.10.0.1/32
PersistentKeepalive = 25
EOF_WG

assert "wg-quick up on the server" on vpn-lab-server sudo wg-quick up wg0
assert "wg-quick up on the client" on vpn-lab-client sudo wg-quick up wg0
status=$(on vpn-lab-server sudo wg show wg0 2>/dev/null || true)
assert_contains "wg0 is up on the server, listening on 51820" "$status" "listening port: 51820"
assert "the client reaches the server through the tunnel" on vpn-lab-client ping -c2 -W3 10.10.0.1
assert "the server reaches the client through the tunnel" on vpn-lab-server ping -c2 -W3 10.10.0.2
handshake=$(on vpn-lab-client sudo wg show wg0 latest-handshakes 2>/dev/null || true)
assert_contains "a handshake happened" "$handshake" "[1-9][0-9]{9}"
cipher=$(on vpn-lab-server "sudo timeout 4 tcpdump -ni vpn-lan -c 2 udp port 51820 2>/dev/null & sleep 1; ping -c2 -W2 10.10.0.2 >/dev/null; wait" || true)
assert_contains "WireGuard frames seen on the segment" "$cipher" "172\.20\.1\.2\.[0-9]+ > 172\.20\.1\.1\.51820|172\.20\.1\.1\.51820 > 172\.20\.1\.2"

report_results "Exercise 3"
