# Network lab: pfSense, Pi-hole and a Lubuntu client

Three VMs installed from scratch on an isolated LAN, ported from kvm-lab's
`network-lab` to plain QEMU, with the libvirt road kept through
`vmctl export-libvirt`.

```text
Internet ──► slirp WAN (QEMU user netdev) ──► pfsense-lab  WAN: DHCP · LAN: 192.168.0.1
                                                  │
                                    segment lab-lan (192.168.0.0/24, domain qlan)
                                    ├── pihole-lab      192.168.0.10   DNS + DHCP .150-.199
                                    ├── lubuntu22-lab   192.168.0.100  desktop client
                                    ├── other VMs       `vmctl lab attach <vm>`
                                    └── host            192.168.0.254  (libvirt road only)
```

Profiles: `vms/profiles/network-lab.json`. Commands:

```bash
vmctl lab plan                 # topology, port forwards, install order, libvirt network XML
vmctl lab install              # pfsense-lab -> pihole-lab -> lubuntu22-lab, from empty disks
vmctl lab up                   # start the three VMs headless in the background, in order
vmctl lab status               # role, address, disk and runtime state of every member
vmctl lab down                 # stop them in reverse order
vmctl lab check                # probe the GUIs and SSH ports from the host (forwards, or the LAN after export)
vmctl lab export               # lab down, export-libvirt of the three VMs (lab-lan created if missing), virsh start, check
vmctl lab unexport             # virsh shutdown (waited), unexport-libvirt, back to plain QEMU
vmctl lab libvirt-test         # export + check + unexport in one go (--keep leaves it in libvirt)
vmctl lab attach arch-dms-local --apply   # put another profile on lab-lan (writes local.json)
```

## Where the lab differs from kvm-lab

| kvm-lab (libvirt) | qemu-iso-lab (plain QEMU) |
|---|---|
| `lab-lan` is a libvirt network, the host has `192.168.0.254` on `virbr-lab` | `lab-lan` is a **multicast socket netdev** shared by the VMs of the host; the host has no address on it. The web GUIs and SSH to the members go through **port forwards on the router's WAN** (see below). After `vmctl export-libvirt pfsense-lab` the segment becomes the same libvirt network as kvm-lab, host address included. |
| VM identities from `scripts/lab.env` | Tracked profiles use the generic `lab`/`lab`; personal identities go in `local.json` as for every other profile |
| Linux members finalised through the QEMU guest agent, then the NIC is moved to the LAN in the domain XML | Members install and provision over SSH on an **install-phase** slirp NIC; the `network_lab` post-install hook writes the static LAN address for the next boot; at runtime the same NIC (same slot, same MAC) is on the segment |
| The two legacy pfSense block rules recovered from the old firewall (`192.168.1.50:22`, disabled test rule) | Not carried over: only the default LAN allow rules, the WAN admin rules and the NAT forwards |
| pfSense `blockpriv`/`blockbogons` on the WAN | Off: the WAN is always a private virtual network here, and it is how the host reaches the GUI |

Everything else is the same: pfSense CE 2.7.2 from the local offline ISO (ZFS,
BIOS, scripted `bsdinstall`), Pi-hole v6 from the official installer with a
pre-seeded `pihole.toml` (OpenDNS upstreams, StevenBlack list, local records,
DHCP `.150`-`.199` for 24 h, domain `qlan`), Lubuntu from Ubuntu Server 22.04.5
+ `lubuntu-desktop` with SDDM autologin and the `shared` host folder on the
desktop, Unbound on pfSense with Pi-hole as the system DNS, checksum offload
disabled on the virtio NICs, serial console enabled.

## Settings recovered from the original appliances (kvm-lab, 2026-09-06)

