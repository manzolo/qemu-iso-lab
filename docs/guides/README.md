# Guide passo passo

Cheatsheet riproducibili delle VM principali del lab: per ogni flusso non presidiato, dal
prerequisito sull'host fino a ciò che l'automazione digita sulla console seriale, così la
stessa installazione si può rifare a mano o capire quando qualcosa si ferma. I nomi dei file
danno l'ordine di lettura; la mappa di tutta la documentazione è in [../README.md](../README.md).

| # | Guida | Profili | Comando |
|---|---|---|---|
| 00 | [00-comune.md](00-comune.md) | tutti | prerequisiti host, `vmctl`, personalizzazione, uso quotidiano |
| 10 | [10-ubuntu-niri.md](10-ubuntu-niri.md) | `ubuntu-niri`, `ubuntu-niri-local` | `vmctl bootstrap-unattended` |
| 20 | [20-debian-server.md](20-debian-server.md) | `debian-server` | `vmctl bootstrap-preseed` |
| 30 | [30-almalinux-fedora.md](30-almalinux-fedora.md) | `almalinux-server`, `fedora-niri-dms-local` | `vmctl bootstrap-kickstart` |
| 40 | [40-arch-niri.md](40-arch-niri.md) | `arch-dms-local`, `arch-noctalia-local`, `cachyos-local` | `vmctl bootstrap-archinstall` |
| 50 | [50-alpine-niri.md](50-alpine-niri.md) | `alpine-niri` | `vmctl bootstrap-alpine` |
| 60 | [60-windows.md](60-windows.md) | `windows11-unattended`, `windows10-unattended` | `vmctl bootstrap-windows` |
| 70 | [70-network-lab.html](70-network-lab.html) | `pfsense-lab`, `pihole-lab`, `lubuntu22-lab` | `vmctl lab install`, `vmctl bootstrap-pfsense` (pagina curata con lo schema di rete SVG, come in kvm-lab) |
| 80 | [80-virsh-cheatsheet.html](80-virsh-cheatsheet.html) | VM esportate con `vmctl export-libvirt`, VM legacy libvirt | `virsh ...` (bignami a schede ereditato da kvm-lab) |

## PDF

```bash
make guides
```

Servono i pacchetti Python `markdown` e `weasyprint`. Il risultato, non versionato, è in
`docs/guides/pdf/`:

- `qemu-iso-lab-guide.pdf`: **il manuale completo**, copertina con l'indice dei capitoli e
  tutte le guide nell'ordine qui sopra;
- `singole/<nome>.pdf`: una guida per file, per stamparne una sola.

I file `.md` e le due pagine `.html` curate sono la fonte: le stringhe esatte (prompt, comandi
di trigger, riga kernel) vengono dal codice in `vmctl/`; se il codice cambia, aggiornare la
guida corrispondente.
