# Laboratorio di rete: pfSense, Pi-hole e client Lubuntu

Profili: `pfsense-lab` (router, SSH 2237), `pihole-lab` (DNS/DHCP, SSH 2238),
`lubuntu22-lab` (client desktop, SSH 2239). È il `network-lab` di kvm-lab portato su QEMU
puro: stessa topologia, stesse tre installazioni da zero, con in più la strada libvirt tramite
`vmctl export-libvirt`. Tempo tipico: 5-10 minuti pfSense, 10-15 Pi-hole, 20-30 Lubuntu.

        Internet --> WAN slirp --> pfsense-lab (192.168.0.1)
                                      |
                        segmento lab-lan 192.168.0.0/24, dominio qlan
                        |-- pihole-lab     192.168.0.10   DNS, DHCP .150-.199
                        |-- lubuntu22-lab  192.168.0.100  desktop
                        |-- altre VM       vmctl lab attach <vm>
                        `-- host           192.168.0.254  (solo strada libvirt)

## 1. Prerequisiti specifici

- La ISO **pfSense CE 2.7.2 amd64 (DVD)**. Netgate non pubblica un URL stabile: si scarica
  dal sito e si mette in `isos/pfSense-CE-2.7.2-RELEASE-amd64.iso`, oppure si indica il
  percorso in `local.json`. Il flusso controlla che sia l'installer offline 2.7.2 (il suo
  `rc.local` lancia `bsdinstall script`) e rifiuta versioni diverse.
- `xorriso` e `growisofs` (pacchetto `dvd+rw-tools`) per ricostruire la ISO, il modulo Python
  `bcrypt` (`python3-bcrypt` su Debian/Ubuntu, `python-bcrypt` su Arch) per l'hash della
  password pfSense. `vmctl setup` li elenca e propone l'installazione.
- La ISO Ubuntu Server 22.04.5 viene scaricata da sola (Pi-hole e Lubuntu partono da lì).
- `virtiofsd` per la cartella condivisa del client.

## 2. Personalizzazione in local.json

```json
"pfsense-lab": {
  "iso": "/percorso/pfSense-CE-2.7.2-RELEASE-amd64.iso",
  "pfsense_config": { "username": "TUO_UTENTE", "password": "TUA_PASSWORD", "timezone": "Europe/Rome" },
  "ssh_provision": { "user": "TUO_UTENTE" }
},
"pihole-lab": {
  "autoinstall": { "username": "TUO_UTENTE", "password_hash": "HASH_SHA512" },
  "ssh_provision": { "user": "TUO_UTENTE" },
  "network_lab": { "password": "PASSWORD_WEB_PIHOLE", "hosts": ["192.168.0.50 nas.qlan"] }
},
"lubuntu22-lab": {
  "autoinstall": { "username": "TUO_UTENTE", "realname": "Nome Cognome", "password_hash": "HASH_SHA512" },
  "cloud_init": { "user": "TUO_UTENTE" },
  "ssh_provision": { "user": "TUO_UTENTE" },
  "shared_dir": { "source": "/percorso/shared", "tag": "shared" }
}
```

La topologia (subnet, indirizzi, pool DHCP, dominio) sta una volta sola in
`pfsense-lab.network_lab`; i membri dicono solo ruolo, gateway e IP. Cambiando la subnet in
`local.json` cambiano insieme config.xml di pfSense, netplan dei membri e `pihole.toml`:
`vmctl lab plan` rifiuta indirizzi fuori subnet, duplicati o un pool DHCP che copre gli IP
statici.

## 3. I comandi

```bash
vmctl lab plan                      # anteprima: topologia, forward, ordine, XML rete libvirt
vmctl lab install                   # pfsense-lab -> pihole-lab -> lubuntu22-lab
vmctl lab up                        # avvia le tre VM headless in background, in ordine
vmctl lab status                    # ruolo, indirizzo, disco e stato di ogni membro
vmctl lab down                      # ferma in ordine inverso
vmctl attach pfsense-lab            # in un altro terminale, per guardare l'installer
```

`lab install` rifiuta di partire se uno dei tre dischi esiste già: mai reinstallazioni
implicite. Per rifare da zero: `vmctl clean pihole-lab` (o spostare il disco) e rilanciare.
I singoli passi si possono lanciare a mano: `vmctl bootstrap-pfsense pfsense-lab`,
`vmctl bootstrap-unattended pihole-lab`, `vmctl bootstrap-unattended lubuntu22-lab`.

## 4. pfSense, fase per fase

1. **config.xml** renderizzato da `network_lab`: WAN `vtnet0` in DHCP, LAN `vtnet1`
   `192.168.0.1/24`, Unbound, NAT in uscita automatico, DNS di sistema = Pi-hole, utente
   `admin` e l'utente del profilo (stessa password, hash bcrypt, chiave SSH del progetto),
   SSH abilitato, offload checksum disabilitato, console seriale a 115200. Le regole
   WAN permettono GUI e SSH dal lato WAN (che su QEMU puro è l'host) e le regole NAT
   inoltrano 8081 → `192.168.0.10:80`, 2238 → `192.168.0.10:22`, 2239 → `192.168.0.100:22`.
2. **ISO non presidiata** in `artifacts/pfsense-lab/pfsense/install.iso`: `xorriso -osirrox`
   estrae `rc.local` e `bsdinstall/script` dalla ISO originale (e verifica che sia quella
   giusta), poi `cp` + `growisofs -M` innestano `installerconfig`, il nuovo `rc.local` e lo
   script patchato. Il file `.source` accanto ricorda ISO e config usati: si ricostruisce
   solo se cambiano.
3. **QEMU**: macchina `pc`, BIOS, disco `virtio-blk-pci,bootindex=1`, CD IDE `bootindex=2`,
   seriale su stdio (`cuau0` nel guest), WAN slirp con i forward, LAN sul segmento
   multicast `lab-lan`, `-no-reboot`.
4. **Sulla seriale** si vedono il boot di FreeBSD, poi `bsdinstall` che partiziona `vtbd0`
   (GPT, ZFS pool `pfSense`, swap 1 G), estrae `base.txz`, scrive `/cf/conf/config.xml`, e
   alla fine:

        ==> pfSense installation complete!

   L'`rc.local` fa `sync` prima del token e `shutdown -p now` dopo: l'host aspetta lo
   spegnimento del guest, non lo uccide. In caso di errore compaiono
   `==> pfSense installation FAILED` e il log di bsdinstall, poi il guest si spegne e il
   comando termina con errore.
5. Nessun post-install SSH: la configurazione è tutta nel config.xml. Al primo avvio da
   disco pfSense applica le regole; la GUI risponde su `http://127.0.0.1:8080/`.

