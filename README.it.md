# QEMU ISO Lab

`QEMU ISO Lab` e un piccolo toolkit locale per gestire macchine virtuali di test partendo da profili definiti in JSON.

Fornisce un catalogo riusabile di guest per installazioni da ISO, import da dischi fisici, boot check e test con QEMU.

Per la versione inglese, vedi [README.md](README.md).

Le note aggiuntive sono in [docs/](docs/), incluso [CI_BOOT_STRATEGY.md](docs/CI_BOOT_STRATEGY.md).

## Panoramica

Il progetto attualmente fornisce:

- un catalogo VM suddiviso tra `vms/catalog.json` e `vms/profiles/*.json`;
- una CLI Python in `bin/vmctl`;
- una piccola TUI in `bin/vmtui`;
- un frontend sottile in `Makefile`, incluso un controllo host `setup`;
- supporto a guest `efi` e `bios`;
- artifacts isolati per VM sotto `artifacts/<vm>/`;
- un boot smoke test leggero basato su `alpine-ci`.

I profili attuali includono guest desktop, guest installer/minimal, template Windows per import e il guest di smoke test `alpine-ci`.

## Struttura Del Progetto

```text
.
├── Makefile
├── README.md
├── README.it.md
├── VM_MANAGER_PLAN.md
├── vms/
│   ├── catalog.json
│   └── profiles/
├── bin/
│   ├── vmctl
│   └── vmtui
├── docs/
│   └── CI_BOOT_STRATEGY.md
├── isos/
├── artifacts/
└── tests/
```

## Requisiti

Requisiti minimi host:

- `qemu-system-x86_64`
- `qemu-img`
- Python 3

Opzionali:

- `dialog` per la TUI;
- file OVMF per guest EFI, ad esempio:
  - `/usr/share/OVMF/OVMF_CODE_4M.fd`
  - `/usr/share/OVMF/OVMF_VARS_4M.fd`

## Installazione

Clonare il repository:

```bash
git clone https://github.com/manzolo/qemu-iso-lab.git
cd qemu-iso-lab
```

Esegui prima il controllo host:

```bash
make setup
```

`make` serve solo se vuoi usare le scorciatoie `make ...` mostrate in questo README.
Se preferisci, puoi usare direttamente `./bin/vmctl ...` e non installarlo.

Installare le dipendenze su sistemi Arch-based:

```bash
sudo pacman -S qemu-desktop qemu-base edk2-ovmf python dialog make
```

