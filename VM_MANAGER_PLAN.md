# VM Manager Plan

## Goal

Trasformare il setup attuale, nato per una singola VM CachyOS, in un piccolo gestore di VM riusabile per test e installazioni di piu sistemi operativi con configurazioni diverse.

L'obiettivo non e fare subito un orchestratore complesso, ma costruire una base chiara che:

- supporti piu guest tramite metadati;
- distingua bene firmware EFI e BIOS/MBR;
- generi artefatti isolati per ogni VM;
- usi `Makefile` come interfaccia comoda, ma tenga la logica vera in uno script dedicato.

## Status

Stato attuale del refactor:

- `vms.json` introdotto con primo profilo `cachyos`
- `bin/vmctl` implementato in Python
- `Makefile` ridotto a frontend sottile sopra `vmctl`
- layout migrato a `isos/` e `artifacts/<vm>/`
- supporto iniziale a `efi` e ramo logico `bios`
- comandi verificati in uso reale o `dry-run`: `list`, `show`, `prep`, `install`, `start`, `clean`

File principali gia presenti:

- [README.md](/home/manzolo/Scrivania/Temp/qemu/cachyos/README.md)
- [vms.json](/home/manzolo/Scrivania/Temp/qemu/cachyos/vms.json)
- [bin/vmctl](/home/manzolo/Scrivania/Temp/qemu/cachyos/bin/vmctl)
- [Makefile](/home/manzolo/Scrivania/Temp/qemu/cachyos/Makefile)

## Product Idea

Una CLI locale, ad esempio `vmctl`, che legge un file JSON con i profili VM e permette azioni come:

- preparare il disco;
- avviare installazione da ISO;
- avviare la VM installata;
- usare modalita video diverse;
- fare cleanup;
- eseguire step post-install specifici;
- copiare eventuali immagini su supporti esterni.

`CachyOS` sarebbe solo uno dei profili disponibili.

## Why This Structure

Il `Makefile` e ottimo come frontend rapido, ma non e il posto giusto per:

- parsing di JSON;
- branching complesso tra BIOS/EFI;
- gestione di default, override e profili;
- validazione dei metadati;
- costruzione di comandi QEMU non banali.

Quindi:

- `Makefile` resta sottile e leggibile;
- `vmctl` diventa il motore;
- `vms.json` diventa il catalogo dei guest.

## Proposed Layout

```text
.
├── Makefile
├── README.md
├── vms.json
├── bin/
│   └── vmctl
├── isos/
│   └── ...
├── artifacts/
│   ├── cachyos/
│   │   ├── disk.vhd
│   │   ├── OVMF_VARS.fd
│   │   ├── logs/
│   │   └── runtime/
│   └── debian-bios/
└── ...
```

## VM Metadata Model

Ogni VM nel JSON dovrebbe avere almeno questi campi:

```json
{
  "cachyos": {
    "name": "CachyOS",
    "iso": "isos/cachyos-desktop-linux-260308.iso",
    "disk_size": "30G",
    "disk_format": "vpc",
    "disk_subformat": "fixed",
    "firmware": "efi",
    "machine": "q35",
    "memory_mb": 4096,
    "cpus": 4,
    "net": "user",
    "audio": true,
    "video": {
      "default": "std",
      "variants": ["std", "safe", "virtio-gl"]
    }
  }
}
```

## Suggested Metadata Fields

Campi iniziali consigliati:

- `name`
- `iso`
- `disk_size`
- `disk_format`
- `disk_subformat`
- `firmware`
- `machine`
- `memory_mb`
- `cpus`
- `net`
- `audio`
- `video.default`
- `video.variants`

Campi utili in seconda fase:

- `arch`
- `boot_order`
- `disk_interface`
- `secure_boot`
- `ovmf_code`
- `ovmf_vars_template`
- `bios_image`
- `extra_qemu_args`
- `post_install`
- `ventoy_compatible`
- `copy_suffix`

## EFI vs BIOS

Il gestore dovra trattare EFI e BIOS come due profili di boot distinti.

### EFI

- usa `OVMF_CODE` in sola lettura;
- crea una copia locale di `OVMF_VARS` per ogni VM;
- usa tipicamente `q35`.

### BIOS/MBR

- niente pflash/OVMF;
- boot classico SeaBIOS;
- puo usare `pc` o altro machine type compatibile;
- utile per testare installazioni legacy o immagini MBR.

## Artifact Strategy

Ogni VM deve avere la sua directory dedicata in `artifacts/<vm>/`.

Questo evita collisioni tra:

- dischi;
- NVRAM;
- log;
- script generati;
- file temporanei.

Esempio:

```text
artifacts/cachyos/
├── disk.vhd
├── OVMF_VARS.fd
├── logs/
└── runtime/
```

## vmctl Responsibilities

