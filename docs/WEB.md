# The web dashboard (`vmctl web`)

```bash
make web            # or: vmctl web --open   (PORT=9000 make web / --port 9000; --port 0 picks a free one)
```

`vmctl web` serves the lab in a browser on **127.0.0.1 only** and prints a URL with a random
token. It drives the same backend as `vmctl` and `vmtui`:

- **Profiles**: every profile with its live state, search (`/`) and the filters of the dashboard
  (All, With disk, Running, Labs). The panel on the right shows the facts and the actions that make
  sense for the selected VM: *Unattended install* (the flow `check-vms` would run), *Get the ISO…*
  for a medium only you can provide, *Download the ISO*, *Boot headless*, *Boot with display*
  (a QEMU window on the host), *Console*, *Screenshot*, *Open viewer on the host*, *Stop*,
  *Checkpoint now*, *Clean*. Enter runs the first one.
- **Console**: the VM's own screen with keyboard and mouse, in the page (noVNC), for any VM that
  runs headless: `Boot headless`, and every unattended install while it runs (*Watch the install*).
  The server bridges a WebSocket to the VM's `runtime/vnc.sock`, the socket `vmctl attach` uses.
  Ctrl+Alt+Del and full screen are buttons; Esc goes to the VM, so the dialog closes with *Close*.
  `…/#console=<vm>` after the URL opens a VM's console directly. The noVNC client is the one
  file fetched from outside (cdn.jsdelivr.net, `@novnc/novnc@1.7.0`); the screenshot view needs
  nothing.
- **All commands…**: a palette in two panes. *For this VM* lists only the commands that make
  sense for the selected profile (of the 19 `bootstrap-*` only its own flow, `install-archinstall`
  only on Arch profiles, `post-install` only with SSH...), *Global* the ones that take no VM
  (`status`, `setup`, `check-vms`, `group`...). The filter takes the keyboard (arrows, Enter runs);
  on the right, the chosen command's options built from the CLI's own parser, with the VM fixed
  and the command line it will run. A new subcommand appears there without any web code.
  `…/#commands=<vm>` opens it directly.
- **Labs**: each lab with its members, and install, start, stop, status, cluster, network map
  (opened in a new tab) and clean.
- **Jobs**: everything runs as a detached job with its log followed live. A job started for a VM
  uses the same `artifacts/<vm>/runtime/tui-job` as the TUI, so `vmtui` shows installs started
  in the browser and the other way round; jobs keep running when the page or the server closes.
  A running job can be cancelled (a VM it started is stopped; disk and logs are kept).

![vmctl web: profiles, the selected VM's actions, jobs](screenshots/web-dashboard.png)

## Safety

The page can delete disks and start anything `vmctl` can, so:

- the server binds 127.0.0.1 and refuses a request whose `Host` header is not `127.0.0.1:<port>`
  or `localhost:<port>` (no DNS rebinding);
- every API call needs the token printed at start (the page keeps it in `sessionStorage` and
  drops it from the address bar);
- commands that need a terminal or sudo are not offered: `shell`, `console`, `flash`,
  `import-device` (and `web` itself);
- destructive commands (`clean`, `delete-iso`, `clean-reports`, `checkpoint restore|delete`,
  `group clean|install`, `check-vms --clean-first`...) ask in the browser, and the server refuses
  them without that confirmation; a confirmed command that has a `--yes` gets it, because a job
  has no terminal to answer on.

## API

| Method | Path | What |
|---|---|---|
| GET | `/api/state` | dashboard rows (the TUI snapshot) and labs; `?fresh=1` skips the 4 s cache |
| GET | `/api/commands` | the command catalog: group, help, arguments (kind, flag, choices, required) |
| POST | `/api/run` | `{"args": ["start", "vm", "--headless"], "confirmed": false}` → `{"job", "command"}` |
| GET | `/api/jobs` | recent jobs, running first |
| GET | `/api/jobs/<id>/log?offset=N` | the log from byte N, the next offset and the job status |
| POST | `/api/jobs/<id>/cancel` | stop a running job |
| GET | `/api/vm/<vm>/show` | `vmctl show <vm> --json` |
| GET | `/api/vm/<vm>/screen.png` | a screenshot of a running headless VM (QMP screendump) |
| GET (WebSocket) | `/api/vm/<vm>/vnc?token=` | the VM's VNC socket, relayed both ways (noVNC in the page) |
| GET | `/labs/<group>/map` | the lab's network map page (`vmctl group map <group>` writes it) |

Every call sends the token as `X-Vmctl-Token` (or `?token=` for images and the map tab).

## Next steps

- **The serial console in the browser**: xterm.js over `runtime/serial.sock` (`vmctl console`),
  and noVNC served from the repository for hosts without internet access.
- **One Python layer for the facts and the actions**: part of them still lives in `bin/vmtui`
  (bash with embedded Python); moving it into a module would serve the TUI, the web and the CLI.
- **Profiles as plugins**: today every profile is an entry of `vms/profiles/*.json`. The idea is
  a catalog that can be downloaded: an index of profiles (a whole catalog, or a single VM) with
  their version, checksum and the flow they need, installed into a local directory that
  `load_config()` merges like `local.json`. The web and the TUI would list what is available,
  install it and keep it up to date.
