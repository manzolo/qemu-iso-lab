# Interagire con le VM da riga di comando

Come si entra in una VM del lab senza finestra grafica: SSH, console seriale, schermo VNC, e
i comandi tipici da lanciare dentro Linux, Windows e pfSense. Vale per ogni profilo; gli
esempi usano il laboratorio di rete (`pfsense-lab`, `pihole-lab`, `lubuntu22-lab`) e le VM
Windows non presidiate.

## 1. Le tre porte d'ingresso

| Via | Comando | Quando usarla |
|---|---|---|
| **SSH** | `vmctl shell <vm>` | Il modo normale. Usa la chiave del progetto (`artifacts/<vm>/ssh/id_ed25519`) e la porta `ssh_host_port` del profilo su `127.0.0.1`: mai password. Su Windows apre `cmd.exe`, su pfSense la shell FreeBSD. |
| **Seriale** | `vmctl console <vm>` | La VM è avviata in background (`vmctl start <vm> --headless --background` o `vmctl lab up`) e la rete non risponde, oppure serve il menu console di pfSense o il boot. Collega il terminale a COM1/ttyS0; si esce con `Ctrl-]`. Tutto ciò che il guest scrive sulla seriale finisce comunque in `artifacts/<vm>/logs/serial.log`. |
| **Schermo** | `vmctl attach <vm>` | Desktop o installer da guardare: apre lo schermo VNC della VM headless in `remote-viewer` (o `--no-viewer` per il solo indirizzo). Anche durante un bootstrap. |

Chi risponde sulla seriale:

- **Linux**: serve un getty su `ttyS0`. I profili del lab (`pihole-lab`, `lubuntu22-lab`) e
  `ubuntu-server-headless` lo abilitano; su un'altra VM basta una volta
  `sudo systemctl enable --now serial-getty@ttyS0.service` (via `vmctl shell`).
- **pfSense**: la console seriale è abilitata dal `config.xml` generato: dopo il boot compare il
  menu numerato (0 logout, 8 shell, 5 reboot, 6 halt...).
- **Windows**: nessuna shell su COM1 (lo script di primo logon ci scrive solo il log). La riga di
  comando di Windows è SSH, vedi §3.

Per lanciare un comando senza aprire la sessione, `vmctl shell` non prende argomenti: si
usa `ssh` direttamente con la stessa chiave e porta, che è anche quello che fa il
`post_install_run` dei profili:

```bash
ssh -i artifacts/lubuntu22-lab/ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -p 2239 lab@127.0.0.1 'ip -br addr'
```

Nel lab di rete le porte 2238/2239 dei membri passano dal router: `vmctl shell pihole-lab`
funziona solo con `pfsense-lab` acceso (prima dell'installazione, invece, sono forward slirp
diretti). Su libvirt, dopo `vmctl lab export`, si usa l'IP vero: `ssh lab@192.168.0.100`.

## 2. Linux: controlli tipici

Dal client Lubuntu (`vmctl shell lubuntu22-lab`):

```bash
ip -br addr                              # la NIC (enp0s4 sul client, enp0s2 su Pi-hole): 192.168.0.100/24
ip route                                 # default via 192.168.0.1
ping -c 3 192.168.0.1                    # il firewall risponde sul segmento
ping -c 3 192.168.0.10                   # Pi-hole
resolvectl query pfsense.qlan            # CNAME -> firewall.qlan -> 192.168.0.1 (record di Pi-hole)
resolvectl query example.org             # nome pubblico: Pi-hole -> OpenDNS attraverso pfSense
curl -sI https://example.org | head -1   # HTTP/2 200: uscita NAT attraverso il firewall
systemd-analyze                          # boot ben sotto il minuto (wait-online spento)
ls ~/shared                              # la cartella shared/ dell'host (virtiofs)
```

Da Pi-hole (`vmctl shell pihole-lab`):

```bash
pihole status                            # FTL attivo, blocking enabled
pihole -t                                # tail delle query in tempo reale (Ctrl-C per uscire)
sudo pihole-FTL --config dhcp.active     # true: DHCP per gli ospiti del segmento (senza sudo legge un valore non affidabile)
dig @127.0.0.1 lubuntu.qlan +short       # 192.168.0.100
sudo cat /etc/pihole/pihole.toml | head  # la configurazione generata da vmctl
```