Installare le dipendenze su Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y qemu-system-x86 qemu-utils ovmf python3 make dialog
```

Se non vuoi installare `make`, il minimo pratico per usare la CLI diretta e:

```bash
sudo pacman -S qemu-desktop qemu-base edk2-ovmf python dialog
# oppure
sudo apt install -y qemu-system-x86 qemu-utils ovmf python3 dialog
```

## Avvio Rapido

### Flusso Locale Guest

Usa questo percorso per un guest locale normale:

```bash
make setup
make list
make show VM=<name>
make prep VM=<name>
make install VM=<name>
```

Dopo aver installato il guest sul disco:

```bash
make start VM=<name>
```

### Flusso Locale Unattended

Usa questo percorso per i profili locali che definiscono `autoinstall` e provisioning SSH:

```bash
make prep VM=<name>
make install-unattended VM=<name>
make post-install VM=<name>
```

Oppure esegui tutto il flusso in un solo passo:

```bash
make bootstrap-unattended VM=<name>
```

Dopo il primo boot puoi aprire una shell con:

```bash
make shell VM=<name>
```

### Boot Check Reale Minimo

Usa questo percorso per il piu piccolo smoke test reale gia presente nel repository:

```bash
make prep VM=alpine-ci
make boot-check VM=alpine-ci
```

Questo flusso scarica una ISO Alpine `virt` piccola, prepara il disco, avvia QEMU in headless mode e aspetta il prompt seriale `login:`.

### TUI Opzionale

Se preferisci una piccola interfaccia testuale:

```bash
make tui
```

La TUI e un frontend sottile sopra `vmctl`. Permette di:

- scegliere il profilo VM;
- eseguire `show`, `fetch-iso`, `prep`, `install`, `install-unattended`, `start`, `post-install`, `shell`, `boot-check`, `clean` e `clean-all`;
- scegliere il profilo video per `install` e `start`.

## Comandi Comuni

Con `make`:

```bash
make setup
make list
make status
make show VM=<name>
make fetch-iso VM=<name>
make prep VM=<name>
make install VM=<name>
make install-unattended VM=<name>
make post-install VM=<name>
make bootstrap-unattended VM=<name>
make start VM=<name>
make start VM=<name> VIDEO=safe
make shell VM=<name>
make boot-check VM=alpine-ci
make clean VM=<name>
make clean-all
```

Direttamente con `vmctl`:

```bash
./bin/vmctl setup
./bin/vmctl list
./bin/vmctl status
./bin/vmctl show <name>
./bin/vmctl fetch-iso <name>
./bin/vmctl prep <name>
./bin/vmctl install <name>
./bin/vmctl install-unattended <name>
./bin/vmctl post-install <name>
./bin/vmctl bootstrap-unattended <name>
./bin/vmctl start <name>
./bin/vmctl start <name> --video safe
./bin/vmctl shell <name>
./bin/vmctl boot-check alpine-ci
./bin/vmctl clean <name>
./bin/vmctl clean --all
```

Esempi `dry-run`:

```bash
./bin/vmctl --dry-run prep <name>
./bin/vmctl --dry-run install <name>
./bin/vmctl --dry-run start <name> --video safe
```

## Modello Dei Profili VM

Ogni voce in `vms/profiles/*.json` definisce tipicamente:

- `name`
- `iso`
- `iso_url`
- `disk`
- `firmware`
- `machine`
- `memory_mb`
- `cpus`
- `network`
- `audio`
- `video`

I profili orientati all'import possono omettere `iso_url` intenzionalmente. Sono pensati per flussi come `import-device`, dove porti dentro la VM un'installazione fisica esistente invece di avviare un installer da ISO.

I profili che definiscono `cloud_init`, `ssh_provision` o `autoinstall` possono anche supportare flussi di livello piu alto come installazione unattended, provisioning post-install via SSH e accesso shell interattivo.

`status` mostra anche uno stato runtime essenziale oltre agli artifact, inclusi processi QEMU in background tracciati e porte SSH forward quando disponibili.

`clean` e intenzionalmente conservativo: ora prova prima a fermare la VM, poi rimuove gli artifact generati.

Il repository include ora `windows10-template` e `windows11-template` come target conservativi per l'import:

- entrambi usano `q35` + EFI;
- entrambi usano di default un disco `sata` per evitare una dipendenza immediata dai driver storage virtio al primo boot;
- entrambi usano rete `e1000e` per una compatibilita Windows piu ampia out-of-the-box;
- `windows11-template` e utilizzabile per guest importati, ma requisiti nativi Windows 11 come TPM/Secure Boot non sono ancora modellati in `vmctl`.

Esempio:

```json
{
  "my-vm": {
    "name": "My VM",
    "iso": "isos/example.iso",
    "iso_url": "https://example.invalid/example.iso",
    "disk": {
      "path": "artifacts/my-vm/disk.qcow2",
      "size": "32G",
      "format": "qcow2",
      "interface": "virtio"
    },
    "firmware": {
      "type": "efi",
      "code": "/usr/share/OVMF/OVMF_CODE_4M.fd",
      "vars_template": "/usr/share/OVMF/OVMF_VARS_4M.fd",
      "vars_path": "artifacts/my-vm/OVMF_VARS.fd"
    },
    "machine": "q35",
    "memory_mb": 4096,
    "cpus": 4
  }
}
```

## Modalita Firmware

### EFI

Per i profili `efi`, `vmctl`:

- preferisce i path `code` e `vars_template` definiti nel profilo;
- fa fallback su path OVMF comuni se quelli configurati non esistono;
- accetta gli override ambiente `OVMF_CODE` e `OVMF_VARS_TEMPLATE`;
- usa `OVMF_CODE` come firmware in sola lettura;
- crea una copia locale di `OVMF_VARS`;
- avvia QEMU con drive pflash.

Quindi una voce come:

```json
"firmware": {
  "type": "efi",
  "code": "/usr/share/OVMF/OVMF_CODE_4M.fd",
  "vars_template": "/usr/share/OVMF/OVMF_VARS_4M.fd",
  "vars_path": "artifacts/ubuntu-desktop/OVMF_VARS.fd"
}
```

va bene come default, e `make setup` ti dira se il tuo host usa un layout OVMF diverso.

### BIOS

Per i profili `bios`, `vmctl`:

- non usa OVMF;
- non crea file NVRAM;
- usa il normale boot flow QEMU/SeaBIOS.

## Artifacts

Ogni VM salva il proprio stato locale sotto:

```text
artifacts/<vm>/
```

Contenuto tipico:

```text
artifacts/my-vm/
├── disk.qcow2
├── OVMF_VARS.fd
├── logs/
└── runtime/
```

Questo evita collisioni tra profili guest diversi.

## Profili Video

Le varianti video dipendono dal profilo. Esempi comuni:

- `std`
- `safe`
- `virtio-gl`

Uso tipico:

- `std`: modalita semplice di default;
- `safe`: aggiunge seriale ed e piu utile per debug;
- `virtio-gl`: setup piu aggressivo per sessioni moderne Wayland/compositor.

Nota pratica:

Alcuni compositor Wayland, come `niri`, possono comunque comportarsi male in VM anche quando il guest si avvia correttamente.

## Aggiungere Una Nuova VM

Flusso minimo:

1. Copiare la ISO sotto `isos/`, oppure definire `iso_url`.
2. Aggiungere un nuovo oggetto VM in uno dei file sotto `vms/profiles/`.
3. Scegliere formato disco, tipo di firmware e impostazioni runtime.
4. Preparare e avviare:

```bash
make prep VM=<name>
make install VM=<name>
```

## Cloud-Init E Post-Install

`vmctl` puo anche allegare un seed ISO `cloud-init` generato dal profilo VM e poi completare un post-install via SSH.

Campi supportati dentro il profilo:

- `cloud_init.hostname`
- `cloud_init.user`
- `cloud_init.ssh_authorized_keys`
- `cloud_init.ssh_authorized_keys_file`
- `cloud_init.ssh_key`
- `cloud_init.ssh_host_port`
- `cloud_init.packages`
- `cloud_init.runcmd`
- `cloud_init.copy_from_host`
- `cloud_init.post_install_run`
- `autoinstall.hostname`
- `autoinstall.username`
- `autoinstall.realname`
- `autoinstall.password_hash`
- `autoinstall.timezone`
- `autoinstall.keyboard_layout`
- `autoinstall.storage_layout`
- `autoinstall.install_ssh`

Uso tipico:

```bash
./bin/vmctl start ubuntu-niri --cloud-init
./bin/vmctl post-install ubuntu-niri
```

`start --cloud-init` genera `artifacts/<vm>/cloud-init/{user-data,meta-data,seed.iso}` e allega `seed.iso` alla VM. `post-install` aspetta che SSH sia raggiungibile sulla porta inoltrata dal profilo, copia eventuali file host definiti in `copy_from_host` ed esegue i comandi remoti in `post_install_run`.

Per automatizzare anche l'installer Ubuntu Server:

```bash
./bin/vmctl install-unattended ubuntu-niri-local
./bin/vmctl start ubuntu-niri-local
./bin/vmctl post-install ubuntu-niri-local
```

`install-unattended` genera un seed `autoinstall`, estrae `casper/vmlinuz` e `casper/initrd` dalla ISO e avvia l'installer con il parametro kernel `autoinstall`. Il processo QEMU termina al reboot finale dell'installer (`-no-reboot`), poi puoi avviare il sistema installato normalmente e completare il `post-install`.

Esempio committabile:

- `ubuntu-niri` mostra una configurazione `cloud_init` pulita, senza path o username personali hardcodati.
- `ubuntu-niri` mostra anche una sezione `autoinstall` da completare con un vero hash SHA-512 della password.

Override locale ignorato da git:

- copia `vms/profiles/local.json.example` in `vms/profiles/local.json`;
- sostituisci `YOUR_USER` e i path SSH/dotfiles con i tuoi;
- usa il profilo `ubuntu-niri-local`, che resta solo sul tuo host.

Shortcut:

```bash
make init-local-profile
```

## Smoke Test CI

Il repository include un vero boot smoke test basato su `alpine-ci`.

Quel profilo e volutamente piccolo e adatto alla CI:

- usa Alpine `virt`;
- avvia in headless mode;
- usa rilevamento via seriale;
- e pensato per GitHub Actions con `tcg`, senza assumere `kvm`.

Maggiori dettagli sono in [docs/CI_BOOT_STRATEGY.md](docs/CI_BOOT_STRATEGY.md).
