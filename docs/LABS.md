# Labs

A lab is a group of VMs that share a private network segment and work together: a router with
its DNS server and a client, or a three-node Proxmox cluster. Each member is an ordinary profile
with its own unattended install; the lab adds the segment, the start order, the cross-VM step
(the Proxmox cluster) and a network map with how to log in to every member.

| Lab | Members | RAM | Install | Guide |
|---|---|---|---|---|
| `netlab` | pfSense router, Pi-hole DNS, Lubuntu desktop client | ~8 GB | `vmctl group install netlab` | [NETWORK-LAB.md](NETWORK-LAB.md) |
| `proxmox-lab` | three Proxmox VE 9.2 nodes (ZFS mirror, clustered over `pve-lan`), Debian Xfce client with a browser | ~12 GB | `vmctl group install proxmox-lab` | [UNATTENDED.md](UNATTENDED.md#proxmox-ve-bootstrap-proxmox-and-the-proxmox-lab) |

Both are part of the validation matrix: `vmctl check-vms --group netlab` or `--group proxmox-lab`
reinstalls the members from scratch, and for the Proxmox lab also forms the cluster and records it
as one more row (`cluster-pve-lab`).

## In the dashboard

`vmtui`, then **Labs** (or F2): each lab with its members and their state. Enter runs the
suggested step: install what is missing, start the stack, or open its map.

![vmtui, Labs filter: the network lab and the Proxmox lab ready to install, and a session lab of two linked machines](screenshots/vmtui-labs.png)

## From the command line

```bash
vmctl group list --labs                 # the labs and their members
vmctl group install proxmox-lab         # install what is missing, start the stack, form the cluster
vmctl group status proxmox-lab          # running / installed, addresses on the segment
vmctl group map proxmox-lab --open      # the network map below, with the Access table and a runbook
vmctl group guide netlab --lang it      # the lab's walkthrough (vms/labs/netlab/guide.it.md)
vmctl group test netlab                 # the lab's tests/test_NN_*.sh over the running stack (started if needed); exit 1 on a failed check
vmctl group down proxmox-lab            # stop every member, in reverse start order
vmctl group reset k8s-lab               # every member back to its reset point (lab-start), then up again
vmctl group checkpoint k8s-lab          # take the current disks as the new reset point (--name N: another one)
vmctl group clean proxmox-lab           # delete the members' disks (asks first; ISOs are kept)
```

### Reset lab

`vmctl group install` ends every member it installs with a **reset point**: a checkpoint named
`lab-start` (docs/CHECKPOINTS.md), the member exactly as the install left it, before the lab's first
runtime boot. `vmctl group reset <lab>` (the card's **Reset lab…**) stops the stack, puts every
member back to it and starts the stack again: the exercises can be done again from scratch in
minutes instead of a reinstall. All members or none: a member without the checkpoint stops the
reset before anything is touched. `vmctl group checkpoint <lab>` (**Save as reset point…** under
*More lab actions*) makes the current state the reset point instead, for instance after preparing
something the exercises should always start from; `--name` keeps several. A cloud-image member's
reset point is an overlay on its base image, so it costs only what the install changed; extra disks
(lvm, zfs, mdadm, the Proxmox mirror) are part of it.

## The network map

`vmctl group map <lab>` writes one self-contained HTML page under `artifacts/labs/<lab>/`: the
host, the NAT forwards on 127.0.0.1, every member with its state and the segment with its
addresses. Below the drawing, an **Access** table lists every web GUI, SSH command and login, and
a **runbook** walks through the lab step by step (each block marked *run*, *check* or *try*).

The diagram uses device icons and power LEDs; stopped VMs have dim, dashed cables. Running
VMs keep their cables dim while idle. Cables glow only when measured byte counters increase,
with brighter glow and faster pulses for more traffic. In the web UI the map refreshes
automatically, roughly every two seconds. LEDs show VM power, not a connectivity test;
the cable glow, moving pulses and TX/RX byte rates summarize activity during the sample
interval rather than tracing individual packets or proving that the guest received them.

Click the small lens on a LAN cable (or focus it and press Enter) to open the **Packet inspector**;
it stays open until **Close** or Escape (nothing opens on hover), can be dragged by its title bar
and resized from its corner. The list accumulates what the polls bring (up to 500 headers);
the chips filter by protocol (TCP, UDP, ICMP, ARP, other) and direction (TX, RX), the box filters
by any text (an IP, a port, a flag), the footer counts what is shown out of what was seen;
**Pause** freezes the list while the capture goes on, **Clear** starts it afresh. A click on a row opens the packet itself: every decoded header with its fields (Ethernet,
VLAN, ARP, IPv4/IPv6 with TTL, identification, flags and fragment offset, TCP with sequence,
acknowledgment, flags, window and options, UDP, ICMP with id and sequence) and a hex dump of
those header bytes with offsets; the payload is never captured, and the detail says how many
bytes of it were on the wire. The panel shows recent TX/RX packet headers:
source/destination IPs and ports, ICMP echo request/reply with sequence number, TCP flags,
UDP, ARP and IPv6. Unsupported or truncated headers are labelled rather than guessed.
The list is a bounded sample: up to 24 recent entries per NIC from the last 30 seconds,
with at most 100 summaries per second and 128 retained summaries per segment. Payloads
are not retained or sent to the browser. The inspector works on QEMU LAN segments, even
when guest-agent counters supply the byte rates. NAT has no packet capture; its panel
explains the limitation. Closing or hiding a panel does not stop the map's counters.

For QEMU segments the web server passively counts Ethernet frames on the existing loopback
multicast group and attributes them by MAC address. No guest installation or VM restart is
needed. Only the header summaries above are kept in memory, and listeners close after 15 seconds without map
requests. Rates are observed traffic and may undercount if the listener drops frames under
load. When available, QEMU guest-agent interface counters are used instead, including for
NAT NICs. An unmeasurable NIC says **Traffic unavailable**; NAT requires an enabled, running
guest agent with interface statistics. A failed refresh clears the animation and marks the
view stale. Hidden tabs pause polling, and the system's reduced-motion preference disables
the moving pulses. Exported HTML opened as a file remains a static snapshot.

Local checks: `python3 -m unittest discover -s tests -p 'test_lab*.py'`,
`python3 -m unittest discover -s tests -p test_packet_summary.py`,
`python3 tests/lab_traffic_smoke.py` for isolated multicast capture, and
`PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node tests/lab_map_browser.mjs`
for browser behavior (optionally set `PLAYWRIGHT_CHROMIUM_EXECUTABLE`).

![Map of the Proxmox lab: three nodes and the client on pve-lan, forwards for SSH and the web GUIs](screenshots/lab-map-proxmox.png)

![Map of the network lab: pfSense in front, Pi-hole and Lubuntu behind it, forwards through the router's WAN](screenshots/lab-map-netlab.png)

## Lab content: `vms/labs/<lab>/`

A lab is a declared group (`meta.groups`) and, when it has something to teach, a directory next
to the profiles ([QLAB_IMPORT.md](QLAB_IMPORT.md), F3):

```
vms/labs/netlab/
├── lab.json          # title, summary, members (must match the profiles declaring the group), exercises
├── guide.en.md       # the walkthrough, both languages kept in sync
├── guide.it.md
├── provision/        # files the members copy in (copy_from_host; part of their profile fingerprint)
└── tests/
    ├── test_01_dns.sh    # over vms/labs/_common.sh: on <vm> <command>, assert, report_results
    └── test_02_firewall.sh
```

An exercise is `title`, `text` and `blocks`, each `kind` *do* / *check* (read-only) / *try*
(reversible), `where` a member or `host`, and `commands`. `labs.load_content` validates the file
(an unknown member or kind fails loudly), the exercises join the runbook of `vmctl group map`
after what vmctl derives from the profiles and before *Run the stack*, and the map page opens
with the lab's title and summary. A group with content is a lab even without a segment (a
one-server lab). `vmctl group guide <lab> [--lang it]` prints the guide; `vmctl group list --labs
--json` carries `title`, `content`, `guides` and `tests`. The first content is `netlab`'s.

**`vmctl group test <lab>`** runs `tests/test_NN_*.sh` in order against the running stack (a
stopped member is started first, infrastructure first, and SSH is waited for), shows each
script's output as it comes, counts its `[PASS]`/`[FAIL]` lines and ends with one line per script
and one for the lab; the exit status is 1 when a check failed. A script whose exit status is not
its number of failed checks (SSH down, a bash error) is reported as an *error*, not as a failed
check. `--json` prints the same as data (the web's *Run tests* button on the lab card runs it as
a job). On the lab card the buttons run something (Start/Stop stack, Status, Run tests) and the links open a page: **Guide** is `vms/labs/<lab>/guide.<lang>.md` rendered at `/labs/<lab>/guide?lang=en|it` (the browser's language when the lab has it, a link to the other one at the top; `labs.render_markdown` escapes every piece of text and only http(s) links are links), **Map** is the network map and **Consoles** opens every member of the lab side by side on `/multi`
(like *Start in console* on a selection, stopped members started), with the page headed by the
lab, its title in the network bar and each member's address on the lab's segment next to its name. `check-vms` runs the tests too: when every member of a lab with tests was in the run and
passed, the members start on their fresh disks and a `lab-<name>` row records the outcome (SKIP
when a member did not pass), like the `cluster-<name>` row of a Proxmox cluster.

## Your own lab

Create a Linux firewall lab with a router, server and client on Ubuntu cloud images:

```bash
vmctl group new my-firewall --title "My Linux firewall lab" \
  --member router=ubuntu-cloud-base --member server=ubuntu-cloud-base \
  --member client=ubuntu-cloud-base --dry-run
# Review the RAM/CPU/disk estimate, then repeat without --dry-run to create the lab.
vmctl group guide my-firewall --lang en
vmctl group install my-firewall
vmctl group test my-firewall
vmctl group map my-firewall --open
vmctl group down my-firewall
```

`new` writes `<lab>-<role>` profiles in **`vms/profiles/local.json`** and content in
**`vms/labs.local/<lab>/`**, both gitignored. The content includes `lab.json` with exercises,
EN/IT guides and a test that pings every peer. Labs made entirely of cloud images also get
`test_02_internet.sh`: an HTTPS request to `example.org` from each member, checking DNS and
Internet access through NAT. Edit these files to build your exercises; guides, web cards,
maps and `check-vms --group my-firewall` use them just like tracked labs.

Choose a new lab name and 2–254 distinct roles. vmctl assigns the first free `172.20.N.0/24`
(N=1…255), addresses `.1`, `.2`, … and free SSH ports from 2400 up, clear of the catalog's own
numbering (which grows from 2222 as profiles are added). `--dry-run` writes nothing.
A member can extend a base or start from an existing profile: tracked profiles use the
catalog recipe, with `{{user}}` preserved and personal overrides excluded. Adjust each
member's login in `local.json` if needed.

Cloud images configure the private NIC automatically. For other guests, configure its
address and adapt the ping test to the OS; vmctl warns when you must supply an ISO.
Each member has its own NAT connection. Use the `router` role to start that VM first,
then add firewall rules and routing as exercises.

To remove the lab, clean its disks first:

```bash
vmctl group clean my-firewall
vmctl group remove my-firewall
```

`remove` deletes the local profiles, scaffold bases and content directory, keeping cached
media and checkpoints. It refuses while a member still has disks. Both commands preserve
other local settings and save the previous `local.json` as `local.json.bak`.

## Temporary links between VMs (`vmctl link`)

A lab needs profiles that declare a segment. Two machines started on their own, each on its
own slirp network, cannot see each other — until they are linked, running or not:

```bash
vmctl link kali debian-server           # both get a hot-plugged NIC on the "session" segment and a 192.168.100.x address
vmctl link almalinux-server             # one more VM on the same segment
vmctl link                              # who is linked, with the addresses
vmctl link --off                        # unplug everyone (or name the VMs to unplug)
vmctl link a b --segment backend        # a second, separate segment (192.168.<n>.0/24, or --subnet 172.16.5.0/24)
```

In the dashboard, **click a machine's network button** to choose a peer, or **drag that button
onto another machine** (running or stopped). To connect several, select them with Ctrl/Cmd+click
or Shift+click and choose **Link network**. The same
command runs after a confirmation, the machine's panel shows a *Linked* row with the address,
its peers and an *Unlink* button, and the segment appears under **Labs** as a session lab.

A **running** VM gets the NIC at once, hot-plugged. A **stopped** one is recorded as *pending*
("at next start"): `vmctl start` (headless, background or with a window) adds the NIC to its
QEMU command line and a detached `vmctl link --settle` sets the address once SSH answers
(`artifacts/<vm>/logs/link-settle.log`). A member that stops goes back to pending and rejoins at
its next start; only `vmctl link --off` (or *Unlink*) takes it off the segment. Verified live
2026-09-27: debian-server and almalinux-server linked while off, both at `enp0s6` after the boot,
ping 0.6 ms; almalinux stopped and restarted rejoined by itself.

The segment itself is a multicast group on the **loopback interface** (`localaddr=127.0.0.1`
on every `-netdev socket,mcast=…`, since 2026-10-02): the lab's frames never leave the host and a
host firewall does not see them. Before that they went out on the LAN NIC with TTL 1 and came
back through its INPUT chain, where ufw's default deny dropped them (`[UFW BLOCK] … DST=239.x`
in `journalctl -k`): two linked VMs had their addresses and ARP failed both ways. `vmctl link`
now probes that path first and warns when a host still drops multicast on lo. VMs linked
before the change keep talking only among themselves: unlink and link again.

What happens on a running VM: the NIC is added to QEMU over QMP (`netdev_add` on the multicast
socket the labs use, `device_add`) with a MAC that is stable per segment and VM; Linux,
FreeBSD and Windows 10/11 guests with SSH get the address set by vmctl (the interface is found by
its MAC), other guests (Windows 7 and older, hobby systems) are told which address to set.
NetworkManager guests get a manual `vmctl-link-<if>` connection, which DHCP does not flush;
Windows gets a static address in PowerShell, the adapter on the Private profile and an inbound
ping rule (`vmctl-link-icmp`), since it would otherwise stay on a 169.254 address and drop ICMP. Nothing is written to the profiles: the record of a
segment is `artifacts/labs/links/<segment>.json`. Limits: hot-plugging needs a VM running in the
background (*Boot headless*: only those have a QMP socket) and, on q35, the two empty PCIe root
ports of headless boots since vmctl 0.9; a VM that cannot take it is linked at its next start
instead. A NIC a q35 VM booted with cannot be unplugged live: *Unlink* forgets it at once and the
NIC goes with the next stop. For a permanent LAN, declare `networks` in the profiles (Customize).

## Going further

- How groups, start order and maps work: [UNATTENDED.md](UNATTENDED.md#groups-as-stacks-and-the-lab-map-vmctl-group).
- The network lab on libvirt instead of plain QEMU: [NETWORK-LAB.md](NETWORK-LAB.md#libvirt-road).
- The next labs (k3s, multi-segment routing, highly available web, Samba AD): [LAB_IDEAS.md](LAB_IDEAS.md).
- The qlab labs to port (vpn, ssh, pxe, pam, mysql, lvm, docker, apache): [QLAB_IMPORT.md](QLAB_IMPORT.md).
