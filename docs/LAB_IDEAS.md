# Lab ideas

Written on 2026-09-24, after the Proxmox lab. None of these is started. Each one reuses what the
network lab and the Proxmox lab already proved: members on a shared `segment` NIC in the runtime
phase, `vmctl group install` (cumulative, then down + up), a cross-VM step once the stack runs,
and `vmctl group map` with its Access table and run/check/try runbook
(see [UNATTENDED.md](UNATTENDED.md#groups-as-stacks-and-the-lab-map-vmctl-group)).
Sizes are chosen to fit the 30 GB host next to nothing else.

## Recommended order

1. **k3s cluster**: the natural next step after the Proxmox cluster; it turns the cluster step
   into a mechanism every later lab can use.
2. **Multi-segment routing**: almost no RAM, and the first lab whose map has more than one bus.
3. **Highly available web**: tiny, and a failover you can watch from the client.
4. **Samba Active Directory**: the most instructive and the heaviest.

## 1. Kubernetes with k3s

- **Members**: one k3s server and two agents on minimal Debian (about 2 GB each), plus the Xfce
  client of the Proxmox lab with `kubectl` and a browser on a dashboard (Headlamp).
- **Cross-VM step**: read the node token on the server, join the agents over SSH, wait until
  `kubectl get nodes` lists three `Ready` nodes. The same shape as `pvecluster.form`.
- **Runbook**: `kubectl get nodes -o wide`, a demo Deployment + Service, MetalLB handing out a
  LoadBalancer address on the segment, and a *try* block: stop an agent and watch the pods move.
- **Code**: generalise `pvecluster` into per-lab hooks (a `lab_hook` or `cluster.kind` in the
  profile: `proxmox`, `k3s`, ...) run by `vmctl group install` and `vmctl group cluster`;
  the runbook's cluster phase then comes from the hook as well.

## 2. Routing across several segments

- **Members**: three or four light routers (Alpine or Debian with FRR, 512 MB) on three segments,
  OSPF between them, and two clients at opposite ends.
- **Runbook**: `vtysh -c "show ip ospf neighbor"`, `show ip route`, `traceroute` end to end, and a
  *try* block that takes one link down and shows OSPF converging on the other path.
- **Code**: nothing new in the flows; the map must lay out several buses and members with two or
  three segment NICs (today it draws one bus per segment but was only exercised with one).

## 3. Highly available web

- **Members**: two web servers, two HAProxy nodes with `keepalived` sharing a virtual IP on the
  segment, and the client.
- **Runbook**: `ip addr` on both balancers (who holds the VIP), `curl` through the VIP in a loop,
  and a *try* block: stop the active balancer and watch the VIP move without the client noticing.
- **Code**: profiles and post-install scripts only; the VIP could appear on the map as an address
  of the segment rather than of a member.

## 4. Active Directory with Samba

- **Members**: a Samba AD domain controller on Debian, a Windows 10/11 client joined to the domain,
  an Ubuntu client joined with `realmd`/`sssd`.
- **Runbook**: `samba-tool domain info`, `wbinfo -u`, a domain login on both clients, basic group
  policy, the domain's DNS.
- **Code**: an unattended domain join for Windows (in `vmctl-setup.ps1`, once the DC answers) and
  a cross-VM ordering rule: the DC must be provisioned before the clients join.

## Extensions of the Proxmox lab

- **ZFS replication and HA** (light, recommended): `pvesr` replication of CT 200/201 between nodes
  on `local-zfs`, an HA group, and a *try* block: power off the node that runs IT-Tools and watch
  it restart on another node. Fits the current 3 GB nodes.
- **Ceph variant** (heavy): 6-8 GB per node, a third disk per node for the OSD (not the root
  mirror) and ideally a second segment for the Ceph network; about 24 GB with the client, so only
  on an otherwise idle host. Worth it as a separate `proxmox-lab-ceph` group, not as the default.
