# Guide passo passo

Cheatsheet riproducibili delle VM principali del lab: per ogni flusso non presidiato, dal
prerequisito sull'host fino a ciò che l'automazione digita sulla console seriale, così la
stessa installazione si può rifare a mano o capire quando qualcosa si ferma.

| Guida | Profili | Comando |
|---|---|---|
| [00-comune.md](00-comune.md) | tutti | prerequisiti host, `vmctl`, personalizzazione, uso quotidiano |
| [windows.md](windows.md) | `windows11-unattended`, `windows10-unattended` | `vmctl bootstrap-windows` |
| [arch-niri.md](arch-niri.md) | `arch-dms-local`, `arch-noctalia-local`, `cachyos-local` | `vmctl bootstrap-archinstall` |
| [debian-server.md](debian-server.md) | `debian-server` | `vmctl bootstrap-preseed` |
| [almalinux-fedora.md](almalinux-fedora.md) | `almalinux-server`, `fedora-niri-dms-local` | `vmctl bootstrap-kickstart` |
| [alpine-niri.md](alpine-niri.md) | `alpine-niri` | `vmctl bootstrap-alpine` |
| [ubuntu-niri.md](ubuntu-niri.md) | `ubuntu-niri`, `ubuntu-niri-local` | `vmctl bootstrap-unattended` |

I PDF si generano con `make guides` (servono i pacchetti Python `markdown` e `weasyprint`) e
finiscono in `docs/guides/pdf/`, non versionati. I file `.md` sono la fonte: le stringhe
esatte (prompt, comandi di trigger, riga kernel) vengono dal codice in `vmctl/`; se il codice
cambia, aggiornare la guida corrispondente.
