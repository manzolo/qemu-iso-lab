# The web dashboard (`vmctl web`)

```bash
make web            # or: vmctl web --open   (PORT=9000 make web / --port 9000; --port 0 picks a free one)
```

`vmctl web` serves the lab in a browser on **127.0.0.1 only** and prints a URL with a random
token. It drives the same backend as `vmctl` and `vmtui`. To see one VM through its whole
lifecycle from the page, screen by screen, read [From zero to a running VM](FIRST_VM.md).

What the page offers:

- **Profiles**: every profile with its live state, search (`/`) and the filters of the dashboard
  (All, With disk, Running, Labs). The panel on the right shows the facts and the actions that make
  sense for the selected VM: *Unattended install* (the flow `check-vms` would run), *Get the ISO…*
  for a medium only you can provide, *Download the ISO*, *Boot headless*, *Boot with display*
  (a QEMU window on the host), *Console*, *Screenshot*, *Open viewer on the host*, *Stop*,
  *Checkpoint now*, *Clean*. Enter runs the first one. Right-click a row (or Shift+F10)
  to open the same actions, SSH access and profile customization. This also works on VM
  names, addresses and state badges inside lab cards.
- **Customize**: change memory and vCPUs, or edit a partial JSON override. The catalog stays
  unchanged; edits go to the ignored `vms/profiles/local.json`, with the previous document in
  `local.json.bak`. The complete candidate configuration is validated before writing, and an
  editor opened before another change cannot overwrite it. Objects merge recursively and
  arrays append, matching the CLI. Restore catalog values removes only this VM's override;
  profiles created entirely locally can be edited but have no catalog defaults to restore.
  Resource changes apply at the next VM start; editing disk size does not resize existing disks.
