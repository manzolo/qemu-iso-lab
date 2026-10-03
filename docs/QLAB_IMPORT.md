# Importing the qlab labs (plan)

Written on 2026-09-27; F1 started on 2026-10-02 (branch `feature/qlab-import`, see the status
under each foundation). It plans how eight of the teaching labs built in
[qlab](https://github.com/manzolo/qlab) (`~/Workspaces/qemu/qlab`, one `qlab-plugin-<name>-lab`
repository each) become labs of this project: vpn, ssh, pxe, pam, mysql, lvm, docker, apache.
The other ideas, which are new labs rather than ports, are in [LAB_IDEAS.md](LAB_IDEAS.md).

## Where we are (2026-10-03) — the backlog

Done, merged and verified live (vmctl 0.17.0 → 0.17.6, 2026-10-02/03; the tour of the catalog site shows vpn-lab):

| step | what | live |
|---|---|---|
| F1 | `bootstrap-cloudimg`: vendor cloud image as a qcow2 overlay + cloud-init seed (`ubuntu-cloud-base`, `ubuntu-24.04-cloud`) | 49 s after the download |
| F2 | `vmctl shell <vm> -- <command>` + `vms/labs/_common.sh` (`on`, `assert*`, `report_results`) | used by every test below |
| F3 | lab content `vms/labs/<lab>/` (lab.json exercises in the map's runbook, guides EN/IT, tests), `vmctl group guide` | — |
| F4 | `vmctl group test <lab>` + the `lab-<name>` row of `check-vms` | — |
| netlab | content for the existing pfSense lab | 2/2, 9 checks |
| vpn-lab | WireGuard + OpenVPN, two members on `vpn-lan` | 5/5, 34 checks |
| ssh-lab | SSH hardening: sshd_config, fail2ban, port knocking, scanning | 6/6, 29 checks |
| docker-lab | Docker Engine + Compose, images pre-pulled | 6/6, 25 checks |
| lvm-lab | PVs, VG, ext4/xfs LVs, online growth, snapshot rollback | 7/7, 43 checks |
| git-lab | a prepared repository: objects, branches, merge, conflicts, stash, remote, rebase, git flow (one cloud image) | 9/9, 60 checks |
| zfs-lab | the ZFS half of qlab's raid-lab, extended: RAIDZ, datasets, snapshots, offline/resilver, scrub, send/receive, mirrors (one cloud image, four disks) | 7/7, 46 checks |
| mdadm-lab | Linux software RAID, the RAID qlab's raid-lab only named: a mirror, a failed disk, a RAID5 with a hot spare, a grow, stop/assemble (one cloud image, four disks) | 6/6, 42 checks |
| web | lab card: Run tests, Guide (page), Consoles (`/multi` headed by the lab, addresses per pane), restyled card | browser test |

Next, in this order:

1. **apache-lab** and **mysql-lab** (phase 2, one server each: content + tests on `ubuntu-cloud-base`).
2. **F5 group checkpoints** (`vmctl group checkpoint create|restore`, *Reset lab* on the card; lvm-lab and
   the hardening labs benefit most). Note: checkpoints refuse `extra_disks` today, so lvm-lab needs that
   lifted (copy every disk) or a lab-specific reset.
3. **pam-lab** (three VMs with OpenLDAP; never lock out the key-based sudo vmctl needs).
4. **F6 network boot + pxe-lab** (the last foundation, the biggest).
5. Then the other qlab plugins (dns, dhcp, firewall, nginx, postgres, raid, samba, systemd, git...):
   most are one-server labs that fit the docker-lab/lvm-lab pattern unchanged.

Open points found on the way: Debian's genericcloud image publishes SHA512SUMS only (needs an
`iso_sha512` field before a Debian member); a guide's relative links (`../../docs/...`) render as
text on the guide page. The lab rows in the matrix: the first full run (2026-10-03, 171 PASS) had
every member pass but all five `lab-<name>` rows fail on "Disk image not found", because the row
cleanup removed each member's disk before the lab tests; fixed in 0.17.4 (a passing member waits
for its lab's tests) and verified with vpn/ssh/docker/lvm (10/10), netlab's row still to see in
the next full matrix. `cluster-pve-lab` passed (3 nodes, quorate).

