# Working inside the VMs from the command line

How to get into a lab VM without a graphical window: SSH, serial console, VNC screen, and the
typical commands to run inside Linux, Windows and pfSense. It applies to every profile; the
examples use the network lab (`pfsense-lab`, `pihole-lab`, `lubuntu-lab`) and the
unattended Windows VMs.

## 1. The three doors

| Door | Command | When |
|---|---|---|
| **SSH** | `vmctl shell <vm>` | The normal way. Uses the project key (`artifacts/<vm>/ssh/id_ed25519`) and the profile's `ssh_host_port` on `127.0.0.1`: never a password. On Windows it opens `cmd.exe`, on pfSense the FreeBSD shell. |
| **Serial** | `vmctl console <vm>` | The VM runs in the background (`vmctl start <vm> --headless --background` or `vmctl lab up`) and the network does not answer, or you need the pfSense console menu or the boot messages. Attaches the terminal to COM1/ttyS0; `Ctrl-]` detaches. Everything the guest writes on the serial also lands in `artifacts/<vm>/logs/serial.log`. |
| **Screen** | `vmctl attach <vm>` | A desktop or an installer to watch: opens the VNC screen of the headless VM in `remote-viewer` (or `--no-viewer` for the address only). Also during a bootstrap. |

Who answers on the serial:

- **Linux**: a getty on `ttyS0` is needed. The lab profiles (`pihole-lab`, `lubuntu-lab`)
  and `ubuntu-server-ci` enable it; on another VM run once
  `sudo systemctl enable --now serial-getty@ttyS0.service` (through `vmctl shell`).
- **pfSense**: the serial console is enabled by the generated `config.xml`: after the boot the
  numbered menu appears (0 logout, 8 shell, 5 reboot, 6 halt...).
- **Windows**: no shell on COM1 (the first-logon script only writes its log there). The
  Windows command line is SSH, see §3.

To run one command without opening a session, `vmctl shell` takes no arguments: use `ssh`
directly with the same key and port, which is also what the profiles' `post_install_run` does:

```bash
ssh -i artifacts/lubuntu-lab/ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -p 2239 lab@127.0.0.1 'ip -br addr'
```

