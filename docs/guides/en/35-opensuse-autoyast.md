# openSUSE Tumbleweed with AutoYaST

Profile: `opensuse-tumbleweed-autoyast` (SSH 2247). The kvm-lab Tumbleweed recipe on plain
QEMU, through the `bootstrap-autoyast` command. Typical time: 30-45 minutes, most of it
package downloads.

## 1. Specific prerequisites

Only `xorriso` (for the seed image) beyond the common prerequisites of guide 00. The ISO is
`openSUSE-Tumbleweed-NET-x86_64-Current.iso`, about 250 MB, fetched automatically: it boots
the installer and takes the packages from the online `oss` repository. It is a rolling image:
the same URL gives a different build every week, which is exactly what makes this profile a
good weekly smoke test.

## 2. The command

```bash
vmctl bootstrap-autoyast opensuse-tumbleweed-autoyast
vmctl attach opensuse-tumbleweed-autoyast          # to watch the installer
```

## 3. What happens, step by step

1. **Seed**: the profile renders an AutoYaST XML profile into an image labelled `AUTOINST`,
   attached as a **USB stick**.
2. **Boot**: `boot/x86_64/loader/linux` and the matching initrd are extracted from the ISO
   and booted with `install=<repo> autoyast=usb:///autoinst.xml ifcfg=*=dhcp netsetup=dhcp
   textmode=1` and the serial console. The installer ISO is the **only CD-ROM** and it is a
   SATA one: linuxrc scans `/dev/sr*` for the repository, so a virtio CD would be invisible,
   while a *second* CD makes YaST stop mid-install asking which drive holds Disc 1.
3. **Install**: YaST partitions the disk (GPT, 512 MB EFI + btrfs root), installs the
   patterns (`enhanced_base`, `gnome`, `kvm_server`) and the packages of the profile.
4. **Chroot script**: inside the installed system it creates the user and its group, the
   passwordless sudo drop-in, the SSH authorized key, the GDM autologin, enables the
   services (`NetworkManager`, `sshd`, the guest agent, the display manager), sets the
   graphical target, puts a getty on `ttyS0` and opens SSH in firewalld. Then `sync`,
   `blockdev --flushbufs`, and only then the completion token
   `==> AutoYaST install complete!` on the serial.

   Two of those steps exist because of what happens without them. openSUSE's firewalld
   opens `dhcpv6-client` and nothing else, so sshd listens, the forwarded port connects and
   the session dies at the banner exchange. And the profile disables the **YaST second
   stage** (`second_stage: false`): it would run on tty1 at first boot and, since the host
   ends the install at the guest's reboot, announce "The previous installation has failed.
   Would you like it to continue?" to whoever opens the VM. With it off, the chroot script
   above is what configures the system, and the first boot goes straight to GNOME.
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

`install_repo` is the installation source on the kernel line; with a full DVD as the `iso`
you can drop it, but then the DVD must be the only CD in the machine. `patterns` is where you
swap desktop: `gnome`, `kde`, `xfce`. `chroot_commands` are appended
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
