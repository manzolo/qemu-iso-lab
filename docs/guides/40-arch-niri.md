# Arch Linux con niri (DankMaterialShell o Noctalia) e CachyOS

Profili: `arch-dms-local` (SSH 2230), `arch-noctalia-local` (2226), `cachyos-local` (2223).
Flusso `bootstrap-archinstall`: uno script `pacstrap` autonomo sulla ISO live, senza
`archinstall`. Tempo tipico: 15-30 minuti, dipende dai mirror.

## 1. Prerequisiti

- ISO Arch (`isos/archlinux-latest-x86_64.iso`, scaricata e validata da `vmctl`) o CachyOS
  (`cachyos-desktop-linux-latest.iso`, trovata sul mirror tramite `iso_discovery`).
- Host con OVMF (profili EFI) e `xorriso`.
- In `local.json`: `archinstall_config.username`/`password` e `ssh_provision.user` con lo stesso
  nome, eventuali dotfiles in `copy_from_host`, `shared_dir` se vuoi la cartella condivisa.

## 2. Il comando

```bash
vmctl bootstrap-archinstall arch-dms-local       # --timeout 1800 di default
vmctl attach arch-dms-local                      # facoltativo
```

## 3. Cosa succede

1. **Seed** `bootstrap.iso` in `artifacts/<vm>/archinstall/`: `install.sh` (sgdisk, pacstrap,
   arch-chroot, GRUB, utente, servizi) e `run.sh` che lo avvia. Viene attaccato come CD virtio,
   quindi nel live è `/dev/vdb`.
2. **Boot diretto** con kernel e initramfs estratti dalla ISO
   (`arch/boot/x86_64/vmlinuz-linux` e `initramfs-linux.img`) e riga kernel:

        archisobasedir=arch archisolabel=ARCH_YYYYMM console=ttyS0,115200 quiet

3. **Login e trigger sul seriale**, digitati dall'automazione (e da te, se lo fai a mano):

        archiso login: root
        root@archiso ~ # mkdir -p /tmp/archconf && mount /dev/vdb /tmp/archconf && bash /tmp/archconf/run.sh

4. **install.sh** nel live: partiziona `/dev/vda` (ESP + root), `pacstrap` con i pacchetti del
   profilo (niri, quickshell/DMS o Noctalia, sddm/greetd, NVIDIA open dove previsto), in
   `arch-chroot` imposta locale, fuso, utente e password, sudo senza password, `NetworkManager`,
   `sshd`, GRUB su UEFI, poi i `bootstrap_chroot_commands` del profilo.
5. **Chiusura**, sempre in quest'ordine:

        sync
        blockdev --flushbufs /dev/vda /dev/vda1 /dev/vda2 || true
        echo "==> Arch Linux installation complete!"
        poweroff -f

6. **Post-install** via SSH: copia dei dotfiles (`copy_from_host`, ad esempio `~/.config/niri`
   e `~/.config/DankMaterialShell`), poi i `post_install_run` (`niri validate`, controlli dei
   servizi, cartella condivisa).

## 4. CachyOS sullo stesso flusso

Cambiano solo i dettagli letti dal profilo: kernel `vmlinuz-linux-cachyos` e
`initramfs-linux-cachyos.img`, etichetta `COS_YYYYMM`, riga kernel con in più
`systemd.unit=multi-user.target` (così Plasma live e Calamares non partono), prompt
`CachyOS login:` e `root@CachyOS`, e `inherit_live_pacman_conf` che copia nel target il
`pacman.conf` del live con il repo `[cachyos]`. Il desktop vero è il metapacchetto
`cachyos-niri-noctalia` con `sddm`: senza, niri parte ma lo schermo resta grigio.

## 5. Verifica e uso

```bash
vmctl shell arch-dms-local
niri --version && systemctl --user status dms 2>/dev/null | head -3
ls ~/shared ~/Desktop/shared      # cartella condivisa, se configurata
```

`vmctl start arch-dms-local` apre la sessione grafica con `virtio-vga-gl`; i profili NVIDIA
richiedono la GPU passata al guest e non sono coperti da questa guida.

## 6. Note

- Il flusso resta valido solo se la chiusura di `install.sh` non viene toccata: token dopo il
  flush e attesa dello spegnimento naturale (vedi `docs/ARCH_GRUB_BOOT_FIX.md`).
- I compositori niri disattivano la gestione del tasto di accensione
  (`input { disable-power-key-handling }`), altrimenti `vmctl stop` sospenderebbe la VM
  invece di spegnerla.
