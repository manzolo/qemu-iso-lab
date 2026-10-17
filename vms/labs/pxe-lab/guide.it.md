# PXE lab: una macchina con il disco vuoto installa Debian dalla rete

Un server e un client su un segmento isolato. Il client non ha ISO e ha il disco vuoto: il suo
firmware non trova niente da avviare sul disco e chiede alla rete. Tutto quello che gli serve
arriva dal server, un componente alla volta: un indirizzo, un boot loader, un menu, l'installer,
le risposte alle domande dell'installer e una strada verso il mirror Debian. Portato dal pxe-lab
di qlab: la LAN è passata a 172.20.9.0/24 (192.168.100.0/24 lo usa `vmctl link`) e il server è
un'immagine cloud Ubuntu 24.04.

| VM | Ruolo | Segmento `pxe-lan` | SSH dall'host |
|---|---|---|---|
| `pxe-lab-server` | dnsmasq (DHCP, DNS, TFTP), nginx, NAT | 172.20.9.1 | `vmctl shell pxe-lab-server` (127.0.0.1:2374) |
| `pxe-lab-client` | BIOS, disco vuoto, installa Debian 13 dalla rete | DHCP, 172.20.9.100-200 | `vmctl shell pxe-lab-client` (127.0.0.1:2375) |
| `pxe-lab-client-efi` | lo stesso con UEFI (OVMF): GRUB al posto di pxelinux | DHCP, 172.20.9.100-200 | `vmctl shell pxe-lab-client-efi` (127.0.0.1:2376) |

Ogni VM ha due schede: il NAT di QEMU, per l'SSH dall'host, e `pxe-lan`, il segmento che
condividono. I client si avviano dalla rete solo da `pxe-lan`; il loro installer riceve il nome di
quella scheda (BOOTIF), così non configura per errore quella del NAT. I due client differiscono
solo per il firmware, ed è proprio questo il punto: una rete, due boot loader.

I menu del server nominano il client che installano (quello BIOS `pxe-lab-client`, quello di GRUB
`pxe-lab-client-efi`) e ogni firmware ha la sua cartella di risposte: un client per firmware.

## Avvio

```bash
vmctl group install pxe-lab       # il server (immagine cloud, due minuti), poi ogni client dalla rete (una decina ciascuno)
vmctl group status pxe-lab
vmctl group map pxe-lab --open    # la mappa: traffico per scheda, l'ispettore dei pacchetti, gli esercizi
```

`group install` installa prima il server. L'installazione di un client è `vmctl bootstrap-netboot
pxe-lab-client` (o `pxe-lab-client-efi`): accende il server se è spento, scrive le risposte del
client nella radice web del server e avvia il client dalla scheda `pxe-lan`.

## Un avvio, passo per passo

1. **Il firmware.** Per l'installazione la scheda di rete è il primo dispositivo di avvio. Sul
   client BIOS la sua ROM è iPXE (tutte le schede di rete di QEMU la hanno); sul client UEFI è il
   PXE di OVMF, che prende la strada dell'esercizio 7.
2. **DHCP.** iPXE manda un DISCOVER in broadcast su `pxe-lan`. dnsmasq risponde con un OFFER:
   un indirizzo tra 172.20.9.100 e 200 e, siccome la richiesta porta l'opzione 175 (iPXE), un file
   di avvio che è un URL `http://`: `boot.ipxe`. REQUEST e ACK lo confermano.
3. **HTTP, poi TFTP.** iPXE scarica `boot.ipxe` da nginx. Lo script carica `lpxelinux.0` via
   TFTP; pxelinux prende i suoi moduli e `pxelinux.cfg/default` sempre via TFTP e mostra il menu.