In the network lab the members' ports 2238/2239 go through the router: `vmctl shell
pihole-lab` works only with `pfsense-lab` up (before the install, instead, they are direct
slirp forwards). On libvirt, after `vmctl lab export`, use the real IP: `ssh lab@192.168.0.100`.

## 2. Linux: typical checks

From the Lubuntu client (`vmctl shell lubuntu-lab`):

```bash
ip -br addr                              # the NIC (enp0s4 on the client, enp0s2 on Pi-hole): 192.168.0.100/24
ip route                                 # default via 192.168.0.1
ping -c 3 192.168.0.1                    # the firewall answers on the segment
ping -c 3 192.168.0.10                   # Pi-hole
resolvectl query pfsense.qlan            # CNAME -> firewall.qlan -> 192.168.0.1 (Pi-hole record)
resolvectl query example.org             # public name: Pi-hole -> OpenDNS through pfSense
curl -sI https://example.org | head -1   # HTTP/2 200: NAT exit through the firewall
systemd-analyze                          # boot well under a minute (wait-online off)
ls ~/shared                              # the host's shared/ folder (virtiofs)
```

From Pi-hole (`vmctl shell pihole-lab`):

```bash
pihole status                            # FTL active, blocking enabled
pihole -t                                # live tail of the queries (Ctrl-C to leave)
sudo pihole-FTL --config dhcp.active     # true: DHCP for the guests of the segment (without sudo the value is unreliable)
dig @127.0.0.1 lubuntu.qlan +short       # 192.168.0.100
sudo cat /etc/pihole/pihole.toml | head  # the configuration rendered by vmctl
```

From the serial (`vmctl console pihole-lab`): login `lab`, then the same commands; useful
while changing the network, when SSH would drop. `Ctrl-]` to return to the host.

## 3. Windows: PowerShell over SSH

`vmctl shell windows11-unattended` opens `cmd.exe` as the profile user (OpenSSH Server
installed at the first logon by the `vmctl-setup.ps1` script, project key in
`administrators_authorized_keys`). From there:

```bat
powershell                                   :: interactive PowerShell
powershell -NoProfile -Command "Get-ComputerInfo | Select OsName, OsVersion"
powershell -NoProfile -Command "Test-NetConnection 192.168.0.1 -Port 80"
powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 | ft IPAddress, InterfaceAlias"
type C:\vmctl\setup.log                      :: the first-logon log, the same one written to COM1
```

One command without a session, with the same `ssh` as above (port 2235 for Windows 11, 2236
for Windows 10):

```bash
ssh -i artifacts/windows11-unattended/ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -p 2235 lab@127.0.0.1 'powershell -NoProfile -Command "Get-Date; hostname"'
```

A whole script: copy it with `scp` (same key, capital `-P`) into `C:/Users/<user>/` and run
it with `powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\<user>\x.ps1`. The
`post_install_run` of the Windows profiles does exactly this, one command at a time, after
the install; write PowerShell rather than `findstr` on localised output.

To put a Windows VM on the lab segment: `vmctl lab attach windows11-unattended --apply`, then
in PowerShell `Get-NetIPConfiguration` shows the address taken over DHCP from Pi-hole (pool
`.150-.199`). Mind that with the NIC on the segment the SSH port 2235 no longer exists (no
slirp); you get back in through `vmctl attach` or, on libvirt, by IP.

## 4. pfSense: console and shell

```bash
vmctl console pfsense-lab                # console menu (root): 8) Shell, then Ctrl-] to leave
vmctl shell pfsense-lab                  # FreeBSD shell over SSH (port 2237) as the profile user
ssh -i artifacts/pfsense-lab/ssh/id_ed25519 -p 2237 admin@127.0.0.1   # as admin (uid 0): needed for pfctl
```

`pfctl` reads `/dev/pf` and wants root: the profile user has a shell but no privileges
(pfSense has no sudo), so either log in as `admin` with the same key, or use the console
menu. In the shell:

```sh
ifconfig vtnet0 inet                     # WAN (slirp 10.0.2.15 on plain QEMU, 192.168.122.x on libvirt)
ifconfig vtnet1 inet                     # LAN 192.168.0.1
pfctl -sr | head -20                     # active rules (LAN allow, WAN GUI/SSH, forwards)
pfctl -sn                                # NAT: automatic outbound + the port forwards to the members
pfctl -ss | grep 192.168.0.100           # states opened by the client
ping -c 3 192.168.0.10                   # the router sees Pi-hole
drill lubuntu.qlan @192.168.0.10         # the system DNS is Pi-hole
cat /cf/conf/config.xml | head -40       # the configuration written by the installer
```

The GUI is on `http://127.0.0.1:8080/` (plain QEMU) or `http://192.168.0.1/` (libvirt),
profile user or `admin`, same password.

## 5. From the host, without entering

```bash
vmctl lab status                         # role, IP, disk, state of every member
vmctl lab check                          # HTTP and TCP probes: pfSense GUI, Pi-hole GUI, SSH ports
vmctl lab install [--export]             # the whole lab from scratch (--export ends in libvirt); lab clean removes it
vmctl lab export · unexport · libvirt-test   # libvirt road: the same probes on the real IPs
tail -f artifacts/pfsense-lab/logs/serial.log       # the serial of a background VM
curl -s http://127.0.0.1:8081/admin/ | head -3      # Pi-hole through the router forward
```

The bootstrap logs live in `artifacts/<vm>/logs/` (`bootstrap-serial.log` is the installer's
serial, `post-install.stdout.log` the SSH commands of the post-install).