`bin/vmctl` dovrebbe:

- leggere e validare `vms.json`;
- risolvere i path relativi;
- creare i dischi;
- creare la NVRAM locale per le VM EFI;
- comporre il comando `qemu-system-x86_64`;
- offrire subcommand semplici.

Subcommand V1 implementati:

- `vmctl list`
- `vmctl show <vm>`
- `vmctl prep <vm>`
- `vmctl install <vm>`
- `vmctl start <vm>`
- `vmctl start <vm> --video safe`
- `vmctl clean <vm>`
- `vmctl clean --all`

Subcommand previsti ma non ancora integrati nel nuovo flusso:

- `vmctl copy <vm> --target /dev/sdX1`
- eventuali hook `post-install`

## Makefile Role

Il `Makefile` dovrebbe diventare solo una facciata ergonomica:

```make
make list
make prep VM=cachyos
make install VM=cachyos
make start VM=cachyos
make start VM=cachyos VIDEO=safe
make clean VM=cachyos
make clean-all
```

Regola pratica:

- niente logica pesante nel `Makefile`;
- il `Makefile` chiama solo `bin/vmctl`.

Stato attuale:

- questo obiettivo e gia raggiunto nella V1

## Implementation Choice

Per `vmctl` ci sono due opzioni serie:

### Option A: Bash + jq

Pro:

- veloce da prototipare;
- dipendenze minime;
- resta vicino ai comandi QEMU reali.

Contro:

- parsing e validazione piu fragili;
- crescita piu difficile;
- piu scomodo da testare.

### Option B: Python

Pro:

- parsing JSON pulito;
- validazione migliore;
- codice piu manutenibile;
- piu facile supportare subcommand, defaults e override.

Contro:

- un po piu struttura iniziale;
- richiede piu disciplina nel design.

Direzione consigliata: `Python`.

## Minimal V1 Scope

Versione iniziale completata:

1. `vms.json` con un primo profilo `cachyos`.
2. `bin/vmctl` con subcommand:
   - `list`
   - `show <vm>`
   - `prep <vm>`
   - `install <vm>`
   - `start <vm>`
   - `clean <vm>`
   - `clean --all`
3. Supporto a `firmware=efi` e ramo logico per `firmware=bios`.
4. Artifacts sotto `artifacts/<vm>/`.
5. `Makefile` frontend che chiama `vmctl`.
6. `README.md` con documentazione d'uso.

## Current Gaps

Punti ancora aperti dopo la V1:

- manca un profilo BIOS reale da testare
- nessuna validazione forte dello schema JSON
- gli script legacy sono ancora presenti in root
- `vtoyboot` e copia su Ventoy non sono ancora integrati in `vmctl`
- niente log seriali persistenti
- niente override CLI per RAM, CPU e disco

## V2 Ideas

Estensioni possibili dopo la V1:

- profili multipli;
- un profilo BIOS reale, ad esempio Debian;
- preset video `safe`, `std`, `virtio-gl`;
- log seriali persistenti;
- snapshot runtime separati;
- hook pre/post start;
- supporto a `vtoyboot`;
- esportazione verso Ventoy;
- template JSON condivisibili;
- validazione schema JSON.

## Open Design Questions

Domande da chiudere prima o durante lo sviluppo:

- i file ISO vanno tutti sotto `isos/` o lasciamo path arbitrari?
- vogliamo consentire override CLI per RAM/CPU/video?
- i formati disco supportati inizialmente sono solo `vpc` e `qcow2` oppure anche `raw`?
- il supporto Ventoy entra nel core di `vmctl` o resta modulo/plugin separato?
- vogliamo registrare log di boot sempre, oppure solo in modalita debug?

## Next Steps

Prossimi step consigliati:

1. aggiungere un secondo profilo reale `bios`, ad esempio `debian-bios`
2. spostare gli script legacy in `legacy/` oppure rimuoverli
3. aggiungere una validazione minima dei campi obbligatori in `vmctl`
4. introdurre logging seriale opzionale sotto `artifacts/<vm>/logs/`
5. decidere se integrare `setup-vtoyboot` e `copy-to-ventoy` in `vmctl`
6. valutare override CLI per `--memory`, `--cpus`, `--iso`, `--video`

## Practical Recommendation

Ordine pragmatico per il prossimo sviluppo:

1. aggiungere profilo BIOS reale
2. testare davvero il ramo `bios`
3. ripulire i file legacy
4. aggiungere validazione JSON
5. integrare funzioni Ventoy solo dopo che il core VM e stabile

## Expected Outcome

Se il refactor viene bene, il risultato finale sara:

- piu pulito del setup attuale;
- estendibile a molti guest;
- meno fragile di una collezione di script ad hoc;
- adatto a diventare un piccolo laboratorio locale per test VM.
