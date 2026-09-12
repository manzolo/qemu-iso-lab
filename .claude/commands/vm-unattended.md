---
description: Run the unattended install of a lab VM in the background with the right bootstrap flow, watch it, report PASS/FAIL with evidence
category: vm
argument-hint: <vm> [--clean] [--timeout SEC]
allowed-tools:
  - Bash
  - Read
  - Monitor
---

Install the VM `$ARGUMENTS` unattended, the way the maintainer does it by hand:

1. Pick the flow from the profile: `./bin/vmctl show <vm> --json` and the section it carries:
   `autoinstall` -> `bootstrap-unattended`, `preseed_config` -> `bootstrap-preseed`,
   `kickstart_config` -> `bootstrap-kickstart`, `autoyast_config` -> `bootstrap-autoyast`,
   `archinstall_config` -> `bootstrap-archinstall`, `alpine_config` -> `bootstrap-alpine`,
   `omarchy_config` -> `bootstrap-omarchy`, `windows_config` -> `bootstrap-windows`,
   `pfsense_config` -> `bootstrap-pfsense`, `reactos_config` -> `bootstrap-reactos`.
   A profile with none of them is `manual`: say so and stop.
2. Refuse to start if `vmctl status <vm>` shows it running. With `--clean`, or when the disk
   already has data and the user wants a fresh install, run `./bin/vmctl clean <vm>` first.
3. Run the bootstrap in the background, unbuffered, with its log in the scratchpad:
   `PYTHONUNBUFFERED=1 ./bin/vmctl <flow> <vm> --timeout <SEC, default 3600> > <scratchpad>/bootstrap-<vm>.log 2>&1; echo "exit=$?" >> <the log>`
   (`run_in_background: true`). The default 300 s timeout of some flows is a false FAIL on a
   desktop install; always pass `--timeout`.
4. Arm a Monitor on that log for the lines that matter: `exit=`, `[ok]`, `error: `, `Prompt:`,
   `default=` (a d-i prompt nobody will answer), `Desktop verification`, `FAILED`,
   `Timed out`, `Bootstrap complete`. Filter out `SQUASHFS error` (the CD eject at the end
   of an Ubuntu autoinstall) and the OpenSSH post-quantum warnings.
5. While it runs, do not poll; `/vm-shot <vm> --no-wake` shows the installer screen when a
   prompt is suspected (the QMP socket exists during a bootstrap too).
6. On `exit=0` report PASS with the guest facts that prove it (the post-install lines). On a
   failure quote the first real error from the log or the serial log under
   `artifacts/<vm>/logs/`, name the stage, and do not retry blindly: diagnose first.
