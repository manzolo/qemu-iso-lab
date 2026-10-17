# PXE lab: a machine with an empty disk installs Debian over the network

A server and a client on an isolated segment. The client has no ISO and an empty disk: its
firmware finds nothing to boot on the disk and asks the network. Everything it needs comes from
the server, one component at a time: an address, a boot loader, a menu, the installer, the
answers to the installer's questions, and a way out to the Debian mirror. Ported from qlab's
pxe-lab: the LAN moved to 172.20.9.0/24 (192.168.100.0/24 is what `vmctl link` uses) and the
server is an Ubuntu 24.04 cloud image.

| VM | Role | Segment `pxe-lan` | SSH from the host |
|---|---|---|---|
| `pxe-lab-server` | dnsmasq (DHCP, DNS, TFTP), nginx, NAT | 172.20.9.1 | `vmctl shell pxe-lab-server` (127.0.0.1:2374) |
| `pxe-lab-client` | BIOS, empty disk, installs Debian 13 over the network | DHCP, 172.20.9.100-200 | `vmctl shell pxe-lab-client` (127.0.0.1:2375) |
| `pxe-lab-client-efi` | the same with UEFI (OVMF): GRUB instead of pxelinux | DHCP, 172.20.9.100-200 | `vmctl shell pxe-lab-client-efi` (127.0.0.1:2376) |

Each VM has two NICs: QEMU's NAT, for the host's SSH, and `pxe-lan`, the segment they share.
The clients netboot from `pxe-lan` only; their installer is told which NIC that was (BOOTIF), so
it never configures the NAT one by mistake. The two clients differ only in their firmware, which
is the point: one network, two boot loaders.

The server's menus name the client they install (the BIOS menu `pxe-lab-client`, the GRUB one
`pxe-lab-client-efi`) and each firmware has its own answers directory: one client per firmware.

## Start

```bash
vmctl group install pxe-lab       # the server (a cloud image, two minutes), then each client over the network (about ten each)
vmctl group status pxe-lab
vmctl group map pxe-lab --open    # the map: live traffic per NIC, the packet inspector, the exercises
```

`group install` installs the server first. A client's install is `vmctl bootstrap-netboot
pxe-lab-client` (or `pxe-lab-client-efi`): it starts the server if it is not running, writes the
client's answers into the server's web root, and boots the client from its `pxe-lan` NIC.

## One boot, step by step

1. **The firmware.** For the install, the NIC is the first boot device. On the BIOS client its
   boot ROM is iPXE (QEMU's network cards all carry it); on the UEFI client it is OVMF's own PXE,
   which takes the road of exercise 7.
2. **DHCP.** iPXE broadcasts a DISCOVER on `pxe-lan`. dnsmasq OFFERs an address from
   172.20.9.100-200 and, because the request carries option 175 (iPXE), a boot file that is an
   `http://` URL: `boot.ipxe`. REQUEST and ACK confirm it.
3. **HTTP, then TFTP.** iPXE fetches `boot.ipxe` from nginx. The script chains `lpxelinux.0` over
   TFTP; pxelinux loads its modules and `pxelinux.cfg/default` over TFTP too, and shows the menu.
4. **The installer.** The menu's default entry loads the Debian installer's kernel and initrd
   over HTTP (the initrd is 40 MB: TFTP would take minutes), with `url=` pointing at the answers,
   `BOOTIF` naming the NIC that netbooted, and the serial console.
5. **The answers.** The installer configures that NIC with DHCP, fetches `preseed.cfg`, and from
   then on asks nothing: language, disk, user, packages, boot loader are all answered.
6. **The mirror.** Packages come from deb.debian.org. The segment has no route out: the server
   forwards and masquerades, and dnsmasq answers the DNS lookups.
7. **The end.** The late script installs the key and sudo rule, syncs, prints the completion
   token on the serial console and the machine powers itself off. `vmctl start` then boots the
   disk.

## Exercise 1: dnsmasq, confined to the lab segment

```bash
cat /etc/dnsmasq.d/pxe-lab.conf
sudo ss -ulpn | grep dnsmasq
```

`interface=pxe-lan` and `bind-dynamic` keep dnsmasq on the segment: DHCP on 67 is bound to
`pxe-lan`, DNS and TFTP to 172.20.9.1. A DHCP server answering on someone else's network is an
outage, which is why this matters more than anything else in the file.

The file also says where DNS goes next: `no-resolv` and `server=127.0.0.53`, the server's own
resolver. Ubuntu's dnsmasq service points it at a resolvconf file that does not exist on this
system; without those two lines every lookup from the client is refused and the installer stops
on "Bad archive mirror", with the network otherwise perfect.

## Exercise 2: what TFTP serves

```bash
ls -l /srv/pxe/tftp /srv/pxe/tftp/pxelinux.cfg
cat /srv/pxe/tftp/pxelinux.cfg/default
```

TFTP has no authentication and moves one small block at a time: it carries the boot loader
(90 KB), its modules and the menu, nothing more. From the client, read the menu the way the
firmware does, then see dnsmasq log the transfer:

