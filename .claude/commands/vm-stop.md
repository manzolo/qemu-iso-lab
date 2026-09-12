---
description: Stop a running lab VM gracefully (vmctl stop), then confirm it is down
category: vm
argument-hint: <vm> | --all
allowed-tools:
  - Bash
---

Stop the VM `$ARGUMENTS` with `./bin/vmctl stop <vm>` (ACPI power-off through QMP, SSH
fallback, then SIGTERM after the profile's grace period; Windows profiles need up to 300 s,
do not shorten it). With `--all`, list the running ones with `./bin/vmctl status` and stop
each. Never `kill -9` a QEMU whose guest may be writing: a bootstrap in progress is cancelled
with `./bin/vmctl cancel-install <vm>`, and only then stopped. Confirm with `vmctl status`
and report which VMs are still running, if any.
