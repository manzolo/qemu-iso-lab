# legacy/

Prototipi storici sostituiti da `bin/vmctl`. Conservati come riferimento, non
vengono più usati dai workflow correnti.

- `setup-vhd.sh` — creava un disco VHD, copiava OVMF_VARS e generava run-*.sh
  per CachyOS. Oggi: `vmctl prep` e `vmctl install`.
- `run-install.sh` — installer hardcoded CachyOS (EFI + virtio + GTK).
  Oggi: `vmctl install <vm>`.
- `run-boot.sh` — boot del disco già installato. Oggi: `vmctl start <vm>`.

Questi script erano costruiti attorno a un singolo disco `cachyos-30g.vhd`
nella radice del progetto, con percorsi e nomi cablati. Il modello attuale
(profili JSON in `vms/profiles/`, artifact isolati per VM in `artifacts/<vm>/`)
copre lo stesso scenario in modo dichiarativo e per più distro.
