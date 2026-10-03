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
vmctl group clean proxmox-lab           # delete the members' disks (asks first; ISOs are kept)
```

## The network map

`vmctl group map <lab>` writes one self-contained HTML page under `artifacts/labs/<lab>/`: the
host, the NAT forwards on 127.0.0.1, every member with its state and the segment with its
addresses. Below the drawing, an **Access** table lists every web GUI, SSH command and login, and
a **runbook** walks through the lab step by step (each block marked *run*, *check* or *try*).

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

Create a private lab without changing any tracked file. For example, three Ubuntu cloud
images for Linux firewall exercises, a server and a client:

```bash
vmctl group new my-firewall --title "My Linux firewall lab" \
  --member router=ubuntu-cloud-base --member server=ubuntu-cloud-base \
  --member client=ubuntu-cloud-base --dry-run
# Review the resource estimate, then repeat without --dry-run to save it.
vmctl group new my-firewall --title "My Linux firewall lab" \
  --member router=ubuntu-cloud-base --member server=ubuntu-cloud-base \
  --member client=ubuntu-cloud-base
vmctl group guide my-firewall --lang en
vmctl group install my-firewall
vmctl group test my-firewall
vmctl group map my-firewall --open
vmctl group down my-firewall
```

`new` accepts 2–254 distinct `--member role=base-or-profile` entries. Names use lowercase
letters, digits and hyphens; each resulting `<lab>-<role>` name must fit in 64 characters.
The profiles go into **`vms/profiles/local.json`**, and the teaching material goes into
**`vms/labs.local/<lab>/`**; both are gitignored. The new directory contains `lab.json`
(title, summary, members and do/check/try exercises), `guide.en.md`, `guide.it.md` and
`tests/test_01_reachability.sh`. The test sources the tracked `vms/labs/_common.sh` and pings
every peer from every member through `vmctl shell`.

The scaffold chooses the first free `172.20.N.0/24`, with N from 1 through 255, considering
tracked and local profiles, including overlapping networks. Members receive `.1`, `.2`, …
in command-line order on a segment named `user-lab-N`. Existing networks are unchanged.
SSH ports use clone's allocator from 2300 upwards, skipping configured ports. The preview
reports total RAM, vCPUs and virtual disk capacity (including extra disks, excluding cached
media), and warns about media you must supply yourself, using the same source detection as
the rest of vmctl. Nothing is downloaded or started by `new`.

Simple bases such as `ubuntu-cloud-base` remain an `extends` reference. An existing profile,
or a base with its own topology, becomes a private snapshot base `<lab>-<role>-base` in
`local.json`: this preserves provisioning while replacing inherited groups, NICs and mutable
artifact paths. Its original group/cluster membership is discarded. Review inherited
provisioning scripts before installation: embedded assumptions about the source lab are
still yours to adapt. Local-only profiles inherit their recipe's login and locale; customize
these in their local entries if needed (the top-level identity applies to tracked profiles).

Cloud images configure the private NIC automatically during installation. Other guests
need their own address configuration and may need adapted reachability commands (for example
Windows ping syntax). Each member also has an independent NAT connection for SSH and internet
access. The scaffold supplies a shared segment; making the router a firewall or gateway is an
exercise you add, not an automatic change to the other guests' routes. Use `router` as the
role when that VM should start first.

Edit only `local.json` and `labs.local/<lab>/` to add packages, provisioning, guidance and
exercises. Guides, the web card, the network map and `vmctl check-vms --group my-firewall`
use the same content loading as tracked labs, including the `lab-my-firewall` test row when
all members participate. `--tracked-only` excludes local profiles and local lab content.
Local lab names must not collide with tracked labs/groups; duplicates are errors, never
overrides. Member names in `lab.json` must match the profiles declaring that group.

To remove a lab, first stop it and delete its disks explicitly:

```bash
vmctl group clean my-firewall
vmctl group remove my-firewall --dry-run
vmctl group remove my-firewall
```

`remove` refuses tracked profiles and any member with a primary or extra disk, including an
empty prepared disk. It removes local member profiles, private scaffold bases and the local
content directory; retained checkpoints and cached media stay on disk. It refuses removal if
another profile still depends on a scaffold base. Both commands validate the whole candidate
configuration before atomically replacing `local.json`, preserving other local settings and
saving the previous document as `local.json.bak`. `--dry-run` writes nothing.

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