Dalla seriale (`vmctl console pihole-lab`): login `lab`, poi gli stessi comandi; utile quando
si sta cambiando la rete e SSH cadrebbe. `Ctrl-]` per tornare all'host.

## 3. Windows: PowerShell via SSH

`vmctl shell windows11-unattended` apre `cmd.exe` come l'utente del profilo (OpenSSH Server
installato al primo logon dallo script `vmctl-setup.ps1`, chiave del progetto in
`administrators_authorized_keys`). Da lì:

```bat
powershell                                   :: shell interattiva PowerShell
powershell -NoProfile -Command "Get-ComputerInfo | Select OsName, OsVersion"
powershell -NoProfile -Command "Test-NetConnection 192.168.0.1 -Port 80"
powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 | ft IPAddress, InterfaceAlias"
type C:\vmctl\setup.log                      :: il log del primo logon, lo stesso finito su COM1
```

Un comando solo, senza sessione, con lo stesso `ssh` di prima (porta 2235 per Windows 11,
2236 per Windows 10):

```bash
ssh -i artifacts/windows11-unattended/ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -p 2235 lab@127.0.0.1 'powershell -NoProfile -Command "Get-Date; hostname"'
```

Uno script intero: si copia con `scp` (stessa chiave, `-P` maiuscola) in `C:/Users/<utente>/`
e si lancia con `powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\<utente>\x.ps1`.
Il `post_install_run` dei profili Windows fa esattamente questo, comando per comando, dopo
l'installazione; conviene scrivere PowerShell e non `findstr` su output localizzati.

Per portare una VM Windows sul segmento del lab: `vmctl lab attach windows11-unattended
--apply` e poi in PowerShell `Get-NetIPConfiguration` per vedere l'indirizzo preso via DHCP
da Pi-hole (pool `.150-.199`). Attenzione: con la NIC sul segmento la porta SSH 2235 non
esiste più (niente slirp); si rientra da `vmctl attach` o, su libvirt, via IP.

## 4. pfSense: console e shell

```bash
vmctl console pfsense-lab                # menu console (root): 8) Shell, poi Ctrl-] per uscire
vmctl shell pfsense-lab                  # shell FreeBSD via SSH (porta 2237) come utente del profilo
ssh -i artifacts/pfsense-lab/ssh/id_ed25519 -p 2237 admin@127.0.0.1   # come admin (uid 0): serve per pfctl
```

`pfctl` legge `/dev/pf` e vuole root: l'utente del profilo ha la shell ma non i privilegi
(pfSense non ha sudo), quindi o si entra come `admin` con la stessa chiave, o dal menu console.
Nella shell:

```sh
ifconfig vtnet0 inet                     # WAN (slirp 10.0.2.15 su QEMU puro, 192.168.122.x su libvirt)
ifconfig vtnet1 inet                     # LAN 192.168.0.1
pfctl -sr | head -20                     # regole attive (LAN allow, WAN GUI/SSH, forward)
pfctl -sn                                # NAT: outbound automatico + i port forward verso i membri
pfctl -ss | grep 192.168.0.100           # stati aperti dal client
ping -c 3 192.168.0.10                   # il router vede Pi-hole
drill lubuntu.qlan @192.168.0.10         # il DNS di sistema è Pi-hole
cat /cf/conf/config.xml | head -40       # la configurazione scritta dall'installer
```

La GUI è su `http://127.0.0.1:8080/` (QEMU puro) o `http://192.168.0.1/` (libvirt), utente
del profilo o `admin`, stessa password.

## 5. Dall'host, senza entrare

```bash
vmctl lab status                         # ruolo, IP, disco, stato di ogni membro
vmctl lab check                          # sonde HTTP e TCP: GUI pfSense, GUI Pi-hole, porte SSH
tail -f artifacts/pfsense-lab/logs/serial.log       # la seriale di una VM in background
curl -s http://127.0.0.1:8081/admin/ | head -3      # Pi-hole attraverso il forward del router
```

I log dei bootstrap stanno in `artifacts/<vm>/logs/` (`bootstrap-serial.log` è la seriale
dell'installer, `post-install.stdout.log` i comandi SSH del post-install).