4. **L'installer.** La voce predefinita del menu carica kernel e initrd dell'installer Debian via
   HTTP (l'initrd pesa 40 MB: via TFTP ci vorrebbero minuti), con `url=` che punta alle risposte,
   `BOOTIF` che nomina la scheda da cui è partito e la console seriale.
5. **Le risposte.** L'installer configura quella scheda con il DHCP, scarica `preseed.cfg` e da lì
   non chiede più niente: lingua, disco, utente, pacchetti e boot loader hanno tutti una risposta.
6. **Il mirror.** I pacchetti arrivano da deb.debian.org. Il segmento non ha una strada propria
   verso l'esterno: il server inoltra e maschera, e dnsmasq risponde alle domande DNS.
7. **La fine.** Lo script finale installa la chiave e la regola sudo, sincronizza, scrive il
   segnale di fine sulla console seriale e la macchina si spegne da sola. Poi `vmctl start` avvia
   il disco.

## Esercizio 1: dnsmasq, chiuso nel segmento del lab

```bash
cat /etc/dnsmasq.d/pxe-lab.conf
sudo ss -ulpn | grep dnsmasq
```

`interface=pxe-lan` e `bind-dynamic` tengono dnsmasq sul segmento: il DHCP sulla 67 è legato a
`pxe-lan`, DNS e TFTP a 172.20.9.1. Un server DHCP che risponde sulla rete di qualcun altro è un
guasto, per questo è la cosa più importante del file.

Il file dice anche dove vanno le domande DNS: `no-resolv` e `server=127.0.0.53`, il resolver del
server stesso. Il servizio dnsmasq di Ubuntu lo manda a un file di resolvconf che su questo
sistema non esiste; senza quelle due righe ogni ricerca del client viene rifiutata e l'installer
si ferma su "Bad archive mirror", con la rete per il resto perfetta.

## Esercizio 2: cosa serve il TFTP

```bash
ls -l /srv/pxe/tftp /srv/pxe/tftp/pxelinux.cfg
cat /srv/pxe/tftp/pxelinux.cfg/default
```

Il TFTP non ha autenticazione e sposta un piccolo blocco alla volta: porta il boot loader
(90 KB), i suoi moduli e il menu, niente di più. Dal client leggi il menu come fa il firmware,
poi guarda dnsmasq registrare il trasferimento:

```bash
curl -s tftp://172.20.9.1/pxelinux.cfg/default | head -5            # sul client
sudo journalctl -u dnsmasq --since -2min | grep sent                # sul server
```

## Esercizio 3: DORA

Sul server segui dnsmasq: `sudo journalctl -u dnsmasq -f`. Sul client rilascia l'indirizzo della
scheda del segmento e chiedilo di nuovo:

```bash
IF=$(ip -o -4 addr | awk '/172\.20\.9\./ {print $2}'); echo $IF
sudo ifdown $IF; sudo ifup $IF
```

Il log mostra DHCPDISCOVER, DHCPOFFER, DHCPREQUEST e DHCPACK con il MAC del client e, grazie a
`log-dhcp`, ogni opzione inviata: 1 (maschera), 3 (router), 6 (DNS), 67 (file di avvio). Il lease
finisce in `/var/lib/misc/dnsmasq.leases`.

Sulla mappa del lab la lente accanto a una scheda `pxe-lan` apre l'ispettore dei pacchetti: il
filtro `port 67 or port 68` mostra gli stessi quattro messaggi sul filo, con i loro campi.

## Esercizio 4: dal firmware all'installer

```bash
curl -s http://172.20.9.1/boot.ipxe                                 # sul client
curl -sI http://172.20.9.1/debian/initrd.gz | grep -i length
sudo grep -o 'BOOTIF=[^ ]*' /var/log/installer/syslog | head -1
sudo tail -5 /var/log/nginx/pxe-lab.access.log                      # sul server
```

`boot.ipxe` imposta l'opzione 210, il prefisso dei percorsi di pxelinux: senza, pxelinux
cercherebbe i suoi moduli sotto l'URL HTTP da cui è arrivato. pxelinux è un programma BIOS: un
iPXE che gira su firmware UEFI passa invece a GRUB (esercizio 7), e se non parte nessun boot
loader lo script avvia direttamente l'installer.

## Esercizio 5: le risposte

```bash
ls -l /srv/pxe/www/debian
grep -E '^d-i (netcfg|mirror|partman-auto)/' /srv/pxe/www/debian/preseed.cfg
```

vmctl scrive qui `preseed.cfg` e `late_command.sh` prima di ogni installazione: l'utente e l'hash
della password, la chiave SSH del progetto, il segnale di fine che l'host aspetta. L'installer
scarica il primo con `url=` e il secondo con `wget` dal suo comando finale.

## Esercizio 6: il gateway

```bash
sysctl net.ipv4.ip_forward                                          # sul server
sudo iptables -t nat -L POSTROUTING -v -n
```

Dal client lega una richiesta alla scheda del segmento per fare la strada dell'installer:

```bash
IF=$(ip -o -4 addr | awk '/172\.20\.9\./ {print $2}'); echo $IF
curl -s --interface $IF -o /dev/null -w '%{http_code}\n' http://deb.debian.org/debian/dists/trixie/Release
```

Con `sudo sysctl -w net.ipv4.ip_forward=0` sul server la stessa richiesta resta appesa; `=1` la
fa ripartire.

## Esercizio 7: UEFI, la stessa rete con un altro boot loader

`pxe-lab-client-efi` si avvia con OVMF. Il PXE del suo firmware lo dichiara nell'opzione DHCP 93
(client-arch 7, UEFI x86-64), e dnsmasq risponde con lo shim di Debian, `bootnetx64.efi`, invece
di pxelinux:

```bash
grep -E 'efi64|dhcp-boot' /etc/dnsmasq.d/pxe-lab.conf                          # sul server
ls -l /srv/pxe/tftp/debian-installer/amd64
cat /srv/pxe/tftp/debian-installer/amd64/grub/grub.cfg
```

Lo shim chiede `grubx64.efi` nella radice TFTP (il server ne tiene lì una copia), GRUB legge
moduli e `grub.cfg` dal suo prefisso incorporato, `debian-installer/amd64/grub`, e il menu carica l'installer via HTTP: `(http,172.20.9.1)/debian/linux`. GRUB conosce
il MAC con cui si è avviato dalla rete (`$net_default_mac`, `aa:bb:…`) e `regexp` lo riscrive nella
forma BOOTIF di d-i, `01-aa-bb-…`. Le risposte arrivano da `debian-efi/`, accanto a quelle del
client BIOS.

```bash
ls /sys/firmware/efi                                                # su pxe-lab-client-efi: solo UEFI
sudo efibootmgr                                                     # la voce "debian" dell'installer
sudo grep -o 'BOOTIF=[^ ]*' /var/log/installer/syslog | head -1
```

## Esercizio 8: reinstallarlo

Dall'host:

```bash
vmctl stop pxe-lab-client
vmctl clean pxe-lab-client
vmctl bootstrap-netboot pxe-lab-client
```

Seguilo sulla console del client (`vmctl attach pxe-lab-client`, o il pulsante Console della
mappa), in `journalctl -u dnsmasq -f` sul server e nell'ispettore dei pacchetti con
`port 67 or port 68 or port 69 or port 80`. Gli stessi tre comandi reinstallano
`pxe-lab-client-efi`, passando da GRUB.

## Test

```bash
vmctl group test pxe-lab
```

Sette script in `vms/labs/pxe-lab/tests/`, uno per esercizio: dnsmasq chiuso nel segmento, la
radice TFTP e un trasferimento TFTP, un DORA completo, la catena iPXE e la riga del kernel
dell'installer, il client installato, il gateway, il client UEFI e il suo GRUB.

## Ripristino

```bash
vmctl group reset pxe-lab           # tutte le macchine tornano al checkpoint lab-start
sudo bash ~/setup-pxe-server.sh     # sul server: configurazione e file scritti di nuovo
```
