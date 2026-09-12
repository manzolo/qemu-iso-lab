---
description: Show the lab at a glance (which VMs run, have data, expose SSH) or one VM in detail
category: vm
argument-hint: [vm]
allowed-tools:
  - Bash
---

Run `./bin/vmctl status $ARGUMENTS` (all VMs when no name is given) and summarise: the VMs
running now with their SSH ports, the ones installed but stopped, the ones with no disk yet.
Keep the table to what the user asked; for one VM add `./bin/vmctl show <vm> --json` facts
that matter (flow, ISO, ports) in a few lines. Do not start or stop anything from here.
