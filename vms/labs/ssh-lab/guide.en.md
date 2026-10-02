# SSH hardening lab: tighten a server, watch it defend itself

Two Ubuntu 24.04 servers on an isolated segment: a server to harden and a client to probe it
from. Everything here is defensive — SSH configuration, fail2ban and port knocking — practised
against a machine that is yours, on a network that reaches nothing else. Ported from qlab's
ssh-lab: `labuser` became the profile's user and the LAN moved to 172.20.2.0/24.

| VM | Role | Segment `ssh-lan` | SSH from the host |
|---|---|---|---|
| `ssh-lab-server` | the target: sshd, fail2ban, knockd | 172.20.2.1 | `vmctl shell ssh-lab-server` (127.0.0.1:2361) |
| `ssh-lab-client` | the probe: ssh-client, nmap, knock, sshpass | 172.20.2.2 | `vmctl shell ssh-lab-client` (127.0.0.1:2362) |

Each VM has a NAT NIC for the host (this is how vmctl reaches them) and `ssh-lan`, the segment the
two VMs share. **fail2ban ignores the NAT network (10.0.2.0/24)** and the port-knock chain gates
only the segment, so nothing you do in this lab can lock vmctl — or you — out of either machine.

## Start

```bash
vmctl group install ssh-lab       # two cloud images, a minute each after the download; then the stack comes up
vmctl group status ssh-lab
vmctl group map ssh-lab --open    # the map, the access table and the exercises
```

Open two terminals: `vmctl shell ssh-lab-server` and `vmctl shell ssh-lab-client`. One command
without a session: `vmctl shell ssh-lab-server -- sudo fail2ban-client status sshd`.

## Exercise 1: SSH anatomy

On the server, read what sshd offers:

```bash
systemctl status ssh
grep -vE '^#|^$' /etc/ssh/sshd_config      # Port, PermitRootLogin, PasswordAuthentication, PubkeyAuthentication
```

On the client, check the segment reaches the server's sshd (a plain TCP open to port 22):

```bash
timeout 3 bash -c 'echo > /dev/tcp/172.20.2.1/22' && echo reachable
```

## Exercise 2: key-based authentication

On the client, make a modern key and the heavier classic, and read their type:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/lab_key -N ''
ssh-keygen -l -f ~/.ssh/lab_key.pub        # the fingerprint names ED25519
ssh-keygen -t rsa -b 4096 -f ~/.ssh/lab_rsa -N '' && ssh-keygen -l -f ~/.ssh/lab_rsa.pub
rm -f ~/.ssh/lab_key* ~/.ssh/lab_rsa*      # leave the home as you found it
```

ed25519 is the modern default: short keys, fast operations. 4096-bit RSA is the compatible classic.

## Exercise 3: hardening sshd_config

Tighten the server, always validating before you reload so a typo never leaves sshd unable to
start, then put the file back:

```bash
sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak
sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo sed -i 's/^#\?MaxAuthTries.*/MaxAuthTries 3/' /etc/ssh/sshd_config
sudo sshd -t && sudo systemctl reload ssh
grep -E '^(PermitRootLogin|MaxAuthTries)' /etc/ssh/sshd_config
sudo cp /etc/ssh/sshd_config.bak /etc/ssh/sshd_config && sudo systemctl reload ssh
```

`PubkeyAuthentication` stays on and the project key stays in `authorized_keys`, so vmctl keeps
its access throughout.

## Exercise 4: fail2ban bans a noisy client

fail2ban watches the auth log and bans an address after a few failures. It is scoped to the lab
segment: the NAT side is in `ignoreip`, so this never reaches the host.

```bash
# On the server, look at the jail:
sudo fail2ban-client status sshd
# On the client, a handful of wrong passwords over the segment:
for i in 1 2 3 4; do sshpass -p nope ssh -o StrictHostKeyChecking=no \
  -o PreferredAuthentications=password -o PubkeyAuthentication=no nobody@172.20.2.1 true; done
# Back on the server, the client is now on the banned list:
sudo fail2ban-client status sshd
# Clear it (always, so the next exercise can connect):
sudo fail2ban-client set sshd unbanip 172.20.2.2
```

## Exercise 5: port knocking

SSH from the segment starts closed: an iptables chain `KNOCKD_SSH` that drops it. knockd opens it
for a source only after the sequence 7000, 8000, 9000, and the reverse sequence closes it again.
(vmctl reaches the server over NAT, outside this chain, so it is never gated.)

```bash
# On the server:
sudo iptables -S KNOCKD_SSH                  # DROP by default
# On the client — closed, then knock, then open, then closed:
timeout 3 bash -c 'echo > /dev/tcp/172.20.2.1/22' || echo closed
knock 172.20.2.1 7000 8000 9000
timeout 3 bash -c 'echo > /dev/tcp/172.20.2.1/22' && echo open
knock 172.20.2.1 9000 8000 7000
```

## Exercise 6: what a scan sees

Look at the server from the client the way an outside scan would (open the port with a knock
first), and find the trace it leaves on the server:

```bash
# On the client:
knock 172.20.2.1 7000 8000 9000
nmap -Pn -p 22 172.20.2.1
nmap -Pn -sV -p 22 172.20.2.1               # the OpenSSH banner and version
# On the server:
sudo journalctl -u ssh --no-pager -n 20     # the connection attempts from the client
```

## Tests

```bash
vmctl group test ssh-lab          # all six scripts, the stack started if needed
```

Each script leaves the server as it found it: the hardening test restores `sshd_config`, the
fail2ban test unbans the client, the knocking test closes the port again.

## Stop and clean

```bash
vmctl group down ssh-lab
vmctl group clean ssh-lab         # deletes the two overlay disks (asks first); the cloud image stays in isos/
```
