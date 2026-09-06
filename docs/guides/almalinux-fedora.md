# AlmaLinux 10 server e Fedora con niri via kickstart

Profili: `almalinux-server` (SSH 2229, dalla ISO minimal) e `fedora-niri-dms-local`
(SSH 2233, dalla netinst Everything più repository online). Flusso `bootstrap-kickstart`
con Anaconda in modalità testo. Tempo: 10 minuti Alma, 20-30 Fedora.

## 1. Prerequisiti

- ISO `AlmaLinux-10.x-x86_64-minimal.iso` oppure `Fedora-Everything-netinst-x86_64-NN.iso`
  (scaricate da `vmctl`).
- `xorriso`, OVMF. In `local.json`: `kickstart_config.username`, `password_hash` o
  `password`, `ssh_provision.user`.

## 2. Il comando

```bash
vmctl bootstrap-kickstart almalinux-server
vmctl bootstrap-kickstart fedora-niri-dms-local
```

## 3. Cosa succede

1. `ks.cfg` viene generato in `artifacts/<vm>/kickstart/` e impacchettato in una seed ISO con
   etichetta `KS_CFG`, attaccata come CD virtio.
2. Boot diretto con `vmlinuz` e `initrd.img` estratti dalla ISO e riga kernel:

        inst.ks=hd:LABEL=KS_CFG:/ks.cfg inst.text inst.cmdline inst.repo=<cdrom|URL> console=ttyS0,115200

   `inst.repo` è `cdrom` per Alma (pacchetti dalla ISO) e l'URL del repository Fedora per il
   profilo niri (`kickstart_config.inst_repo`), che usa anche `%packages --ignoremissing`.
3. Nessun input sul seriale: Anaconda in `inst.cmdline` non fa domande, se manca qualcosa al
   kickstart si ferma con un errore invece di chiedere. Anche qui il locale della console
   deve restare ASCII.
4. `%post` nel target: chiave SSH, sudoers, SELinux permissive dove previsto, i
   `post_commands` del profilo (per Fedora: COPR `avengemedia/danklinux` per quickshell,
   `dnf upgrade` perché quickshell è compilato contro Qt 6.11 di `updates`, greetd), quindi:

        sync; blockdev --flushbufs /dev/vda /dev/vda1 /dev/vda2 || true
        echo "==> Kickstart install complete!"
        poweroff -f

5. Post-install via SSH con i `post_install_run` del profilo.

## 4. Verifica

```bash
vmctl shell almalinux-server
cat /etc/os-release | head -2; sudo dnf -q repolist
```

## 5. Rifarlo a mano

Al menu di boot della ISO aggiungi la riga kernel sopra puntando `inst.ks=` al tuo `ks.cfg`
(file su una chiavetta con etichetta, oppure `inst.ks=http://...`). Senza `inst.cmdline`
Anaconda mostra il menu testuale e chiede conferma dove il kickstart è incompleto.
