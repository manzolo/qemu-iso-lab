# VPN lab: WireGuard and OpenVPN between two machines

Two Ubuntu 24.04 servers on an isolated segment, with WireGuard and OpenVPN installed and
nothing configured. You build the tunnels by hand, watch what the network sees, and fence the
server with iptables. Ported from qlab's vpn-lab: `labuser` became the profile's user and the LAN
moved to 172.20.1.0/24 (192.168.100.0/24 is what `vmctl link` uses).

| VM | Role | Segment `vpn-lan` | SSH from the host |
|---|---|---|---|
| `vpn-lab-server` | the VPN endpoint: WireGuard 51820/udp, OpenVPN 1194/udp | 172.20.1.1 | `vmctl shell vpn-lab-server` (127.0.0.1:2359) |
| `vpn-lab-client` | connects through the segment, then through the tunnels | 172.20.1.2 | `vmctl shell vpn-lab-client` (127.0.0.1:2360) |

Each VM has two NICs: a NAT one for the host (SSH, Internet) and `vpn-lan`, a segment shared by
the two VMs only. The tunnels are private networks on top of the segment: WireGuard
`10.10.0.0/24` (server `.1`, client `.2`), OpenVPN `10.20.0.0/24` (server `.1`, client `.2`).

## Start

```bash
vmctl group install vpn-lab       # two cloud images, a minute each after the download; then the stack comes up
vmctl group status vpn-lab
vmctl group map vpn-lab --open    # the map, the access table and the exercises
```

Open two terminals, one per machine: `vmctl shell vpn-lab-server` and `vmctl shell vpn-lab-client`.
A single command without a session: `vmctl shell vpn-lab-server -- sudo wg show`.

## Exercise 1: WireGuard

**Keys, on both machines.** A key pair each; only the public halves travel:

```bash
wg genkey | tee privatekey | wg pubkey > publickey
cat publickey
```

**The server** (`/etc/wireguard/wg0.conf`), with its own private key and the client's public key:

```ini
[Interface]
Address = 10.10.0.1/24
ListenPort = 51820
PrivateKey = <SERVER_PRIVATE_KEY>

[Peer]
PublicKey = <CLIENT_PUBLIC_KEY>
AllowedIPs = 10.10.0.2/32
```

**The client**, with the server's public key and its segment address as Endpoint:

```ini
[Interface]
Address = 10.10.0.2/24
PrivateKey = <CLIENT_PRIVATE_KEY>

[Peer]
PublicKey = <SERVER_PUBLIC_KEY>
Endpoint = 172.20.1.1:51820
AllowedIPs = 10.10.0.1/32
PersistentKeepalive = 25
```

`PersistentKeepalive` keeps the tunnel alive with a packet every 25 s. Bring it up, server first:

```bash
sudo wg-quick up wg0          # on both
sudo wg show                  # "latest handshake: N seconds ago" means the tunnel is up
ping 10.10.0.1                # from the client, through the tunnel
sudo wg-quick down wg0        # tear down
```

No handshake? Check the public keys (each end carries the *other* end's), the port
(`ListenPort` = `Endpoint`), that the server came up first, and that the segment itself works
(`ping 172.20.1.1` from the client, without VPN).

## Exercise 2: OpenVPN with a static key

The simplest OpenVPN: one shared secret, no certificates. On the server:

```bash
sudo openvpn --genkey secret /etc/openvpn/static.key
sudo cat /etc/openvpn/static.key          # paste it on the client as /etc/openvpn/static.key
```

`/etc/openvpn/server.conf` on the server, `/etc/openvpn/client.ovpn` on the client:

```
dev tun                              dev tun
ifconfig 10.20.0.1 10.20.0.2         remote 172.20.1.1 1194 udp
secret /etc/openvpn/static.key       ifconfig 10.20.0.2 10.20.0.1
cipher AES-256-CBC                   secret /etc/openvpn/static.key
auth SHA256                          cipher AES-256-CBC
port 1194                            auth SHA256
proto udp                            persist-tun
keepalive 10 60                      persist-key
persist-tun                          verb 3
persist-key
verb 3
```

Both ends name the cipher: OpenVPN 2.6 on OpenSSL 3 no longer ships the static-key default
BF-CBC (`Cipher BF-CBC not supported` and a fatal exit, the first thing this lab's own test found).

Server first, in the foreground to read its log, then the client:

```bash
sudo openvpn --config /etc/openvpn/server.conf          # server
sudo openvpn --config /etc/openvpn/client.ovpn --daemon # client; then: ping 10.20.0.1
```

Both sides print `Initialization Sequence Completed`. `--daemon` puts the client in the
background; stop it with `sudo pkill openvpn`.

## Exercise 3: what the wire sees

With a tunnel up, capture on the server while the client pings through it:

```bash
sudo tcpdump -ni vpn-lan udp port 51820     # WireGuard: UDP ciphertext on the segment
sudo tcpdump -ni wg0                        # the same pings, in clear, inside the tunnel
sudo tcpdump -ni vpn-lan udp port 1194      # OpenVPN on the segment; tun0 for the clear side
```

That contrast is the whole point of a VPN: the segment carries nothing readable.

## Exercise 4: only the VPN and SSH get in

On the server, allow what you need and drop the rest. SSH first: vmctl's own SSH (and yours)
comes through the NAT NIC, and a lab that locks itself out is no fun.

```bash
sudo iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT
sudo iptables -A INPUT -p udp --dport 51820 -j ACCEPT
sudo iptables -A INPUT -p udp --dport 1194 -j ACCEPT
sudo iptables -A INPUT -j DROP
sudo iptables -L INPUT -n -v
```

From the client, `ping 172.20.1.1` is now dropped while `ping 10.10.0.1` through WireGuard still
answers. `sudo iptables -F` puts everything back.

## Tests

```bash
vmctl group test vpn-lab          # all five scripts, the stack started if needed
bash vms/labs/vpn-lab/tests/test_03_wireguard_tunnel.sh
```

`test_01` the machines and the segment, `test_02` the keys, `test_03` a real WireGuard tunnel
(built and torn down), `test_04` a real OpenVPN static-key tunnel (daemons started and stopped),
`test_05` the iptables fence (applied and flushed). Each script leaves the lab as it found it.

## Stop and clean

```bash
vmctl group down vpn-lab
vmctl group clean vpn-lab         # deletes the two overlay disks (asks first); the cloud image stays in isos/
```
