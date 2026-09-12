---
description: Run a command inside a running lab VM over SSH with the project key (never a password prompt)
category: vm
argument-hint: <vm> <command...>
allowed-tools:
  - Bash
  - Read
---

Run a command inside the VM named first in `$ARGUMENTS`; the rest of the arguments is the
command (default: a short health check: `uname -r; uptime; whoami`).

Build the ssh command from the profile, never from memory:

1. `./bin/vmctl show <vm> --json`: `ssh_provision.ssh_host_port` (or `cloud_init.ssh_host_port`),
   `ssh_provision.user`, `ssh_provision.key_type` (`rsa` -> `artifacts/<vm>/ssh/id_rsa`,
   otherwise `artifacts/<vm>/ssh/id_ed25519`, unless `ssh_key` names another file) and the
   optional `ssh_provision.ssh_options` list (legacy guests: `HostKeyAlgorithms=+ssh-rsa`
   and friends), each passed as `-o`.
2. `timeout 60 ssh -F /dev/null -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
   -o BatchMode=yes -o ConnectTimeout=10 -o LogLevel=ERROR <ssh_options...> -i <key>
   -p <port> <user>@127.0.0.1 '<command>'`. `BatchMode=yes` always: a password prompt would
   hang the tool call. Windows guests get the command through cmd.exe, not `sh -lc`.
3. If the port refuses the connection, `./bin/vmctl status <vm>` and say whether the VM is
   running at all. Show the command's output verbatim in a code block, then one line of
   reading.
