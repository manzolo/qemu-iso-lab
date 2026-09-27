---
description: Reinstall a lab VM unattended while recording its screen, and hand back the time-lapse (GIF, and MP4 with --mp4)
category: vm
argument-hint: <vm> [--keep-disk] [--mp4]
allowed-tools:
  - Bash
  - Read
  - Monitor
---

Record the unattended installation of `$ARGUMENTS` as a time-lapse. Work from the repository
root and keep every temporary file in the scratchpad.

1. Pick the flow as `/vm-unattended` does (`./bin/vmctl show <vm> --json`, the config section
   names the `bootstrap-*` command; a manual profile cannot be recorded this way: say so and
   stop). Refuse if `./bin/vmctl status <vm>` shows it running.
2. Unless `--keep-disk` was given, `./bin/vmctl clean <vm>` so the recording starts from an
   empty disk (the ISO stays cached).
3. Start the bootstrap detached, unbuffered, log in the scratchpad:
   `PYTHONUNBUFFERED=1 setsid nohup ./bin/vmctl <flow> <vm> --timeout 3600 > <scratchpad>/record-<vm>.log 2>&1 < /dev/null &`
4. Wait until `artifacts/<vm>/runtime/qmp.sock` exists (a few seconds), then start the recorder
   the same way: `setsid nohup ./bin/vmctl record <vm> --grace 45 > <scratchpad>/recorder-<vm>.log 2>&1 < /dev/null &`
   (add `--mp4` when the user asked for the video too).
   `--grace 45` keeps one recording across the installer's power-off and the first boot of the
   installed disk (the post-install boots it headless right away).
5. Arm a Monitor on the bootstrap log for `exit=|Bootstrap complete|FAILED|Timed out|error: `
   (the bootstrap ends with `Bootstrap complete` and leaves the VM running for install-only
   flows it powers off by itself). When it ends, `./bin/vmctl stop <vm>` if the VM still runs:
   the recorder then sees the socket go, waits its grace and encodes.
6. Wait for the recorder log to print `gif` (and `mp4`), then read the last frame under
   `artifacts/<vm>/recording/latest/frames/` to check it is the installed system, and report: the
   path and size of `recording.gif` (and the MP4), how many frames were kept out of how many
   captures, and PASS/FAIL of the install itself with the evidence line. If the user wants it on
   the catalog site: `tools/collect_media.py <vm>` copies the GIF into `docs/media/<vm>/`.
   Do not delete anything; the disk stays installed.