Found by the netlab lesson (2026-10-03): the exercise "DNS and DHCP from Pi-hole" promises the fixed
leases of the members in `pihole-FTL --config dhcp.hosts`, but it prints `[]` on a fresh install
(the members' addresses are static, netplan): either seed the leases in the provisioning or drop the
claim; `pihole status` needs sudo to read pihole.toml without a warning.

Lessons worth keeping (each cost a run): tests must not depend on the guest's locale (LVM printed
`5,99g` under it_IT); never address extra disks by name (the system disk comes last on the PCI
bus); a bootstrap handler under test needs `resolve_efi_firmware` mocked (CI has no OVMF, CI was
red on 0.17.0-0.17.1 for it); OpenVPN 2.6 on OpenSSL 3 has no BF-CBC; a knock-gated port makes
fail2ban blind unless the test knocks first.

## What qlab has, what we have

A qlab plugin is a bash `run.sh` that downloads the Ubuntu 22.04 *minimal cloud image*, writes a
cloud-init `user-data` per VM (user `labuser`/`labpass`, packages, a netplan for the internal LAN),
boots the VMs on a QEMU multicast socket (`192.168.100.0/24`, MACs `52:54:00:00:0X:0Y`) and
forwards SSH. Each plugin also carries a didactic `guide.md`, an IT/EN walkthrough rendered to PDF
(`docs/walkthrough.{it,en}.md` + `walkthrough.yaml`) and `tests/test_NN_*.sh`, host-side checks
over SSH (`_common.sh`: `assert`, `assert_contains`, `ssh_server`, `ssh_client`, a cleanup per
lab, `report_results`). The cloud image makes a VM ready in about a minute.

This project already has most of the machinery a lab needs:

| qlab | here | state |
|---|---|---|
| multicast LAN between VMs | `networks[]` with `type: segment` (runtime phase), addresses by MAC in the post-install | done (netlab, proxmox-lab) |
| `qlab run <lab>` | `vmctl group install/up/down/status <group>` (start order by role) | done |
| per-VM cloud-init | `cloud_init` seed + `ssh_provision` / `post_install_run` | done, but only after a full install |
| the VM image | an unattended install from the ISO (8-15 min per member) | **gap: no cloud-image flow** |
| `qlab shell <vm>` | `vmctl shell <vm> [-- command]`, web SSH terminal | done (F2, 2026-10-02) |
| `qlab test <lab>` | `vmctl group test <lab>` + the `lab-<name>` row of `check-vms` | done (F4, 2026-10-02) |
| guide.md, walkthrough PDF | `vmctl group map` (map + do/check/try runbook), `make guides`, `profiledoc` | runbook is code in `labs.py`, not data |
| extra disks (lvm) | `extra_disks` (`qemu.extra_disks`) | done (Proxmox ZFS mirror) |
| a clean restart of an exercise | `vmctl checkpoint create/restore` per VM | done per VM, not per group |
| PXE client without a disk | nothing | **gap: no network boot** |

So the port is mostly **content** (profiles, provisioning scripts, guides, tests) on top of five
foundations, one of which (network boot) only the pxe lab needs.

## Phase 0: foundations

Build these once, before the first lab, each with its unit tests and a live run.

### F1. A cloud-image flow (`bootstrap-cloudimg`)

