# Network lab: pfSense, Pi-hole and a client

Three VMs on an isolated LAN (`lab-lan`, 192.168.0.0/24, domain `qlan`):

| VM | Role | Address |
|---|---|---|
| `pfsense-lab` | router and firewall: WAN on the host's NAT, LAN 192.168.0.1 | 192.168.0.1 |
| `pihole-lab` | DNS and DHCP for the LAN, ad blocking | 192.168.0.10 |
| `lubuntu-lab` | the user's desktop | 192.168.0.100 (fixed lease) |

The host reaches the lab only through the router's WAN forwards on 127.0.0.1: pfSense GUI
`http://127.0.0.1:8080/`, Pi-hole `http://127.0.0.1:8081/admin/`, SSH `vmctl shell <vm>`.
How the pieces fit, what the installs do and what was verified live: [docs/NETWORK-LAB.md](../../../docs/NETWORK-LAB.md).

## Start

```bash
vmctl group install netlab        # installs what is missing (pfSense needs its own ISO, see the profile), then starts the stack
vmctl group status netlab
vmctl group map netlab --open     # the map, the access table and the exercises below
```

## Exercises

The exercises are in `lab.json` and on the map page, each as *check* (read-only) or *try*
(reversible) blocks with the member they run on:

1. **DNS and DHCP from Pi-hole** — who answers the client's names, who hands out the leases.
2. **Through the firewall** — the client's only road to the Internet is the router.

Run a command on a member without opening a session: `vmctl shell pihole-lab -- pihole status`.

## Tests

```bash
bash vms/labs/netlab/tests/test_01_dns.sh
bash vms/labs/netlab/tests/test_02_firewall.sh
```

Each script sources `vms/labs/_common.sh` (`on <vm> <command>`, `assert`, `assert_contains`,
`report_results`) and leaves the lab as it found it; the exit status is the number of failed
checks.

## Stop and clean

```bash
vmctl group down netlab
vmctl group clean netlab          # deletes the three disks (asks first); ISOs and checkpoints stay
```