- **SSH**: the button beside the SSH address opens a browser terminal, a terminal window on
  the host, or copies `bin/vmctl shell <vm>`. Browser SSH uses an interactive PTY and the profile's
  existing SSH credentials. Input, password prompts and resizing work as in a normal terminal.
  Close ends that SSH client without stopping the VM. The browser terminal uses
  [xterm.js](https://xtermjs.org/docs/) 5.5.0 and addon-fit 0.10.0, loaded from cdn.jsdelivr.net.
- **Console**: the VM's own screen with keyboard and mouse, in the page (noVNC), for any VM that
  runs headless: `Boot headless`, and every unattended install while it runs (*Watch the install*).
  The server bridges a WebSocket to the VM's `runtime/vnc.sock`, the socket `vmctl attach` uses.
  The toolbar has fit-to-window/actual-size, reconnect, Ctrl+Alt+Del and full screen controls.
  The connection status stays visible; Esc goes to the VM, so the dialog closes with *Close*.
  `…/#console=<vm>` after the URL opens a VM's console directly. The noVNC client is loaded
  from cdn.jsdelivr.net (`@novnc/novnc@1.7.0`); the screenshot view needs no external assets.
- **Commands** (`C`): a palette in two panes. *This VM* lists only the commands that make
  sense for the selected profile (of the 19 `bootstrap-*` only its own flow, `install-archinstall`
  only on Arch profiles, `post-install` only with SSH...), *Global* the ones that take no VM
  (`status`, `setup`, `check-vms`, `group`...). Suggested commands come first, followed by recently
  used commands. Search by name, description or flag, or filter by category. Arrow keys select;
  Enter in search moves to the form. Only **Run command** or **Ctrl/Cmd+Enter** executes it.
  The form comes from the CLI parser, with required fields, collapsible options, a fixed command
  preview and a copy button. Draft parameters survive switching commands during the page session.
  A new subcommand appears there without any web code.
  `…/#commands=<vm>` opens it directly.
- **Labs**: each lab with its members, and install, start, stop, status, cluster, network map
  (opened in a new tab) and clean. An installed lab offers **Start stack** first (or **Stop stack**
  when all members are running); a lab with missing installations offers **Install lab**.
- **Jobs**: everything runs as a detached job with its log followed live. A job started for a VM
  uses the same `artifacts/<vm>/runtime/tui-job` as the TUI, so `vmtui` shows installs started
  in the browser and the other way round; jobs keep running when the page or the server closes.
  A running job can be cancelled (a VM it started is stopped; disk and logs are kept).
  VM badges distinguish starting, running, stopping and installing using the recorded command;
  a running job alone does not mean an installation is taking place.

![vmctl web: profiles, the selected VM's actions, jobs](screenshots/web-dashboard.png)

![Labs in the web dashboard: member addresses, live VM states and stack controls](screenshots/web-labs.png)

## Safety

The page can delete disks and start anything `vmctl` can, so:

- the server binds 127.0.0.1 and refuses a request whose `Host` header is not `127.0.0.1:<port>`
  or `localhost:<port>` (no DNS rebinding);
- every API call needs the token printed at start (the page keeps it in `sessionStorage` and
  drops it from the address bar);
- commands that need a terminal or sudo cannot run as detached jobs: `shell`, `console`, `flash`,
  `import-device` (and `web` itself). **Flash** and **import-device** are terminal-only forms:
  the host's disks are listed under `--device` (`/api/devices` = `vmctl list-target-devices --json`,
  the system disk left out) as cards, and the selected one shows its partitions with size,
  filesystem, label, mount point and the unallocated space; `--confirm-device` is filled from
  the choice. Then **Open in a terminal**
  asks in the browser (the command and the disk's model, size and partition count), then starts
  the command in a terminal window on the host (`/api/terminal`, validated by the subcommand's
  own parser, only those two commands). The terminal shows the command and **waits for Enter
  before running it** (Ctrl-C aborts): sudo may still hold a valid timestamp, so without that
  stop a click would have overwritten the disk with no question at all. The CLI keeps its disk
  checks and its questions (expansion, resume); the window stays open at the end with the exit
  status. *Copy for terminal* remains for a host without a desktop;
- SSH has dedicated authenticated endpoints for its browser PTY and host terminal; neither
  accepts an arbitrary command from the browser;
- destructive commands (`clean`, `delete-iso`, `clean-reports`, `checkpoint restore|delete`,
  `group clean|install`, `check-vms --clean-first`...) ask in the browser, and the server refuses
  them without that confirmation; a confirmed command that has a `--yes` gets it, because a job
  has no terminal to answer on.

## API

| Method | Path | What |
|---|---|---|
| GET | `/api/state` | dashboard rows (the TUI snapshot) and labs; `?fresh=1` skips the 4 s cache |
| GET | `/api/commands` | catalog: group, help, arguments, exclusive option groups and `terminal_only` |
| POST | `/api/run` | `{"args": ["start", "vm", "--headless"], "confirmed": false}` → `{"job", "command"}` |
| GET | `/api/jobs` | recent jobs, running first |
| GET | `/api/jobs/<id>/log?offset=N` | the log from byte N, the next offset and the job status |
| POST | `/api/jobs/<id>/cancel` | stop a running job |
| GET | `/api/vm/<vm>/show` | `vmctl show <vm> --json` |
| GET / POST | `/api/vm/<vm>/override` | read template/override/revision; save `{"override": {...}, "revision": "..."}` |
| POST | `/api/vm/<vm>/ssh-terminal` | open `vmctl shell <vm>` in a host terminal |
| POST | `/api/terminal` | `{"args": ["flash", "vm", "--device", ...]}`: a terminal-only command in a host terminal window |
| GET | `/api/devices` | the host's block devices (`vmctl list-target-devices --json`) for the `--device` field |
| GET (WebSocket) | `/api/vm/<vm>/ssh?token=` | interactive SSH PTY; JSON input/resize messages, binary terminal output |
| GET | `/api/vm/<vm>/screen.png` | a screenshot of a running headless VM (QMP screendump) |
| GET (WebSocket) | `/api/vm/<vm>/vnc?token=` | the VM's VNC socket, relayed both ways (noVNC in the page) |
| GET | `/labs/<group>/map` | the lab's network map page (`vmctl group map <group>` writes it) |

Every call sends the token as `X-Vmctl-Token` (or `?token=` for images and the map tab).

## Local checks

Run `python3 -m unittest tests.test_webui tests.test_web_terminal tests.test_profile_overrides tests.test_config tests.test_tui_jobs -v` for the server, PTY, overrides and job tests.
The optional `node tests/webui_browser.mjs` browser regression requires Playwright and its
Chromium browser. If Playwright is installed elsewhere, set `PLAYWRIGHT_MODULE` to its absolute
`index.mjs` path. It uses a fixture API and console client, never real VM operations, and saves
desktop/mobile screenshots under `artifacts/webui-review/`.
Set `REAL_TERMINAL_ASSETS=1` to also check the actual xterm CDN assets against the fixture SSH connection.

After a code update, restart `make web` and open the newly printed token URL. Reloading the
page alone refreshes HTML but cannot load new Python API endpoints into an existing server.

## Next steps

- **The serial console in the browser**: extend the SSH terminal to `runtime/serial.sock`
  (`vmctl console`), and serve noVNC/xterm locally for hosts without internet access.
- **One Python layer for the facts and the actions**: part of them still lives in `bin/vmtui`
  (bash with embedded Python); moving it into a module would serve the TUI, the web and the CLI.
- **Profiles as plugins**: today every profile is an entry of `vms/profiles/*.json`. The idea is
  a catalog that can be downloaded: an index of profiles (a whole catalog, or a single VM) with
  their version, checksum and the flow they need, installed into a local directory that
  `load_config()` merges like `local.json`. The web and the TUI would list what is available,
  install it and keep it up to date.