**Status (2026-10-02): done, live PASS** (49 s after the download), `vmctl/cloudimg.py` + `ubuntu-24.04-cloud` (`vms/profiles/cloud.json`),
documented in [UNATTENDED.md](UNATTENDED.md#cloud-images-bootstrap-cloudimg). What changed from the
plan below: the image is named with the ISO fields (`iso`, `iso_url`, `iso_sha256_url`) instead of
a `cloud_image.url` block, so the download, the cache, the catalog site's "public download" and
every ISO test work unchanged, and the flow's own section is `cloudimg_config` like the other
flows; the base of the overlay is a hard link by content under `isos/.cloudimg/`, because
`ensure_iso` deletes a cached file the vendor's SUMS no longer matches; later boots have no seed,
so `provision.sh` disables cloud-init for them. Debian waits for a `sha512` field
(`genericcloud` publishes `SHA512SUMS` only).


The lab members are small Ubuntu/Debian servers; installing each from the ISO costs 8-15 minutes
and a full lab would take longer than qlab's whole session. A profile gets a `cloud_image` block:

```json
"cloud_image": {
  "url": "https://cloud-images.ubuntu.com/minimal/releases/noble/release/ubuntu-24.04-minimal-cloudimg-amd64.img",
  "sha256_url": "https://cloud-images.ubuntu.com/minimal/releases/noble/release/SHA256SUMS",
  "format": "qcow2"
}
```

- `iso.py` downloads and validates it like an ISO (cached under `isos/`, checksum from the
  vendor's `SHA256SUMS`, never a local hash); the disk is a **qcow2 overlay** on the cached image
  (`qemu-img create -b ... -F qcow2`, then `resize` to `disk.size`), so ten members cost the
  image once plus their own writes. `clean`, `checkpoint` and `clone` must then handle a backing
  file: checkpoint and clone `qemu-img convert` into a standalone image (they already convert),
  `clean` removes only the overlay, `export-libvirt` refuses or flattens.
- The seed is the existing `cloud_init` renderer (user, password hash, project key, packages,
  `runcmd`, the netplan for the segment written by MAC), plus the completion token on ttyS0 at the
  end of `runcmd` (sync → token; cloud-init itself does not power off, the flow then continues
  with the post-install over SSH, like `bootstrap-unattended` after its installer).
- `local_test_mode` maps it to `bootstrap-cloudimg`, so `check-vms` and the matrix cover it, and
  the catalog site shows it as a "public download". A new `vmctl list` flow, no new status.
- Debian has the same thing (`debian-13-genericcloud-amd64.qcow2`), so the flow is not
  Ubuntu-only. Minimal images lack some tools the guides use (`nano`, `ping`, `tcpdump`): they go
  in `packages`, as in qlab.
- Estimate: one module (`cloudimg.py`, ~250 lines) + tests + docs; the first live run on a
  single `ubuntu-24.04-cloud` profile, which is useful on its own as the fastest VM of the catalog.

### F2. Commands inside a member: `vmctl shell <vm> -- <command>`

2026-10-03: one argument is a command line for the guest's shell, several are an argv quoted word by word
(`shlex.join`): joined with plain spaces, `sh -c 'echo x > f'` lost its quotes (docker-lab).

**Status (2026-10-02): done.** `lifecycle.cmd_shell` + `ssh.ssh_command_cmd` (BatchMode, LogLevel=ERROR,
the command's exit status returned), `vms/labs/_common.sh` (`on`, `assert`, `assert_fail`,
`assert_contains`, `assert_not_contains`, `report_results`; `VMCTL` overrides the binary),
`tests/test_lab_common.py` runs it against a stub.

The tests and the runbooks run commands in the guests. `vmctl shell` gets an optional command
(`ssh.ssh_shell_cmd` + the command, exit status passed through, `BatchMode=yes`, never a password
prompt). qlab's `_common.sh` becomes `vms/labs/_common.sh` with `on <vm> <command>` built on it
instead of `ssh_server` / `ssh_client` with hard-coded ports.

### F3. Lab content as data: `vms/labs/<lab>/`

**Status (2026-10-02): done.** `labs.load_content`/`content_groups` (validated `lab.json`, guides, tests,
provision), the exercises in `labs.runbook` before *Run the stack* and on the map page, `vmctl group
guide`, `lab_groups` counting a group with content as a lab, `profile_versions.referenced_files`
fingerprinting `vms/labs/` sources, `tests/test_lab_content.py`. First content: `vms/labs/netlab/`
(two exercises, two tests, both guides). Docs: [LABS.md](LABS.md#lab-content-vmslabslab).

Today the runbook of `vmctl group map` is Python in `labs.runbook()`, written for netlab and the
Proxmox cluster. An imported lab brings its own content, in a directory next to the profiles
(outside `vms/profiles/`, or `load_config` would parse it):

```
vms/labs/vpn-lab/
├── lab.json          # title, members (must match meta.groups), exercises, runbook phases
├── guide.en.md       # the qlab guide.md, rewritten for vmctl commands
├── guide.it.md
├── provision/        # scripts copied to the members by the post-install (copy_from_host)
└── tests/
    ├── test_01_anatomy.sh
    └── ...
```

`lab.json` holds, per exercise, `title`, `text`, and do/check/try blocks with the member each runs
on, the same shape `labs.runbook()` returns, so `render_html()` draws it unchanged: the map page
becomes the lab's front page (topology, access table, exercises). `labs.runbook()` stays for the
two existing labs and appends the declared phases. `profile_versions` fingerprints the files of a
lab directory with the members that use it (a guide edit is not a recipe change; a provision
script is), and `tests/test_repo_profiles.py` checks that every lab directory names existing
members and every member of a lab has its directory.

### F4. `vmctl group test <lab>` and a matrix row

**Status (2026-10-02): done.** `labs.run_lab_tests`/`tests_summary`, `vmctl group test <lab> [--json]`
(stack up + SSH wait first, exit 1 on a failed check, a script error told from a failed check),
`lifecycle.run_lab_test_rows` after the cluster checks of `check-vms` (`lab-<name>` row, SKIP when a
member did not pass), the web lab card's *Run tests* and *Guide* buttons; `tests/test_lab_content.py`.

Runs `tests/test_*.sh` of the lab in order against the running stack (starting it if needed,
like `group cluster`), prints `[PASS]/[FAIL]` per check and a summary, exits non-zero on any
failure; `--json` for the web and the report. `check-vms` runs it after the rows when every member
of a lab is in the run, as `run_cluster_checks` does today: a `lab-<name>` row, PASS/FAIL, SKIP
when a member failed, never demoted for lacking a screenshot. The qlab tests are written to be
re-runnable (each ends with its cleanup): keep that rule, a test that leaves state behind breaks
the next run of the matrix.

### F5. A clean start for a whole lab: group checkpoints

Students break things on purpose (sshd, PAM, iptables, LVM). `vmctl group checkpoint create|restore
<lab> [name]` loops `checkpoint.create/restore` over the members (stack stopped first), and
`group install` creates a `fresh` group checkpoint at the end. The web lab card gets *Reset lab*.
No new storage format: each member keeps its own `artifacts/<vm>/checkpoints/<name>/`.

### Web and TUI

The lab card shows *Guide* (the rendered guide.md), *Run tests* (a job running `group test`, the
log console-style like every job), *Reset lab* and the exercises of `lab.json` as an expandable
list with the commands to copy. The Textual Labs filter shows the same entries. No new API: all
of it goes through `/api/run` with `group` subcommands.

## Phase 1..3: the labs

Common choices for all eight, unless a lab says otherwise:

- **Members** on `bootstrap-cloudimg` with Ubuntu 24.04 minimal (qlab used 22.04; 24.04 is the
  current LTS and its netplan and packages behave the same for these exercises), user `lab`/`lab`
  like every tracked profile (qlab's `labuser`/`labpass` is renamed in the guides), 1 GB RAM and
  1 vCPU unless noted, `meta.groups: ["<lab>"]`, `meta.role` server/client, status `unattended`.
- **Names**: `<lab>-server`, `<lab>-client`, ... (qlab's names, which already follow the
  `-server`/`-client`/`-lab` variants of the naming rules).
- **Segment**: one per lab, `<lab>` as the segment name, `172.20.<n>.0/24` with the server on `.1`
  and clients from `.2`: outside netlab's `192.168.0.0/24`, the Proxmox lab's `10.10.10.0/24`,
  the session links' `192.168.100-254.x` and the tunnel ranges the vpn guide uses inside the VMs
  (`10.10.0.0/24`, `10.20.0.0/24`).
- **SSH ports** from 2282 upwards, one per member, in the table of CLAUDE.md
  (`test_repo_profiles.py` refuses duplicates).
- **Guides**: qlab's guide.md is the source; rewrite `qlab shell` → `vmctl shell`, `qlab test` →
  `vmctl group test`, addresses and user, keep the didactic tone; the IT/EN walkthrough PDFs come
  from `make guides` like the other guides.
- **Tests**: qlab's `tests/` are ported file by file onto `_common.sh` (F2); each check keeps its
  description, so the report reads the same.

### Phase 1: two-VM network labs (after F1-F4)

**vpn-lab** (first: its network is the one qlab itself used as the model for every other plugin)

**Status (2026-10-02): ported and live, `vmctl group test vpn-lab` 5/5 (34 checks).** `vms/profiles/vpn-lab.json` (two members on `ubuntu-cloud-base`, the base of
`cloud.json`; the seed writes the segment's netplan by MAC, `cloudimg.segment_netplan`), `vms/labs/vpn-lab/`
(four exercises, five tests, both guides). Addresses 172.20.1.1/.2, tunnels 10.10.0.0/24 and 10.20.0.0/24.

- Members: `vpn-lab-server` 172.20.1.1, `vpn-lab-client` 172.20.1.2 (1 GB each). Packages:
  `wireguard wireguard-tools openvpn iptables tcpdump`.
- Exercises: WireGuard (key pairs, `wg0.conf` both ends, tunnel 10.10.0.0/24), OpenVPN static key
  (tunnel 10.20.0.0/24), traffic analysis (tcpdump: ciphertext on the segment NIC, plaintext in
  `wg0`/`tun0`), iptables allowing only VPN + SSH.
- Tests: 5 (`test_01_vpn_anatomy` .. `test_05_network`), cleanup `wg-quick down wg0` on both.
- New on the map: the tunnel ranges drawn as overlays of the segment (optional).

**ssh-lab**

**Status (2026-10-02): ported and live, `vmctl group test ssh-lab` 6/6 (29 checks).** `vms/profiles/ssh-lab.json` (two members on `ubuntu-cloud-base`; the
server's seed writes the fail2ban jail with `ignoreip` the NAT side, the knockd config and the
`KNOCKD_SSH` chain as a boot unit bound to `ssh-lan`), `vms/labs/ssh-lab/` (six exercises, six tests,
both guides). Addresses 172.20.2.1/.2. The two cautions of the plan are built in: the project key
stays in `authorized_keys` and `PubkeyAuthentication` on, and every test restores/unbans/closes.

- Members: `ssh-lab-server` (the target), `ssh-lab-client` (the attacker), 1 GB each. Packages:
  `openssh-server fail2ban knockd nmap hydra` (hydra on the client only).
- Exercises: anatomy, key auth and password login off, `sshd_config` hardening, fail2ban (trigger
  a ban from the client, unban), port knocking with knockd, reading auth logs and scanning.
- Tests: 6. Two cautions: the hardening exercise must never lock out vmctl's own key (the project
  key stays in `authorized_keys`, tests use it), and the fail2ban test must `unban` the client at
  the end or the next check cannot connect; `ignoreip` keeps the host's NAT side (10.0.2.2) exempt.

### Phase 2: single-VM service labs (only F1-F2 needed, tests via F4)

**apache-lab**: one server (1 GB), `apache2`, sample sites under `provision/`; exercises: anatomy,
serving content, self-signed HTTPS, name-based virtual hosts (the client is the host itself via a
forwarded port and `curl --resolve`, or a second small client on the segment), `.htaccess` and
rewrite, logs. Forwards 8083/8443 → 80/443 for the host browser. Tests: 6.

**mysql-lab**: one server (2 GB), `mysql-server`, a sample database seeded by the post-install
(qlab's SQL); exercises: anatomy, queries and joins, DML, users and GRANT/REVOKE, `mysqldump`
backup and restore, security (bind-address, auth plugins, logging). Tests: 6. PostgreSQL
(`qlab-plugin-postgres-lab`) is the same shape for a later lab.

**docker-lab** — **ported 2026-10-03, live 6/6 (25 checks)**: `vms/profiles/docker-lab.json`, `vms/labs/docker-lab/`; images pre-pulled by the post-install, exercises with `--pull never`. Plan: one server (2 GB, 2 vCPUs, disk 20 GB), `docker.io docker-compose-v2`, user in the
`docker` group; exercises: anatomy, images and containers, exec/logs/attach, volumes and bind
mounts, a Compose app in `~/compose-demo`, building a Dockerfile. Tests: 6. Images are pulled from
Docker Hub during the tests: the matrix row needs network and is the lab most exposed to rate
limits, so the post-install pre-pulls the few images the tests use.

**lvm-lab** — **ported 2026-10-03, live 7/7 (43 checks)**: `vms/profiles/lvm-lab.json`, `vms/labs/lvm-lab/`; the disks found by size (the extra disks are vda-vdc, the system disk vdd), LVM numbers read with `LC_ALL=C`. Plan: one server (1 GB) with `extra_disks` (three 2 GB qcow2 disks, sacrificial by
design); exercises: anatomy, PVs, VGs, LVs with ext4/xfs, online `lvextend --resizefs`,
snapshot and rollback, cleanup. Tests: 7, the last one leaves the disks empty again. This is the
lab where the group checkpoint (F5) matters most. `qlab-plugin-raid-lab` (mdadm on the same kind
of disks) follows naturally.

### Phase 3: the multi-VM and network-boot labs

**pam-lab** (after phase 1; three VMs, 768 MB each)

- Members: `pam-lab-server`, `pam-lab-client`, `pam-lab-ldap` (OpenLDAP with a seeded directory);
  start order LDAP → server → client (`meta.role`: `directory` as infrastructure).
- Exercises: PAM anatomy, `pam_pwquality`, `pam_faillock`, `pam_limits`, `pam_time`,
  `pam_access`, `pam_exec` audit, `pam_google_authenticator` (TOTP secret generated by the test),
  `sssd` against LDAP.
- Tests: 8 (qlab has no test 08 for 2FA; decide whether to write one with `oathtool`).
- Caution: like ssh-lab, no exercise may lock out the `lab` user's key-based sudo that vmctl
  needs for stop, tests and post-install; every PAM change is tested with `pamtester` first, as
  the qlab guide already teaches. qlab's guide is 1381 lines: the longest port.

**pxe-lab** (last; needs F6)

- Members: `pxe-lab-server` (1 GB: `dnsmasq` DHCP+TFTP bound to the segment NIC only, `nginx` for
  the payloads, `samba` for the Windows profile) and `pxe-lab-client`, a profile with an **empty
  disk and no installer of its own** (2 GB for Debian/Ubuntu, 4 GB + UEFI off for Windows).
- **F6, network boot**: a segment NIC gets `"boot": true` (`bootindex` on its `-device`, so
  QEMU's iPXE option ROM boots from it when the disk is empty), and a profile may declare
  `boot_from: "network"` with no `iso`: `local_test_mode` maps it to a new *lab-driven* flow that
  starts the client only after the server passed, waits for the installed system's token on
  ttyS0 (the preseed / autoinstall served by the lab carries it) and then SSH. The vmctl side must
  not serve DHCP: the lab server does, which is the point of the lab (qlab's test 03 proves the
  answer came from dnsmasq and not from SLIRP).
- Profiles of the lab (qlab's `QLAB_PXE_PROFILE`): `debian` (d-i netboot + preseed, the default),
  `ubuntu` (casper + autoinstall over HTTP), `windows` (iPXE + wimboot → WinPE → diskpart / dism /
  bcdboot, BIOS only; needs the user's Windows ISO: `iso_help` like the Windows profiles, and
  qlab's `prepare-windows.sh` becomes a step of the server's provisioning). One `lab.json`
  parameter, `profile`, selects it; the Windows one is `experimental` until it passes live.
- Tests: 6 (dnsmasq confined to the segment, TFTP root, DORA from the client's MAC, bootloader
  over TFTP, client installed and holding a lease, HTTP payload).
- qlab's `pxe-lab-walkthrough/` directory has the screenshots of a full run: reuse them in the
  guide.

## Order, effort, host budget

| step | depends on | RAM of the lab | rough effort |
|---|---|---|---|
| F1 cloud-image flow + `ubuntu-24.04-cloud` | - | 1 GB | 1-2 sessions |
| F2 `vmctl shell -- cmd`, `_common.sh` | - | - | short |
| F3 `vms/labs/` + runbook as data | - | - | 1 session |
| F4 `group test` + matrix row | F2, F3 | - | 1 session |
| vpn-lab | F1-F4 | 2 GB | 1 session |
| ssh-lab | F1-F4 | 2 GB | 1 session |
| F5 group checkpoints + *Reset lab* | - | - | short |
| apache, mysql, docker, lvm | F1-F4 (lvm: F5) | 1-2 GB each | half a session each |
| pam-lab | F1-F5 | 2.3 GB | 1-2 sessions |
| F6 network boot + pxe-lab | F1-F4 | 3-5 GB | 2 sessions (Windows profile extra) |

All eight labs together stay under 20 GB, so the whole set fits a `check-vms` run next to the
rest of the matrix; a lab row costs minutes, not the hour of an ISO install.

## Decisions to take before starting

- **Ubuntu 22.04 or 24.04** for the members (plan: 24.04; the guides need a pass either way for
  vmctl commands and the `lab` user).
- **Where qlab goes afterwards**: archive the plugin repositories once a lab passes here, or keep
  qlab as the lightweight bash variant. The guides and tests are the maintainer's own work in both
  repositories, so moving them is only a matter of copying with history notes.
- **Which of the other qlab plugins follow** (dns, dhcp, firewall, ldap, mail, nginx, postgres,
  raid, samba file sharing, systemd, git, cyber): most are one-VM service labs that fit phase 2
  unchanged once F1-F4 exist.
