# Guide passo passo / Step-by-step guides

Cheatsheet riproducibili delle VM principali del lab, in due lingue: `it/` (italiano) e
`en/` (English). I nomi dei file danno l'ordine di lettura; la mappa di tutta la
documentazione è in [../README.md](../README.md).

Reproducible cheat sheets of the main VMs of the lab, in two languages: `it/` (Italian) and
`en/` (English). File names give the reading order; the map of the whole documentation is
[../README.md](../README.md).

| # | Italiano | English | Profili / Profiles | Comando / Command |
|---|---|---|---|---|
| 00 | [00-comune.md](it/00-comune.md) | [00-common.md](en/00-common.md) | tutti / all | prerequisiti host, `vmctl`, `local.json`, uso quotidiano |
| 05 | [05-interagire.md](it/05-interagire.md) | [05-interacting.md](en/05-interacting.md) | tutti / all | `vmctl shell`, `vmctl console` (seriale), `vmctl attach`; comandi tipici Linux, Windows (PowerShell via SSH), pfSense |
| 10 | [10-ubuntu-niri.md](it/10-ubuntu-niri.md) | [10-ubuntu-niri.md](en/10-ubuntu-niri.md) | `ubuntu-niri`, `ubuntu-niri-local` | `vmctl bootstrap-unattended` |
| 15 | [15-ubuntu-flavors.md](it/15-ubuntu-flavors.md) | [15-ubuntu-flavors.md](en/15-ubuntu-flavors.md) | `lubuntu-24.04`, `kubuntu-24.04`, `xubuntu-24.04`, `ubuntu-mate-24.04`, `ubuntu-budgie-24.04` | `vmctl bootstrap-unattended` |
| 20 | [20-debian-server.md](it/20-debian-server.md) | [20-debian-server.md](en/20-debian-server.md) | `debian-server` | `vmctl bootstrap-preseed` |
| 30 | [30-almalinux-fedora.md](it/30-almalinux-fedora.md) | [30-almalinux-fedora.md](en/30-almalinux-fedora.md) | `almalinux-server`, `rocky9`, `fedora-niri-dms-local`, `fedora-silverblue` | `vmctl bootstrap-kickstart` |
| 35 | [35-opensuse-autoyast.md](it/35-opensuse-autoyast.md) | [35-opensuse-autoyast.md](en/35-opensuse-autoyast.md) | `opensuse-tumbleweed-autoyast` | `vmctl bootstrap-autoyast` |
| 40 | [40-arch-niri.md](it/40-arch-niri.md) | [40-arch-niri.md](en/40-arch-niri.md) | `arch-dms-local`, `arch-noctalia-local`, `cachyos-local` | `vmctl bootstrap-archinstall` |
| 50 | [50-alpine-niri.md](it/50-alpine-niri.md) | [50-alpine-niri.md](en/50-alpine-niri.md) | `alpine-niri` | `vmctl bootstrap-alpine` |
| 60 | [60-windows.md](it/60-windows.md) | [60-windows.md](en/60-windows.md) | `windows11-unattended`, `windows10-unattended` | `vmctl bootstrap-windows` |
| 70 | [70-network-lab.html](it/70-network-lab.html) | [70-network-lab.html](en/70-network-lab.html) | `pfsense-lab`, `pihole-lab`, `lubuntu22-lab` | `vmctl lab install`, `vmctl bootstrap-pfsense` (pagina curata con lo schema SVG / curated page with the SVG topology) |
| 80 | [80-virsh-cheatsheet.html](it/80-virsh-cheatsheet.html) | [80-virsh-cheatsheet.html](en/80-virsh-cheatsheet.html) | VM esportate con `vmctl export-libvirt` / VMs exported with `vmctl export-libvirt` | `virsh ...` (bignami a schede / card cheat sheet) |

## PDF

```bash
make guides          # entrambe le lingue / both languages
python3 tools/build_guides.py it    # una sola / one only
```

Servono i pacchetti Python `markdown` e `weasyprint`. Il risultato, non versionato, è in
`docs/guides/pdf/<lingua>/`:

- `pdf/it/qemu-iso-lab-guide.pdf` e `pdf/en/qemu-iso-lab-guide.pdf`: **il manuale completo**
  per lingua, copertina con l'indice dei capitoli e tutte le guide nell'ordine qui sopra;
- `pdf/it/singole/<nome>.pdf` e `pdf/en/single/<name>.pdf`: una guida per file.

The Python packages `markdown` and `weasyprint` are required. The output, not versioned, is in
`docs/guides/pdf/<lang>/`: `qemu-iso-lab-guide.pdf` (the whole manual with a cover and the
chapter index) and `single/` (one PDF per guide).

I file `.md` e le due pagine `.html` curate sono la fonte: le stringhe esatte (prompt, comandi
di trigger, riga kernel) vengono dal codice in `vmctl/`; se il codice cambia, aggiornare
entrambe le lingue. / The `.md` files and the two curated `.html` pages are the source; when
the code changes, update both languages.
