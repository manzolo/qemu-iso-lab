# QEMU ISO Lab

`QEMU ISO Lab` e un piccolo toolkit locale per gestire macchine virtuali di test partendo da profili definiti in JSON.

Il progetto e nato da un setup specifico per CachyOS, ma si sta evolvendo in un catalogo riusabile di guest per installazioni da ISO, boot check e test con QEMU.

Per la versione inglese, vedi [README.md](README.md).

Le note aggiuntive sono in [docs/](docs/), incluso [CI_BOOT_STRATEGY.md](docs/CI_BOOT_STRATEGY.md).

## Panoramica

Il progetto attualmente fornisce:

- un catalogo VM in `vms.json`;
- una CLI Python in `bin/vmctl`;
- una piccola TUI in `bin/vmtui`;
- un frontend sottile in `Makefile`, incluso un controllo host `setup`;
- supporto a guest `efi` e `bios`;
- artifacts isolati per VM sotto `artifacts/<vm>/`;
- un boot smoke test leggero basato su `alpine-ci`.

Profili di esempio attuali:

- `cachyos`
- `alpine-ci`

## Struttura Del Progetto

```text
.
├── Makefile
├── README.md
├── README.it.md
├── VM_MANAGER_PLAN.md
├── vms.json
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
- `make`

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

Installare le dipendenze su sistemi Arch-based:

```bash
sudo pacman -S qemu-desktop qemu-base edk2-ovmf python dialog
```

Installare le dipendenze su Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y qemu-system-x86 qemu-utils ovmf python3 make dialog
```

## Avvio Rapido

### Flusso Locale Desktop

Usa questo percorso per un guest locale normale come `cachyos`:

```bash
make setup
make show VM=cachyos
make prep VM=cachyos
make install VM=cachyos
```

Dopo aver installato il guest sul disco:

```bash
make start VM=cachyos
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
- eseguire `show`, `fetch-iso`, `prep`, `install`, `start`, `boot-check`, `clean` e `clean-all`;
- scegliere il profilo video per `install` e `start`.

## Comandi Comuni

Con `make`:

```bash
make setup
make list
make show VM=cachyos
make fetch-iso VM=cachyos
make prep VM=cachyos
make install VM=cachyos
make start VM=cachyos
make start VM=cachyos VIDEO=safe
make boot-check VM=alpine-ci
make clean VM=cachyos
make clean-all
```

Direttamente con `vmctl`:

```bash
./bin/vmctl setup
./bin/vmctl list
./bin/vmctl show cachyos
./bin/vmctl fetch-iso cachyos
./bin/vmctl prep cachyos
./bin/vmctl install cachyos
./bin/vmctl start cachyos
./bin/vmctl start cachyos --video safe
./bin/vmctl boot-check alpine-ci
./bin/vmctl clean cachyos
./bin/vmctl clean --all
```

Esempi `dry-run`:

```bash
./bin/vmctl --dry-run prep cachyos
./bin/vmctl --dry-run install cachyos
./bin/vmctl --dry-run start cachyos --video safe
```

## Modello Dei Profili VM

Ogni voce in `vms.json` definisce tipicamente:

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

Il repository include ora `windows10-template` e `windows11-template` come target conservativi per l'import:

- entrambi usano `q35` + EFI;
- entrambi usano di default un disco `sata` per evitare una dipendenza immediata dai driver storage virtio al primo boot;
- entrambi usano rete `e1000e` per una compatibilita Windows piu ampia out-of-the-box;
- `windows11-template` e utilizzabile per guest importati, ma requisiti nativi Windows 11 come TPM/Secure Boot non sono ancora modellati in `vmctl`.

Esempio:

```json
{
  "cachyos": {
    "name": "CachyOS",
    "iso": "isos/cachyos-desktop-linux-260308.iso",
    "iso_url": "https://iso.cachyos.org/desktop/260308/cachyos-desktop-linux-260308.iso",
    "disk": {
      "path": "artifacts/cachyos/disk.vhd",
      "size": "30G",
      "format": "vpc",
      "subformat": "fixed",
      "interface": "virtio"
    },
    "firmware": {
      "type": "efi",
      "code": "/usr/share/OVMF/OVMF_CODE_4M.fd",
      "vars_template": "/usr/share/OVMF/OVMF_VARS_4M.fd",
      "vars_path": "artifacts/cachyos/OVMF_VARS.fd"
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

- preferisce i path `code` e `vars_template` definiti in `vms.json`;
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
artifacts/cachyos/
├── disk.vhd
├── OVMF_VARS.fd
├── logs/
└── runtime/
```

Questo evita collisioni tra profili guest diversi.

## Profili Video

Il profilo `cachyos` include attualmente:

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
2. Aggiungere un nuovo oggetto VM in `vms.json`.
3. Scegliere formato disco, tipo di firmware e impostazioni runtime.
4. Preparare e avviare:

```bash
make prep VM=<name>
make install VM=<name>
```

## Smoke Test CI

Il repository include un vero boot smoke test basato su `alpine-ci`.

Quel profilo e volutamente piccolo e adatto alla CI:

- usa Alpine `virt`;
- avvia in headless mode;
- usa rilevamento via seriale;
- e pensato per GitHub Actions con `tcg`, senza assumere `kvm`.

Maggiori dettagli sono in [docs/CI_BOOT_STRATEGY.md](docs/CI_BOOT_STRATEGY.md).
