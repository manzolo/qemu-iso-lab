# Ubuntu 26.04 con niri via autoinstall

Profili: `ubuntu-niri` (SSH 2222) e `ubuntu-niri-gl` (personalizzabile). Flusso
`bootstrap-unattended`: autoinstall di subiquity più cloud-init per il primo avvio.
Tempo: 15-25 minuti.

## 1. Prerequisiti

- ISO `ubuntu-26.04-live-server-amd64.iso` (scaricata da `vmctl`), OVMF, `xorriso` o
  `genisoimage`/`cloud-localds` per i seed.
- In `local.json` (profilo `-local`): `autoinstall.username`, `realname`, `password_hash`,
  `cloud_init.user`, `ssh_authorized_keys_file`, `ssh_key`, `copy_from_host` per i dotfiles.

## 2. Il comando

```bash
vmctl bootstrap-unattended ubuntu-niri           # --timeout 300 per l'uscita dell'installer
```

Le fasi separate, se vuoi guardarle una per una:

```bash
vmctl prep ubuntu-niri
vmctl install-unattended ubuntu-niri --headless
vmctl start ubuntu-niri --headless --background
vmctl post-install ubuntu-niri
```

## 3. Cosa succede

1. Due seed in `artifacts/<vm>/`: il seed **autoinstall** (`user-data` con la sezione
   `autoinstall`, `meta-data`) e il seed **cloud-init** per il primo avvio, entrambi con
   etichetta `cidata` come richiede la sorgente `nocloud`.
2. Boot diretto con `casper/vmlinuz` e `casper/initrd` estratti dalla ISO, `-no-reboot` e riga
   kernel:

        autoinstall ds=nocloud console=ttyS0,115200n8

3. Nessun input: subiquity legge `user-data` (storage, identità, `install_ssh`, pacchetti) e a
   fine installazione riavvia; con `-no-reboot` QEMU esce e questo è il segnale di
   completamento (non c'è token seriale in questo flusso).
4. `vmctl` avvia il disco installato headless con il seed cloud-init: al primo boot cloud-init
   crea/aggiorna l'utente, installa `packages`, esegue `runcmd` e `write_files`.
5. Post-install via SSH: attesa di `cloud-init status --wait` e della fine di apt, poi
   `copy_from_host` e `post_install_run` (installazione e verifica di niri e DMS).

## 4. Verifica

```bash
vmctl shell ubuntu-niri
lsb_release -d; cloud-init status; niri --version
```

## 5. Rifarlo a mano

Con la ISO live-server: al menu GRUB premi `e`, aggiungi `autoinstall ds=nocloud` alla riga
`linux` e rendi disponibile il seed `cidata` (chiavetta o secondo CD con `user-data` e
`meta-data`). Senza `console=` l'installer usa lo schermo.
