---
description: Boot a lab VM with its display (vmctl start), or tell why it cannot start yet
category: vm
argument-hint: <vm> [--headless]
allowed-tools:
  - Bash
  - Read
---

Boot the VM `$ARGUMENTS` of this repository with its display. Steps:

1. `./bin/vmctl status <vm>` first. If there is no disk with data, do not start anything: say
   so and point at `/vm-unattended <vm>` (or `vmctl provision <vm>` for a manual install).
   If it is already running, say so and stop: `/vm-shot <vm>` shows its screen.
2. Otherwise run `./bin/vmctl start <vm>` with `run_in_background: true` (the GTK window
   opens on the user's desktop; the command returns only when the VM exits). With
   `--headless` in the arguments use `./bin/vmctl start <vm> --headless --background` instead
   and tell the user the SSH port from `vmctl status`.
3. Report in one or two lines what started and how to reach it (`vmctl attach`, `vmctl shell`,
   `vmctl console`). Never leave a VM running silently: name it.
