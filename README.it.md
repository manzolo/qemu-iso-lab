# VM Test Manager

Piccolo gestore locale per creare, avviare e pulire macchine virtuali QEMU partendo da profili definiti in JSON.

Il progetto e nato da un setup ad hoc per CachyOS, ma la direzione e piu generale: un catalogo di guest testabili con parametri diversi per ISO, disco, firmware, video e runtime.

Per the English version, see [README.md](README.md).

Le note di progetto aggiuntive sono in [docs/](docs/), incluso [CI_BOOT_STRATEGY.md](docs/CI_BOOT_STRATEGY.md).

## Stato Attuale

La V1 e una base funzionante con:

- catalogo VM in `vms.json`;
- motore CLI in `bin/vmctl`;
- frontend comodo in `Makefile`;
- supporto iniziale a firmware `efi` e `bios`;
- artifacts separati per ogni VM sotto `artifacts/<nome-vm>/`.

Al momento il profilo configurato e:

- `cachyos`
- `alpine-ci`

## Struttura

```text
.
├── Makefile
├── README.md
├── README.it.md
├── VM_MANAGER_PLAN.md
├── vms.json
├── bin/
│   └── vmctl
├── isos/
│   └── cachyos-desktop-linux-260308.iso
├── artifacts/
│   └── cachyos/
│       ├── disk.vhd
│       ├── OVMF_VARS.fd
│       ├── logs/
│       └── runtime/
└── ...
```

## Concetti

### `vms.json`

Contiene i profili VM.

Ogni profilo definisce almeno:

- nome logico;
- path della ISO;
- URL opzionale per scaricare la ISO;
- configurazione del disco;
- tipo di firmware;
- RAM e CPU;
- profili video;
- impostazioni runtime comuni.

Esempio:

```json
{
  "vms": {
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
}
```

### `bin/vmctl`

E il motore del progetto.

Responsabilita principali:

- leggere `vms.json`;
- risolvere i path;
- creare disco e NVRAM locale;
- comporre il comando QEMU;
- eseguire `prep`, `install`, `start` e `clean`.

### `Makefile`

Non contiene logica complessa. E solo un frontend rapido che chiama `bin/vmctl`.

## Requisiti

Minimi:

- `qemu-system-x86_64`
- `qemu-img`
- Python 3

Per profili EFI servono anche i file OVMF, ad esempio:

- `/usr/share/OVMF/OVMF_CODE_4M.fd`
- `/usr/share/OVMF/OVMF_VARS_4M.fd`

## Uso Rapido

### Clonare Il Repository

```bash
git clone git@github.com:manzolo/qemu-iso-lab.git
cd qemu-iso-lab
```

### Installare I Requisiti Host

Su sistemi Arch-based:

```bash
sudo pacman -S qemu-desktop qemu-base edk2-ovmf python
```

Su Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y qemu-system-x86 qemu-utils ovmf python3 make
```

### Primo Flusso Locale: Guest Desktop

Se vuoi provare localmente il guest principale orientato all'uso desktop:

```bash
make show VM=cachyos
make prep VM=cachyos
make install VM=cachyos
```

Dopo aver installato il sistema sul disco:

```bash
make start VM=cachyos
```

### Primo Flusso Tipo-CI: Boot Check Reale Minimo

Se vuoi il test end-to-end reale piu piccolo gia presente nel repo:

```bash
make prep VM=alpine-ci
make boot-check VM=alpine-ci
```

Questo percorso scarica una ISO Alpine `virt` piccola, crea un disco piccolo, avvia QEMU in headless mode e aspetta il prompt seriale `login:`.

### Elencare le VM

```bash
make list
```

oppure:

```bash
./bin/vmctl list
```

### Mostrare un profilo

```bash
make show VM=cachyos
```

### Preparare disco e NVRAM

```bash
make prep VM=cachyos
```

Questo step:

- scarica la ISO se manca e `iso_url` e configurata;
- crea il disco se manca;
- crea la copia locale della NVRAM se il firmware e EFI;
- prepara le directory `logs/` e `runtime/`.

### Scaricare solo la ISO

```bash
make fetch-iso VM=cachyos
```

Se la ISO esiste gia, non viene scaricato nulla.

### Eseguire un boot check reale headless

```bash
make boot-check VM=alpine-ci
```

Questo flusso e pensato per la CI. Usa una ISO Alpine `virt` piccola, avvia QEMU senza GUI, osserva la console seriale e termina con successo solo quando il guest raggiunge davvero la stringa attesa.

### Avviare l'installazione

```bash
make install VM=cachyos
```

### Avviare la VM installata

```bash
make start VM=cachyos
```

### Usare un profilo video diverso

```bash
make start VM=cachyos VIDEO=safe
make start VM=cachyos VIDEO=virtio-gl
```

### Pulire gli artifacts della VM

```bash
make clean VM=cachyos
```

### Pulire tutte le VM

```bash
make clean-all
```

## Uso Diretto Di `vmctl`

Comandi disponibili:

```bash
./bin/vmctl list
./bin/vmctl show cachyos
./bin/vmctl fetch-iso cachyos
./bin/vmctl prep cachyos
./bin/vmctl install cachyos
./bin/vmctl start cachyos
./bin/vmctl boot-check alpine-ci
./bin/vmctl start cachyos --video safe
./bin/vmctl clean cachyos
./bin/vmctl clean --all
```

Per vedere i comandi senza eseguirli:

```bash
./bin/vmctl --dry-run prep cachyos
./bin/vmctl --dry-run install cachyos
./bin/vmctl --dry-run start cachyos --video safe
```

## Firmware Supportato

### EFI

Per profili `efi`, `vmctl`:

- usa `OVMF_CODE` in sola lettura;
- crea una copia locale di `OVMF_VARS`;
- avvia QEMU con pflash.

### BIOS

Per profili `bios`, `vmctl`:

- non usa OVMF;
- non crea NVRAM;
- usa il boot classico del firmware QEMU/SeaBIOS.
