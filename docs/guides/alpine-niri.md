# Alpine Linux 3.23 con niri

Profilo `alpine-niri` (SSH 2234, BIOS, 4 GB, disco 16 GB). Flusso `bootstrap-alpine`:
`setup-alpine` con answer file, poi un chroot per pacchetti e configurazione. Tempo: 5 minuti.

## 1. Prerequisiti

- ISO `alpine-standard-3.23.x-x86_64.iso`, trovata sull'indice di dl-cdn da `iso_discovery`.
  Il profilo è fissato alla 3.23: la Mesa della 3.24 non ha virgl e niri, che rifiuta il
  rendering software, non prende mai lo schermo con `virtio-vga-gl`.
- `xorriso`. In `local.json`: `alpine_config.username`, `password_hash` e `ssh_provision.user`.

## 2. Il comando

```bash
vmctl bootstrap-alpine alpine-niri
```

## 3. Cosa succede

1. Seed ISO `ALPINESEED` in `artifacts/<vm>/alpine/` con `answers` (per `setup-alpine -f`),
   `install.sh` e `run.sh`; è un CD virtio, nel live `/dev/vdb`.
2. Boot diretto con `boot/vmlinuz-lts` e `boot/initramfs-lts` e la riga kernel del live più
   virtio e seriale:

        modules=loop,squashfs,sd-mod,usb-storage,virtio_blk,virtio_pci console=tty0 console=ttyS0,115200 quiet

3. Login e trigger sul seriale:

        localhost login: root
        localhost:~# modprobe virtio_blk 2>/dev/null; mdev -s 2>/dev/null; mkdir -p /media/vmctl-seed && mount -t iso9660 /dev/vdb /media/vmctl-seed && sh /media/vmctl-seed/run.sh

4. `install.sh`: esporta `ERASE_DISKS=/dev/vda` e lancia `setup-alpine -e -f answers`
   (tastiera, hostname, udev, DHCP, mirror più repository community, utente con chiave SSH,
   sshd, chrony, `setup-disk -m sys`); rimonta la root installata e in chroot installa i
   `packages` (niri, foot, fuzzel, greetd, pam-rundir, le librerie Wayland esplicite, Mesa),
   uno per uno gli `optional_packages`, imposta la password, sudo per `wheel`, dbus e seatd,
   poi i `chroot_commands` (greetd con autologin in `dbus-run-session -- niri --session`).
5. Chiusura: smonta, `sync`, `blockdev --flushbufs`, stampa
   `==> Alpine Linux installation complete!` e `poweroff -f`.
6. Post-install via SSH: `config.kdl` di niri, `niri validate`, controllo che la sessione giri.

## 4. Verifica

```bash
vmctl shell alpine-niri
cat /etc/alpine-release; rc-status default | head; pgrep -x niri
```

## 5. Trappole note

- Senza elogind nulla imposta `XDG_RUNTIME_DIR`: serve `pam-rundir` in `/etc/pam.d/greetd`.
- Il pacchetto niri non dichiara le librerie Wayland che carica a runtime:
  `wayland-libs-server` e `wayland-libs-client` vanno elencati (altrimenti `NoWaylandLib`).
- greetd esegue `initial_session` una sola volta per boot (`/run/greetd.run`): riavviare il
  servizio mostra il greeter, non l'autologin.
