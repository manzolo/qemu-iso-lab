# Debian 13 server with preseed

Profile `debian-server` (SSH 2228, EFI, 2 GB, 20 GB disk). Flow `bootstrap-preseed` on the
netinst. Typical time: 10-15 minutes.

## 1. Prerequisites

- ISO `isos/debian-13.x-amd64-netinst.iso` (downloaded and validated by `vmctl`).
- `xorriso`, OVMF. In `local.json`: `preseed_config.username`, `password_hash` (or
  `password`) and `ssh_provision.user` agreeing.

## 2. The command

```bash
vmctl bootstrap-preseed debian-server            # --timeout 1800
```

## 3. What happens

1. `preseed.cfg` and `late_command.sh` are rendered into `artifacts/<vm>/preseed/` and
   **injected into the initrd** (`cpio -A` on a single archive, not two concatenated cpio
   files: the d-i loader does not read those reliably).
2. Direct boot with `vmlinuz` and `initrd.gz` extracted from the ISO and kernel line:

        auto-install/enable=true preseed/file=/preseed.cfg priority=critical locale=<locale> language=<language> country=<country> keymap=<keyboard> DEBIAN_FRONTEND=text console=ttyS0,115200

3. No input on the serial: d-i answers everything with the preseed (mirror, guided disk
   partitioning, the profile's `tasks`/`packages`, user). The serial locale must stay ASCII,
   accented characters in the text frontend break the console.
4. `late_command` in the target: the user's SSH key, the sudoers rule, then:

        sync; blockdev --flushbufs /dev/vda /dev/vda1 /dev/vda2 || true
        echo "==> Debian preseed install complete!"
        poweroff -f

5. The VM restarts headless and the post-install runs `post_install_run` over SSH.

## 4. Check

```bash
vmctl shell debian-server
cat /etc/debian_version; systemctl is-active ssh
```

## 5. Doing it by hand

With the netinst ISO and the generated `preseed.cfg`: at the boot menu press `e` (or `Tab`
in BIOS), add the kernel line above (without `console=` if you have a screen) and provide
the file, for instance on a USB key with `preseed/file=/hd-media/preseed.cfg`, or over HTTP
with `preseed/url=`.