| Component | Original setting | Here |
|---|---|---|
| pfSense CE 2.7.2 | WAN `vtnet0` DHCP; LAN `vtnet1` `192.168.0.1/24` | same |
| pfSense | automatic outbound NAT; DHCP on the LAN **disabled** | same |
| pfSense | system DNS `192.168.0.10`, Unbound enabled | same |
| Pi-hole | `192.168.0.10/24`, gateway `.1`, DHCP **disabled** | Pi-hole v6, DHCP **enabled** (`network_lab.dhcp.enabled`) |
| Pi-hole upstreams | OpenDNS `208.67.222.222`, `208.67.220.220` | same (`network_lab.upstream_dns`) |
| Pi-hole | 18 local records, 3 CNAMEs, StevenBlack list | the router and every member get a record automatically; add yours to `network_lab.hosts` / `cnames` |
| Lubuntu 22.04.5 | static `192.168.0.100/24`, gateway `.1`, DNS `.10` | same |

## How the pieces fit

### Topology in one place

`pfsense-lab.network_lab` holds the LAN (`lan.name` = the segment, `subnet`,
`host_ip` and `bridge` for libvirt, `domain`), the router address and the
Pi-hole DHCP pool. The members only say who they are:

```json
"network_lab": { "role": "pihole", "gateway_vm": "pfsense-lab", "ip": "192.168.0.10", "web_host_port": 8081 }
"network_lab": { "role": "client", "gateway_vm": "pfsense-lab", "ip": "192.168.0.100" }
```

`netlab.topology()` resolves and validates the whole lab from any member:
addresses inside the subnet, no duplicates, DHCP pool clear of the static
members, one Pi-hole at most.

### NICs and phases

```json
"networks": [
  {"id": "install", "type": "user",    "phase": "install"},
  {"id": "lan",     "type": "segment", "name": "lab-lan", "phase": "runtime"}
]
```

`type: user` is slirp (with the `ssh_host_port` forward on the first one),
`type: segment` is the shared L2 segment. A NIC keeps its position and its MAC
(derived from the disk path and the slot, or given with `mac`) across phases,
so the guest sees one interface whose name never changes. The router lists WAN
and LAN without `phase`: both always present.

### Reaching the lab from the host (plain QEMU)

The router's WAN NIC forwards:

| Host | Guest | What |
|---|---|---|
| `127.0.0.1:8080` | pfSense `:80` | pfSense web GUI (`lab`/`lab` or `admin`/`lab`) |
| `127.0.0.1:2237` | pfSense `:22` | `vmctl shell pfsense-lab` (the project key is in the pfSense accounts) |
| `127.0.0.1:8081` | pfSense `:8081` → NAT → `192.168.0.10:80` | Pi-hole web UI: `http://127.0.0.1:8081/admin/` |
| `127.0.0.1:2238` | pfSense `:2238` → NAT → `192.168.0.10:22` | `vmctl shell pihole-lab` |
| `127.0.0.1:2239` | pfSense `:2239` → NAT → `192.168.0.100:22` | `vmctl shell lubuntu22-lab` |

The rendered `config.xml` carries the matching WAN pass rules (GUI and SSH to
the WAN address) and the NAT rules; `topology()` refuses a router profile whose
`hostfwd` list does not forward every port the NAT rules need. The members'
`ssh_host_port` is therefore the same number before and after the move to the
LAN: during the install it is a slirp forward on the member itself, afterwards
it is a forward on the router.

### pfSense install (`bootstrap-pfsense`)

