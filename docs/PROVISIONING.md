# Provisioning and guest identity

After a guest is installed, `vmctl` can finish its setup over SSH: copy files
from the host, run commands, and re-run both at any time with
`vmctl post-install <vm>`. This page lists the profile fields involved and how
to keep your personal data out of the tracked catalog.

- [Two provisioning sections](#two-provisioning-sections)
- [Field reference](#field-reference)
- [Files and scripts from the host](#files-and-scripts-from-the-host)
- [Sudo in the guest](#sudo-in-the-guest)
- [Guest identity and local.json](#guest-identity-and-localjson)

## Two provisioning sections

- `cloud_init` is for guests whose installer understands cloud-init (Ubuntu).
  `vmctl start <vm> --cloud-init` generates
  `artifacts/<vm>/cloud-init/{user-data,meta-data,seed.iso}` and attaches the
  seed; the first boot creates the user, installs packages and runs `runcmd`.
- `ssh_provision` is for every other guest (Arch, CachyOS, Debian, AlmaLinux,
  Omarchy): the installer or its answer file creates the user, and `vmctl`
  connects afterwards.

Both support `copy_from_host` and `post_install_run`, and both declare the
forwarded SSH port on the host (`ssh_host_port`) that `vmctl shell` uses.

```bash
vmctl start ubuntu-niri --cloud-init
vmctl post-install ubuntu-niri
vmctl shell ubuntu-niri
```

`post-install` waits for SSH on the forwarded port, writes the sudoers rule if
asked to, copies the `copy_from_host` entries, then runs `post_install_run` in
order. Output goes to `artifacts/<vm>/logs/post-install.*.log`.

## Field reference

| Section | Fields |
|---------|--------|
| `cloud_init` | `hostname`, `user`, `ssh_authorized_keys`, `ssh_authorized_keys_file`, `ssh_key`, `ssh_host_port`, `packages`, `runcmd`, `write_files`, `copy_from_host`, `post_install_run` |
| `ssh_provision` | `hostname`, `user`, `ssh_key`, `ssh_host_port`, `sudo_password`, `copy_from_host`, `post_install_run` |
| `autoinstall` (Ubuntu) | `hostname`, `username`, `realname`, `password_hash`, `timezone`, `keyboard_layout`, `storage_layout`, `install_ssh` |
| `archinstall_config` (Arch, CachyOS) | `hostname`, `username`, `password`, `timezone`, `keyboard_layout`, `locale_lang`, `locale_enc`, `bootloader`, `kernels`, `audio`, `packages`, `bootstrap_chroot_commands`, `inherit_live_pacman_conf`, `live_login_prompt`, `live_shell_prompt`, `live_kernel_append` (derivatives; see [UNATTENDED.md](UNATTENDED.md#cachyos-on-the-same-flow)) |
| `preseed_config` (Debian) | `hostname`, `domain`, `username`, `fullname`, `password_hash` or `password`, `timezone`, `keyboard_layout`, `locale`, `language`, `country`, `mirror_hostname`, `mirror_directory`, `tasks`, `packages`, `late_commands`, `disk_device` |
| `kickstart_config` (AlmaLinux/RHEL/Fedora) | `hostname`, `username`, `fullname`, `password_hash` or `password`, `timezone`, `keyboard_layout`, `locale`, `inst_repo` (`cdrom` or a repository URL), `ignore_missing_packages`, `packages`, `post_commands`, `disk_device`, `selinux`, `firewall` |
| `alpine_config` (Alpine) | `hostname`, `username`, `password_hash`, `timezone`, `keyboard_layout`, `keyboard_variant`, `user_groups`, `ntp`, `disk_device`, `kernel_flavor` (`lts` or `virt`), `kernel_opts`, `packages`, `optional_packages`, `chroot_commands` |
| `omarchy_config` | `hostname`, `username`, `password_hash`, `timezone`, `keyboard_layout`, `locale`, `disk_device`, `encrypt` |

`ssh_key` may be `null`: `vmctl` then generates a key pair under
`artifacts/<vm>/ssh/` and injects the public half through the answer file.

SSH ports must be unique across the merged catalog; `vmctl` refuses to load two
profiles that forward the same host port.

## Files and scripts from the host

Provisioning logic lives in plain shell scripts tracked under
`vms/profile-files/<vm>/`, deployed with `copy_from_host` and executed with
`post_install_run`. This keeps the JSON readable and the logic reviewable:

```json
"copy_from_host": [
  {
    "source": "vms/profile-files/my-vm/bin/my-post-install",
    "dest": "/home/{{user}}/bin/my-post-install",
    "dest_mode": "755"
  },
  {
    "source": "~/.config/niri/",
    "dest": "/home/{{user}}/.config/niri"
  }
],
"post_install_run": ["~/bin/my-post-install"]
```

Directories are copied recursively. Copies are idempotent, so re-running
`post-install` after editing a script is the normal way to iterate on a recipe.

## Sudo in the guest

Unattended flows configure passwordless sudo for the guest user themselves:
the kickstart `%post`, the preseed `late_command`, the Arch install script and
the Alpine chroot step all drop a rule in `sudoers.d`, and the Ubuntu autoinstall user is created with
sudo through the installer.

`ssh_provision.sudo_password` exists for guests whose installer cannot do that
(Omarchy ignores archinstall's `custom_commands`). When present, post-install
first writes `/etc/sudoers.d/vmctl-<user>` in the guest, feeding the password to
`sudo -S` on stdin so it never appears in a command line or a log. The step lives
in the shared post-install path, so it applies to every profile that defines the
field; profiles without it are untouched. Keep real passwords in `local.json`.

## Guest identity and local.json

Tracked profiles are generic on purpose:

- the guest user is `lab` with password `lab` (SHA-512 hash included), enough
  for a throwaway VM;
- wherever the user name appears inside a path, a command or a file body
  (`/home/{{user}}/bin`, `chown {{user}}:{{user}}`, sudoers content) the profile
  writes the placeholder `{{user}}`. At load time `vmctl` replaces it with the
  identity declared by the profile (`ssh_provision.user`, `cloud_init.user`,
  `autoinstall.username`, `archinstall_config.username`,
  `omarchy_config.username`, `preseed_config.username`,
  `kickstart_config.username`, `alpine_config.username`; they must agree).

To use your own name, key and dotfiles, override only the identity fields in
the git-ignored `vms/profiles/local.json`; every `{{user}}` follows:

```bash
make init-local-profile        # copies vms/profiles/local.json.example
```

Then replace `YOUR_USER`, `YOUR_PASSWORD` / `REPLACE_WITH_SHA512_HASH`
(`openssl passwd -6`) and the SSH key paths. `local.json` is deep-merged over
the tracked profiles: dicts merge key by key, lists concatenate, scalars
replace. A minimal override looks like this:

```json
{
  "vms": {
    "arch-dms-local": {
      "archinstall_config": { "username": "andrea", "password": "..." },
      "ssh_provision": {
        "user": "andrea",
        "ssh_key": "~/.ssh/id_ed25519",
        "copy_from_host": [
          { "source": "~/.config/niri/", "dest": "/home/{{user}}/.config/niri" }
        ]
      }
    }
  }
}
```

Never commit a real user name, password or hash into a tracked profile: the
repository is public.
