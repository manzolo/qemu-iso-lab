# Proxmox VE lab: a three-node cluster and a container that survives its node

Three Proxmox VE 9.2 nodes, each on a ZFS mirror of two disks, clustered over an isolated segment,
and a Debian Xfce client with a browser on the same segment. Node 1 runs two LXC containers, IT-Tools
and Glance. The exercises read the cluster, replicate a container's disk to the other nodes, put it
under high availability and move it by hand; then node 1 loses its power, and the container starts
again on another node, with the same address, while the client keeps asking for its page.

| VM | Lab address | From the host |
|---|---|---|
| `proxmox-ve` (node 1, CT 200 IT-Tools, CT 201 Glance) | `10.10.10.2` | `vmctl shell proxmox-ve` (2276), GUI https://127.0.0.1:8006 |
| `proxmox-ve-node2` | `10.10.10.3` | `vmctl shell proxmox-ve-node2` (2278), GUI https://127.0.0.1:8007 |
| `proxmox-ve-node3` | `10.10.10.4` | `vmctl shell proxmox-ve-node3` (2279), GUI https://127.0.0.1:8008 |
| `proxmox-lab-client` | `10.10.10.10` | `vmctl shell proxmox-lab-client` (2277) or its console |

The GUI login is `root` / `lab`; the containers answer on the segment at http://10.10.10.20/ (IT-Tools)
and http://10.10.10.21:8080/ (Glance). Every node needs 3 GB of RAM: the stack wants about 12 GB.

## Start

```sh
vmctl group install proxmox-lab   # three automated Proxmox installs, the client, then the cluster: about 40 minutes
vmctl group status proxmox-lab
vmctl shell proxmox-ve
```

## Exercise 1: the cluster

Corosync keeps the nodes in one cluster over the lab segment, and `/etc/pve` is the same file system
on every node (pmxcfs). A cluster acts only while a majority of its nodes, the *quorum*, see each
other: two of three is enough. The GUI of any node manages all three.

```sh
pvecm status | sed -n '/^Name/p;/^Nodes/p;/^Quorate/p'
pvecm nodes
grep ring0_addr /etc/pve/corosync.conf
ls /etc/pve/nodes
```

## Exercise 2: the containers

An LXC container shares the node's kernel: it starts in a second and needs a few hundred MB. Both
containers run on node 1, their disks are ZFS datasets on `local-zfs`, and each has a second
interface on the lab segment, where the client reaches it.

```sh
pct list
pct config 200 | grep -E '^(hostname|memory|net1|rootfs)'
zfs list -o name,used,refer | grep subvol
pct exec 200 -- ip -4 -o addr show eth1
```

On the client: `curl -s http://10.10.10.20/ | grep -o '<title>.*</title>'`. On node 2,
`zfs list | grep subvol` finds nothing: a container on local storage exists on one node only.

## Exercise 3: ZFS replication

A replication job sends the container's dataset to another node with `zfs send`: whole the first time,
then every minute only what changed since the last snapshot. A copy at most a minute old is what lets
another node start the container when its own node is gone.

```sh
pvesr create-local-job 200-0 proxmox-ve-node2 --schedule '*/1'
pvesr create-local-job 200-1 proxmox-ve-node3 --schedule '*/1'
pvesr schedule-now 200-0; pvesr schedule-now 200-1; sleep 15
pvesr status
```

On node 2: `zfs list -t all -o name,used | grep 200` shows the dataset and its `__replicate_`
snapshot, a copy, not a running container (`pct list` is empty there).

## Exercise 4: high availability, a planned move

`ha-manager` looks after the resources it is given. The cluster elects one CRM *master*; every node's
LRM starts and stops what it is told and arms a watchdog. Moving a container is a restart migration:
it stops on node 1, the last changes are replicated, and it starts on node 2 with the same
configuration and address.

```sh
ha-manager add ct:200 --state started --max_relocate 1 --max_restart 1
sleep 20; ha-manager status
ha-manager migrate ct:200 proxmox-ve-node2
sleep 40; ha-manager status | grep ct:200
```

The client gets the same page from 10.10.10.20, now served by node 2. On node 2, `pvesr status` shows
that the job toward node 2 now points at node 1: replication follows the container. Bring it home:

```sh
ha-manager migrate ct:200 proxmox-ve
```

## Exercise 5: a node dies, the container moves

On the client, leave a loop asking for the page every five seconds:

```sh
while true; do printf '%s ' $(date +%T); curl -s -m 2 http://10.10.10.20/ | grep -o '<title>IT Tools' || echo 'no answer'; sleep 5; done
```

On the host, pull the plug on node 1 (a power cut, not a shutdown), and watch from node 2:

```sh
vmctl stop proxmox-ve --force
vmctl shell proxmox-ve-node2 -- "watch -n 3 'ha-manager status'"
```

The two surviving nodes keep the quorum. After about a minute the master marks the service `fence`;
when the dead node's lock expires (two minutes: by then its watchdog would have reset it, had it only
been cut off from the others) the container starts on node 2 or node 3 from the last replica, and the
client's loop finds the page again: about two and a half minutes in all. Whatever changed after the
last sync is lost: that is the price of local storage. Glance, not under HA, stays down until node 1
returns.

```sh
vmctl group up proxmox-lab          # node 1 comes back and rejoins; the container stays where it is
vmctl shell proxmox-ve-node2 -- pvesr status
vmctl shell proxmox-ve-node2 -- "pvesr schedule-now 200-0; sleep 15; pvesr status"
vmctl shell proxmox-ve-node2 -- ha-manager migrate ct:200 proxmox-ve
```

The job toward node 1 failed while node 1 was off, and a failed job waits a few minutes before it
tries again: run it by hand before moving the container back. A migration that finds an old copy
there instead copies the whole disk under a new name (`subvol-200-disk-1`) and leaves the old one.

To try: `ha-manager crm-command node-maintenance enable proxmox-ve` moves the resources away
before a planned maintenance (`disable` gives the node back). Back to the lab as installed:

```sh
ha-manager remove ct:200; for j in 200-0 200-1; do pvesr delete $j; done
```

## Tests

```sh
vmctl group test proxmox-lab     # five scripts, one per exercise; the fifth powers node 1 off and on again (about 8 minutes)
```

Each test leaves the cluster as it found it: CT 200 on node 1, out of HA, no replication job.