## 5. Pi-hole e Lubuntu, fase per fase

1. **Autoinstall Ubuntu 22.04.5** identico agli altri profili Ubuntu (seed `cidata`, kernel e
   initrd estratti da `casper/`, `autoinstall ds=nocloud console=ttyS0`), ma sulla NIC di
   **fase install**: slirp con il forward SSH del profilo. Sulla seriale scorrono subiquity
   e curtin fino al poweroff.
2. **Primo avvio in background** sulla stessa NIC; `wait_for_ssh` sulla porta del profilo,
   attesa di cloud-init e di apt.
3. **Hook `network_lab`** (prima dei `post_install_run` del profilo):

        :: Network lab: configuring pihole-lab as pihole (192.168.0.10 on lab-lan)
        $ ssh ... 'rm -rf /tmp/vmctl-netlab'
        $ scp ... artifacts/pihole-lab/netlab lab@127.0.0.1:/tmp/vmctl-netlab
        $ ssh ... 'sudo bash /tmp/vmctl-netlab/setup.sh'
        ==> netlab: installing Pi-hole (unattended, pre-seeded pihole.toml)
        ==> netlab: writing the static LAN configuration (applied at the next boot)
        ==> netlab: guest configured

   Il nome dell'interfaccia viene chiesto al guest (`ip -o link` sul MAC del lab) e finisce
   sia nel netplan (`match: macaddress` + `set-name`) sia in `pihole.toml`. Il Pi-hole v6 si
   installa con `basic-install.sh --unattended` grazie al `pihole.toml` pre-compilato
   (upstream OpenDNS, record locali del router e di ogni membro, pool DHCP `.150`-`.199`),
   poi `pihole setpassword`. Sul client lo script disabilita `systemd-networkd-wait-online`
   (renderer NetworkManager): senza, ogni boot aspetterebbe 120 s.
4. **Indirizzo statico al prossimo boot**: `/etc/netplan/01-vmctl-lab.yaml` e
   `99-vmctl-lab-network.cfg` (cloud-init non tocca più la rete). `vmctl lab install` ferma la
   VM; da `vmctl lab up` in avanti la NIC è sul segmento e SSH passa dal router.

## 6. Dopo l'installazione

| Cosa | Dove |
|---|---|
| GUI pfSense | `http://127.0.0.1:8080/` (utente del profilo o `admin`); al primo accesso pfSense propone il wizard iniziale, si può chiudere |
| GUI Pi-hole | `http://127.0.0.1:8081/admin/` |
| Shell | `vmctl shell pfsense-lab`, `vmctl shell pihole-lab`, `vmctl shell lubuntu22-lab` |
| Desktop del client | `vmctl attach lubuntu22-lab`, oppure `vmctl start lubuntu22-lab` con finestra |
| Un'altra VM sulla LAN | `vmctl lab attach arch-dms-local --apply`, poi `vmctl start` normale |

Strada libvirt: `vmctl export-libvirt pfsense-lab` (e i membri) crea anche la rete libvirt
`lab-lan` (bridge `virbr-lab`, host `192.168.0.254`); da lì GUI e SSH sono raggiungibili
direttamente sugli IP della LAN come in kvm-lab.

## 7. Se qualcosa si ferma

- `bootstrap-pfsense` termina subito con "Incompatible ISO": non è la 2.7.2 offline.
- Sulla seriale pfSense resta al menu dell'installer: l'ISO non ha ricevuto l'`rc.local`
  (controllare che `growisofs` sia quello di dvd+rw-tools, non un alias).
- `vmctl shell pihole-lab` non risponde dopo `lab up`: il router non è partito o
  `pfsense-lab` non ha ancora finito il boot (`vmctl attach pfsense-lab`).
- Il client parte lento: verificare `systemctl is-enabled systemd-networkd-wait-online`
  (deve essere `disabled`); i `post_install_run` del profilo lo stampano.
- `vmctl lab plan` fallisce con "hostfwd must forward host port N": ai forward del router
  manca una porta che le regole NAT usano (dopo aver cambiato una porta SSH in local.json).
