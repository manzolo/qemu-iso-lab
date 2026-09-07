# The Ubuntu desktop flavors (Lubuntu, Kubuntu, Xubuntu, MATE, Budgie)

Profiles: `lubuntu-24.04` (SSH 2240), `kubuntu-24.04` (2241), `xubuntu-24.04` (2242),
`ubuntu-mate-24.04` (2243), `ubuntu-budgie-24.04` (2244). One recipe, five desktops, the
kvm-lab flavor family on plain QEMU. Typical time: 30-40 minutes each.

## 1. The idea

There is no separate installer per flavor. All five install from the **same** Ubuntu Server
24.04.4 ISO through `bootstrap-unattended`, and the desktop is chosen by one line of the
answer file: the metapackage in `autoinstall.packages`. What changes per flavor is only

- the metapackage (`lubuntu-desktop`, `kubuntu-desktop`, `xubuntu-desktop`,
  `ubuntu-mate-desktop`, `ubuntu-budgie-desktop`),
- the display manager it ships (SDDM for Lubuntu and Kubuntu, LightDM for the other three),
- the session name the autologin drop-in asks for (`Lubuntu`, `plasma`, `xubuntu`, `mate`,
  `ubuntu-budgie-desktop`).

So the ISO is downloaded once and shared by all five, and adding a sixth flavor is a profile,
not code.

## 2. The command

```bash
vmctl bootstrap-unattended kubuntu-24.04
vmctl attach kubuntu-24.04             # in another terminal, to watch the installer
```

Expect the installer to spend most of the time downloading the metapackage: the server ISO
carries no desktop, so the packages come from the archive over the slirp NIC.

## 3. What the profile sets up

```json
"autoinstall": { "packages": ["kubuntu-desktop", "spice-vdagent", "qemu-guest-agent"] },
"cloud_init": {
  "write_files": [
    { "path": "/etc/sudoers.d/90-{{user}}-nopasswd", "content": "{{user}} ALL=(ALL) NOPASSWD:ALL\n" },
    { "path": "/etc/sddm.conf.d/vmctl-autologin.conf", "content": "[Autologin]\nUser={{user}}\nSession=plasma\n" }
  ],
  "runcmd": ["systemctl set-default graphical.target", "systemctl enable sddm",
             "systemctl enable --now serial-getty@ttyS0.service"]
}
```

The getty on `ttyS0` is what makes `vmctl console kubuntu-24.04` a login prompt while the VM
runs in the background; see guide 05.

## 4. Check

```bash
vmctl shell kubuntu-24.04
systemctl get-default                  # graphical.target
systemctl is-enabled sddm              # enabled
dpkg-query -W -f='${Status}\n' kubuntu-desktop
```

The post-install runs exactly these three commands, so a green `check-vms` row already means
the desktop is installed and the display manager is enabled.

## 5. Another version, another flavor

Copy one profile and change three fields: `iso`/`iso_url` (any `ubuntu-<version>-live-server-amd64.iso`),
the metapackage and the session. 22.04 and 26.04 work the same way; kvm-lab keeps all three
versions of all five flavors, this repo tracks the current LTS to keep the validation matrix
short. Personal variants belong in `local.json`, never in a tracked profile.

Post-install now fails unless graphical.target is the default, the desktop metapackage is installed, the display manager is active and the configured user has an active local graphical session. An active greeter alone is not a successful autologin. The check waits up to roughly two minutes for session startup; no live verification was performed for this change.
