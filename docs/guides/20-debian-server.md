# Debian 13 server con preseed

Profilo `debian-server` (SSH 2228, EFI, 2 GB, disco 20 GB). Flusso `bootstrap-preseed`
sulla netinst. Tempo tipico: 10-15 minuti.

## 1. Prerequisiti

- ISO `isos/debian-13.x-amd64-netinst.iso` (scaricata e validata da `vmctl`).
- `xorriso`, OVMF. In `local.json`: `preseed_config.username`, `password_hash` (o `password`)
  e `ssh_provision.user` coincidenti.

## 2. Il comando

```bash
vmctl bootstrap-preseed debian-server            # --timeout 1800
```

## 3. Cosa succede

1. `preseed.cfg` e `late_command.sh` vengono generati in `artifacts/<vm>/preseed/` e
   **iniettati nell'initrd** (`cpio -A` su un archivio unico, non due cpio concatenati: il
   loader di d-i non li legge in modo affidabile).
2. Boot diretto con `vmlinuz` e `initrd.gz` estratti dalla ISO e riga kernel:

        auto-install/enable=true preseed/file=/preseed.cfg priority=critical locale=<locale> language=<lingua> country=<paese> keymap=<tastiera> DEBIAN_FRONTEND=text console=ttyS0,115200

3. Nessun input sul seriale: d-i risponde a tutto con il preseed (mirror, partizionamento
   guidato del disco, `tasks`/`packages` del profilo, utente). Il locale del seriale deve
   restare ASCII, i caratteri accentati nel frontend testuale rompono la console.
4. `late_command` nel target: chiave SSH dell'utente, regola sudoers, quindi:

        sync; blockdev --flushbufs /dev/vda /dev/vda1 /dev/vda2 || true
        echo "==> Debian preseed install complete!"
        poweroff -f

5. La VM riparte headless e il post-install esegue `post_install_run` via SSH.

## 4. Verifica

```bash
vmctl shell debian-server
cat /etc/debian_version; systemctl is-active ssh
```

## 5. Rifarlo a mano

Con la ISO netinst e il `preseed.cfg` generato: al menu di boot premi `e` (o `Tab` in BIOS),
aggiungi la riga kernel sopra (senza `console=` se hai lo schermo) e fornisci il file, ad
esempio su una chiavetta con `preseed/file=/hd-media/preseed.cfg`, oppure via HTTP con
`preseed/url=`.
