# openSUSE Tumbleweed with AutoYaST

Profile: `opensuse-tumbleweed-autoyast` (SSH 2247). The kvm-lab Tumbleweed recipe on plain
QEMU, through the `bootstrap-autoyast` command. Typical time: 30-45 minutes, most of it the
4.7 GB DVD download.

## 1. Specific prerequisites

Only `xorriso` (for the seed CD) beyond the common prerequisites of guide 00. The ISO is
`openSUSE-Tumbleweed-DVD-x86_64-Current.iso`, fetched automatically. It is a rolling image:
the same URL gives a different build every week, which is exactly what makes this profile a
good weekly smoke test.

## 2. The command

```bash
vmctl bootstrap-autoyast opensuse-tumbleweed-autoyast
vmctl attach opensuse-tumbleweed-autoyast          # to watch the installer
```

## 3. What happens, step by step

1. **Seed CD**: the profile renders an AutoYaST XML profile into a CD labelled `AUTOINST`.
2. **Boot**: `boot/x86_64/loader/linux` and the matching initrd are extracted from the DVD
   and booted with `autoyast=cd:///autoinst.xml ifcfg=*=dhcp netsetup=dhcp textmode=1` and
   the serial console. Both media are attached as **SATA** CD-ROMs, because linuxrc only
   scans `/dev/sr*`: on a virtio CD the answer file would be invisible.
3. **Install**: YaST partitions the disk (GPT, 512 MB EFI + btrfs root), installs the
   patterns (`enhanced_base`, `gnome`, `kvm_server`) and the packages of the profile.
4. **Chroot script**: inside the installed system it creates the user and its group, the
   passwordless sudo drop-in, the SSH authorized key, the GDM autologin, enables `sshd` and
   a getty on `ttyS0`. Then `sync`, `blockdev --flushbufs`, and only then the completion
   token `==> AutoYaST install complete!` on the serial.
5. **Reboot**: YaST reboots, QEMU was started with `-no-reboot` and exits by itself. The host
   then starts the installed VM headless and runs the SSH post-install.

The order in step 4 is the project invariant: flush first, token second, guest powers itself
off last. Never print the token earlier.

## 4. Customisation

```json
"autoyast_config": {
  "username": "YOUR_USER", "password_hash": "$6$...",
  "keyboard_layout": "italian", "language": "it_IT", "timezone": "Europe/Rome",
  "patterns": ["enhanced_base", "kde"], "packages": ["openssh", "vim"],
  "enable_services": ["NetworkManager", "sshd"],
  "chroot_commands": ["zypper --non-interactive install htop"]
}
```

`patterns` is where you swap desktop: `gnome`, `kde`, `xfce`. `chroot_commands` are appended
to the chroot script before the flush, so they run inside the installed system with the
network still unconfigured; use `zypper` only for packages already on the DVD.

## 5. Check

```bash
vmctl shell opensuse-tumbleweed-autoyast
cat /etc/os-release
systemctl get-default          # graphical.target
systemctl is-active sshd
```

## 6. Doing it by hand

The rendered profile is `artifacts/<vm>/autoyast/autoinst.xml` and the seed CD is next to it.
Any QEMU or libvirt that boots the DVD kernel with `autoyast=cd:///autoinst.xml` and both CDs
reproduces the same install; validating the XML against the YaST RELAX NG schema
(`xmllint --relaxng .../profile.rng`) is the fastest way to debug a rejected profile.