See [UNATTENDED.md](UNATTENDED.md#pfsense-scripted-bsdinstall). In short: the
local ISO is copied once per VM into `artifacts/pfsense-lab/pfsense/install.iso`
with `installerconfig`, `rc.local` and a patched `bsdinstall/script` grafted in
by `growisofs -M`; the VM boots BIOS with the disk first and the CD second,
`bsdinstall` installs ZFS on `vtbd0` and drops the rendered `config.xml` in
`/cf/conf`, then the serial console prints `==> pfSense installation complete!`
and the guest powers off.

### Linux members (Ubuntu autoinstall + `network_lab` hook)

`bootstrap-unattended` installs Ubuntu Server 22.04.5 on the install-phase slirp
NIC exactly like every other Ubuntu profile. During the post-install,
`netlab.provision_guest` runs before the profile's own `post_install_run`:

1. asks the guest for the name of the interface with the lab MAC;
2. renders `lan.yaml` (static address, gateway, DNS, search domain), and for
   the Pi-hole `pihole.toml`, `adlists.list`, the web password, then copies them
   to `/tmp/vmctl-netlab`;
3. runs `setup.sh` with sudo: Pi-hole installer `--unattended`, `pihole
   setpassword`, FTL check; then `/etc/netplan/01-vmctl-lab.yaml`, cloud-init
   network disabled, `pihole-FTL --config dhcp.active <enabled>`; on the client
   `systemd-networkd-wait-online` (and networkd) disabled, otherwise every boot
   stalls two minutes once the renderer is NetworkManager (the kvm-lab fix).

The static address applies at the next boot, which is the first runtime boot on
the segment. `vmctl lab install` stops each member after its post-install.

### libvirt road

`vmctl lab export` does it in one go (QEMU lab down, three exports, `virsh start`
in order, then the same checks as `vmctl lab check --libvirt`); `vmctl lab
unexport` shuts the domains down with virsh, waits for `shut off` and removes
the definitions, disks untouched; `vmctl lab libvirt-test` chains the two.
Under the hood `vmctl export-libvirt pfsense-lab` (and the members) renders the runtime NICs:
slirp → libvirt `default`, segment → libvirt network `lab-lan`, defined,
started and set to autostart from `netlab.segment_network_xml()` when libvirt
does not have it yet (bridge `virbr-lab`, host `192.168.0.254/24`, no DHCP/DNS/
NAT). From then on the GUIs are on `http://192.168.0.1/` and
`http://192.168.0.10/admin/`, as in kvm-lab, and the port forwards are simply
not exported.

## Prerequisites

- `xorriso` and `growisofs` (`dvd+rw-tools`), the Python `bcrypt` module
  (`python3-bcrypt` / `python-bcrypt`): all listed by `vmctl setup`.
- The pfSense CE 2.7.2 amd64 DVD ISO (Netgate publishes no stable URL) at
  `isos/pfSense-CE-2.7.2-RELEASE-amd64.iso`, or `"iso"` set in `local.json`.
  The flow checks it is the offline installer (`rc.local` running `bsdinstall
  script`) and refuses other versions rather than guessing.
- Ubuntu 22.04.5 live server ISO: downloaded automatically.
- `virtiofsd` for the client's shared folder.

## Checklist to verify a fresh install

Not verified by the unit tests (they never start a VM); run them once after
`vmctl lab install && vmctl lab up`:

| Check | How |
|---|---|
| pfSense installed and booting from disk | `vmctl attach pfsense-lab`: console menu, WAN with a `10.0.2.x` address, LAN `192.168.0.1` |
| pfSense GUI login | `http://127.0.0.1:8080/` with `lab` and with `admin` (verified live on 2026-09-06: install 3 min, GUI up 20 s after boot, both logins land on the first-time setup wizard, which can be closed; SSH with the project key works) |
| Pi-hole up, DHCP state | `vmctl shell pihole-lab`, then `pihole status` and `pihole-FTL --config dhcp.active` |
| Local DNS | on the client: `resolvectl query pfsense.qlan` → `firewall.qlan` → `192.168.0.1` |
| Public DNS and HTTPS through the firewall | on the client: `curl -sI https://example.org` |
| Client routing | `ip route` shows only the LAN and `default via 192.168.0.1` |
| Client desktop and autologin | `vmctl attach lubuntu22-lab` |
| Shared folder | `~/shared` and the desktop link show `./shared` of the host |
| Boot time of the client | `systemd-analyze` well under a minute (no wait-online stall) |
| Pi-hole web UI through the forward | `http://127.0.0.1:8081/admin/` |

## References

- [libvirt network XML](https://libvirt.org/formatnetwork.html)
- [pfSense config.xml](https://docs.netgate.com/pfsense/en/latest/config/xml-configuration-file.html)
- [VirtIO in pfSense](https://docs.netgate.com/pfsense/en/latest/virtualization/virtio.html)
- [Pi-hole v6 configuration file](https://docs.pi-hole.net/ftldns/configfile/)
- [QEMU socket netdev (mcast)](https://www.qemu.org/docs/master/system/invocation.html#hxtool-5)
