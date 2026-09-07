# Guida comune: host, vmctl e uso quotidiano

Tutto quello che vale per ogni VM del lab, da leggere una volta. Le guide per distro
rimandano qui per i passi ripetuti.

## 1. Prerequisiti sull'host (Linux)

```bash
# Ubuntu / Debian
sudo apt install -y qemu-system-x86 qemu-utils ovmf python3 fzf xorriso virtiofsd p7zip-full
# Arch / CachyOS
sudo pacman -S qemu-desktop qemu-base edk2-ovmf python fzf xorriso virtiofsd p7zip

git clone git@github.com:manzolo/qemu-iso-lab.git && cd qemu-iso-lab
make install-cli          # symlink di vmctl e vmtui in ~/.local/bin
vmctl setup               # controlla qemu, qemu-img, OVMF e i tool opzionali
```

`vmctl setup` deve dire `Setup check passed`. I tool opzionali servono a flussi precisi:
`xorriso` per le seed ISO, `7z` per la ISO di Windows (file system UDF), `virtiofsd` per le
cartelle condivise, `dialog`/`fzf` per la TUI.

## 2. Come funziona un bootstrap non presidiato

Ogni comando `vmctl bootstrap-*` fa sempre le stesse cose, cambia solo l'installer:

1. Scarica la ISO in `isos/` (o usa quella indicata dal profilo) e la valida.
2. Genera l'answer file del profilo e lo impacchetta in una piccola **seed ISO** sotto
   `artifacts/<vm>/`.
3. Estrae kernel e initrd dalla ISO e avvia QEMU **headless** con la console seriale sullo
   stdio: tutto ciò che il guest stampa su `ttyS0`/`COM1` finisce sul tuo terminale e in
   `artifacts/<vm>/logs/bootstrap-serial.log`.
4. Quando serve, digita da solo sul seriale (login `root`, mount del seed, avvio dello script).
5. Aspetta il **token di completamento** (`==> ... installation complete!`) e lascia che il
   guest si spenga da solo.
6. Riavvia la VM installata in background e fa il post-install via SSH sulla porta del profilo.

Mentre gira puoi guardare lo schermo da un altro terminale:

```bash
vmctl attach <vm>            # apre remote-viewer sul VNC della VM headless
vmctl attach <vm> --no-viewer   # stampa solo l'indirizzo vnc://127.0.0.1:PORTA
```

## 3. Personalizzare senza toccare i profili tracciati

I profili in `vms/profiles/*.json` sono generici (utente `lab`, password `lab`). Le tue
impostazioni vanno in `vms/profiles/local.json`, ignorato da git:

```bash
make init-local-profile      # copia local.json.example
```

Esempio: cambiare utente, chiave SSH e cartella condivisa di un profilo.

```json
{
  "vms": {
    "arch-dms": {
      "archinstall_config": { "username": "TUO_UTENTE", "password": "TUA_PASSWORD" },
      "ssh_provision": { "user": "TUO_UTENTE", "ssh_key": "~/.ssh/id_ed25519" },
      "shared_dir": { "source": "~/Workspaces/qemu/storage/shared", "tag": "shared" }
    }
  }
}
```

Il nome utente deve coincidere in tutte le sezioni che lo dichiarano; ogni `{{user}}` nei
percorsi e nei comandi del profilo segue. `vmctl show <vm> --json` mostra il profilo risolto.

## 4. Uso quotidiano

| Cosa | Comando |
|---|---|
| Elenco e stato | `vmctl list`, `vmctl status` |
| Avvio con finestra | `vmctl start <vm>` |
| Avvio headless in background | `vmctl start <vm> --headless --background` |
| Shell nel guest | `vmctl shell <vm>` (SSH con la chiave di `artifacts/<vm>/ssh/`) |
| Console seriale di una VM in background | `vmctl console <vm>` (`Ctrl-]` per uscire) |
| Schermo di una VM headless | `vmctl attach <vm>` |
| Spegnimento pulito | `vmctl stop <vm>` (pulsante ACPI, poi SSH, poi SIGTERM) |
| Rifare il post-install | `vmctl post-install <vm>` |
| Cancellare disco e artefatti | `vmctl clean <vm>` |
| Menu testuale | `vmtui` |
| Lab di rete (pfSense + Pi-hole + Lubuntu) | `vmctl lab install` (da zero), `lab up`/`down`, `lab check`, `lab export`/`unexport` (libvirt), `lab clean`; `vmctl lab install --export` installa e consegna subito a libvirt. In `vmtui` è la riga "Network Lab" del dashboard. Guida 70. |

SSH diretto senza `vmctl shell`:

```bash
ssh -i artifacts/<vm>/ssh/id_ed25519 -o BatchMode=yes -p <porta> <utente>@127.0.0.1
```

## 5. Cartella condivisa (virtiofs)

Un profilo con `shared_dir` condivide una cartella dell'host con il guest. Ad ogni avvio
`vmctl` lancia un `virtiofsd` per la VM e aggiunge il device `vhost-user-fs-pci` con il tag
scelto. Nei guest Linux il post-install scrive `/mnt/<tag>` in fstab (automount systemd), lo
monta e crea i link `~/<tag>` e `<Desktop>/<tag>`; nei guest Windows installa WinFSP, avvia
`VirtioFsSvc`, la cartella compare come unità (di solito `Z:`) con un collegamento sul desktop.

Montaggio manuale su Linux, se serve: `sudo mount -t virtiofs <tag> /mnt/<tag>`.

## 6. Dove guardare quando qualcosa si ferma

| File | Contenuto |
|---|---|
| `artifacts/<vm>/logs/bootstrap-serial.log` | console seriale dell'installazione |
| `artifacts/<vm>/logs/post-install.stdout.log` | output dei comandi post-install |
| `artifacts/<vm>/logs/bootstrap-start.log` | stdout della VM avviata in background |
| `artifacts/<vm>/logs/virtiofsd.log` | demone della cartella condivisa |
| `artifacts/<vm>/<flusso>/` | answer file e script generati (preseed, ks.cfg, install.sh, autounattend.xml) |

Regola d'oro di tutti i flussi: il token di completamento viene stampato **dopo** `sync` e
`blockdev --flushbufs`, e l'host aspetta che il guest si spenga da solo. Se un'installazione
sembra riuscita ma al primo avvio finisce in `grub rescue>`, è quasi sempre questa sequenza
che è stata violata.

## Migrazione dei nomi dei profili

I vecchi nomi restano accettati con un avviso di deprecazione. Visualizza la migrazione delle cartelle dei dischi con `python3 tools/migrate_profile_names.py --root /path/to/checkout`; arresta le VM interessate e passa quel checkout al catalogo rinominato prima di aggiungere `--apply`. `--local-config /path/to/local.json` salva una copia di sicurezza e aggiorna gli override personali. Le porte SSH non cambiano.

`vmctl list` mostra lo stato `manual`, `unattended` o `experimental` e la data dell’ultimo PASS dal vivo registrato. Una data assente indica che non è stata registrata. La verifica storica è separata dall’esito corrente nel report HTML; test automatici e dry-run non la aggiornano.
