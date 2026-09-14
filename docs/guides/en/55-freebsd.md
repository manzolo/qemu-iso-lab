# FreeBSD unattended

`freebsd-unattended` installs the public FreeBSD 14.3 disc1 on BIOS/pc with a virtio
UFS disk. Install `xorriso` and `growisofs` on the host. The recipe creates `lab`/`lab`,
sudo, the project SSH key and a serial login. SSH uses `127.0.0.1:2271`.

```sh
vmctl bootstrap-freebsd freebsd-unattended --timeout 1800
vmctl shell freebsd-unattended
vmctl stop freebsd-unattended
vmctl check-vms freebsd-unattended --clean-first --timeout 1800 --report --document
```

The installer timeout is bounded and errors include a FAILED token and the serial log.
The completion token follows sync and UFS unmount; shutdown is natural. The installed
guest is checked with `pgrep`, `freebsd-version`, SSH and sudo. No desktop is installed.
See [the flow notes](../../UNATTENDED.md#freebsd-disc1-bootstrap-freebsd) and
[recorded live results](../../PROFILE_TODO.md). The manual `freebsd` profile remains available.
