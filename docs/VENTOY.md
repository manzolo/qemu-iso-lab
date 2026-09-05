# Ventoy utilities

Two helpers under `bin/` let a guest disk travel to a Ventoy USB key,
independently from the main VM workflow. They are off the `vmctl` path and only
matter for the Ventoy multi-boot scenario.

| Tool | Where it runs | What it does |
|------|---------------|--------------|
| `bin/ventoy-prep` | as root **inside the guest** | downloads the `vtoyboot` ISO, extracts it and runs `vtoyboot.sh`, so the guest disk becomes Ventoy-bootable |
| `bin/ventoy-copy <target> <file.vhd>` | as root **on the host** | copies a `.vhd` to a Ventoy partition or mountpoint, appending the `.vtoy` suffix Ventoy requires |

Profiles meant for this use `"format": "vhd"` in their `disk` section
([PROFILES.md](PROFILES.md#disk)), for example `cachyos`.