```bash
curl -s tftp://172.20.9.1/pxelinux.cfg/default | head -5            # on the client
sudo journalctl -u dnsmasq --since -2min | grep sent                # on the server
```

## Exercise 3: DORA

On the server, follow dnsmasq: `sudo journalctl -u dnsmasq -f`. On the client, release the
segment NIC's address and ask again:

```bash
IF=$(ip -o -4 addr | awk '/172\.20\.9\./ {print $2}'); echo $IF
sudo ifdown $IF; sudo ifup $IF
```

The log shows DHCPDISCOVER, DHCPOFFER, DHCPREQUEST and DHCPACK with the client's MAC, and with
`log-dhcp` every option sent: 1 (netmask), 3 (router), 6 (DNS), 67 (boot file). The lease ends up
in `/var/lib/misc/dnsmasq.leases`.

On the lab map, the lens next to a `pxe-lan` NIC opens the packet inspector: the filter
`port 67 or port 68` shows the same four messages on the wire, with their fields.

## Exercise 4: from the firmware to the installer

```bash
curl -s http://172.20.9.1/boot.ipxe                                 # on the client
curl -sI http://172.20.9.1/debian/initrd.gz | grep -i length
sudo grep -o 'BOOTIF=[^ ]*' /var/log/installer/syslog | head -1
sudo tail -5 /var/log/nginx/pxe-lab.access.log                      # on the server
```

`boot.ipxe` sets option 210, pxelinux's path prefix: without it pxelinux would look for its
modules under the HTTP URL it came from. pxelinux is a BIOS program: an iPXE running on UEFI
firmware chains GRUB instead (exercise 7), and if no boot loader starts at all, the script boots
the installer directly.

## Exercise 5: the answers

```bash
ls -l /srv/pxe/www/debian
grep -E '^d-i (netcfg|mirror|partman-auto)/' /srv/pxe/www/debian/preseed.cfg
```

vmctl writes `preseed.cfg` and `late_command.sh` here before each install: the user and password
hash, the project's SSH key, the completion token the host waits for. The installer fetches the
first with `url=` and the second with `wget` from its late command.

## Exercise 6: the gateway

```bash
sysctl net.ipv4.ip_forward                                          # on the server
sudo iptables -t nat -L POSTROUTING -v -n
```

From the client, bind a request to the segment NIC to take the path the installer took:

```bash
IF=$(ip -o -4 addr | awk '/172\.20\.9\./ {print $2}'); echo $IF
curl -s --interface $IF -o /dev/null -w '%{http_code}\n' http://deb.debian.org/debian/dists/trixie/Release
```

`sudo sysctl -w net.ipv4.ip_forward=0` on the server and the same request hangs; `=1` brings it
back.

## Exercise 7: UEFI, the same network with another boot loader

`pxe-lab-client-efi` boots OVMF. Its firmware's PXE says so in DHCP option 93 (client-arch 7,
x86-64 UEFI), and dnsmasq answers with Debian's shim, `bootnetx64.efi`, instead of pxelinux:

```bash
grep -E 'efi64|dhcp-boot' /etc/dnsmasq.d/pxe-lab.conf                          # on the server
ls -l /srv/pxe/tftp/debian-installer/amd64
cat /srv/pxe/tftp/debian-installer/amd64/grub/grub.cfg
```

The shim asks for `grubx64.efi` at the TFTP root (the server keeps a copy there), GRUB reads its
modules and `grub.cfg` from its built-in prefix, `debian-installer/amd64/grub`, and the menu loads the installer over HTTP: `(http,172.20.9.1)/debian/linux`. GRUB
knows the MAC it netbooted with (`$net_default_mac`, `aa:bb:…`) and `regexp` rewrites it in
d-i's BOOTIF form, `01-aa-bb-…`. The answers come from `debian-efi/`, next to the BIOS client's.

```bash
ls /sys/firmware/efi                                                # on pxe-lab-client-efi: UEFI only
sudo efibootmgr                                                     # the installer's "debian" entry
sudo grep -o 'BOOTIF=[^ ]*' /var/log/installer/syslog | head -1
```

## Exercise 8: install it again

From the host:

```bash
vmctl stop pxe-lab-client
vmctl clean pxe-lab-client
vmctl bootstrap-netboot pxe-lab-client
```

Watch it on the client's console (`vmctl attach pxe-lab-client`, or the map's Console button),
in `journalctl -u dnsmasq -f` on the server, and in the packet inspector with
`port 67 or port 68 or port 69 or port 80`. The same three commands reinstall
`pxe-lab-client-efi`, through GRUB.

## Tests

```bash
vmctl group test pxe-lab
```

Seven scripts under `vms/labs/pxe-lab/tests/`, one per exercise: dnsmasq's confinement, the TFTP
root and a TFTP transfer, a full DORA, the iPXE chain and the installer's kernel line, the
installed client, the gateway, the UEFI client and its GRUB.

## Reset

```bash
vmctl group reset pxe-lab           # every machine back to the lab-start checkpoint
sudo bash ~/setup-pxe-server.sh     # on the server: configuration and files written again
```
