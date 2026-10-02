# The web dashboard (`vmctl web`)

```bash
make web            # or: vmctl web --open   (PORT=9000 make web / --port 9000; --port 0 picks a free one)
```

`vmctl web` serves the lab in a browser on **127.0.0.1 only** and prints a URL with a random
token. It drives the same backend as `vmctl` and `vmtui`. To see one VM through its whole
lifecycle from the page, screen by screen, read [From zero to a running VM](FIRST_VM.md).

What the page offers:

- **Profiles**: every profile with its live state, search (`/`) and the filters of the dashboard
  (the workspace tabs My VMs, Catalog and Labs, then the machine filters All, Running, Stopped, To install, With disk and Hidden while there are hidden profiles). The × in the search box clears the text and keeps the current filter.
- **My VMs**: the profiles you chose out of the catalog (plus whatever is running right now,
  so a running VM never hides). Its opposite is **Hide from the lists** (right-click a row, or a
  selection): a hidden profile leaves All and With disk, still shows while it runs or holds a
  disk, and the *Hidden* chip (shown while there are any) lists them to bring them back;
  hiding takes the star away. `vmctl catalog hide|unhide` from the terminal; `check-vms`,
  groups and `vmctl list` still see every profile. Choose them with *Add to My VMs* (right-click a row,
  the ☆ button of the details panel, or a multiple selection), and the page opens on that view
  from then on; the chosen profiles carry a ★ in every view. The selection is
  `vmctl catalog add|remove|set|clear` and lives in `vms/profiles/local.json` under `catalog`
  (`POST /api/catalog`). An empty selection is the whole catalog. The panel on the right shows the facts and the actions that make
  sense for the selected VM: *Unattended install* (the flow `check-vms` would run), *Get the ISO…*
  for a medium only you can provide, *Download the ISO*, *Boot headless*, *Boot with display*
  (a QEMU window on the host), *Console*, *Screenshot*, *Open viewer on the host*, *Stop*,
  *Checkpoint now*, *Clean*. *Get the ISO…* shows where to download the medium (the links open
  in a new tab) and a **Choose ISO…** button: a picker over this computer's folders (Home,
  Downloads, `isos/` and the folders your other profiles' media already live in, `GET /api/fs`),
  with the file checked on the spot (`POST /api/vm/<vm>/iso-check`: ISO 9660, pinned hashes, for
  Windows the editions and languages of `install.wim`), then *Use it where it is* (`vmctl iso set`:
  the path in `local.json`, nothing copied) or *Move into isos/* (`--move`). The ISO never travels
  through the browser. F2 boots the selected machine headlessly. Right-click a row (or Shift+F10)
  to open the same actions, SSH access and profile customization. This also works on VM
  names, addresses and state badges inside lab cards.
- **Start options**: an installed machine starts in a separate console window by default. The
  other ways are under *Other ways to start*:
  - **Start and open console here**;
  - **Start without opening console**;
  - **Start in a host window**;
  - **Start ephemeral (changes discarded)**: `vmctl start --ephemeral`, QEMU `-snapshot`. The
    session writes to temporary files and the disk and EFI variables stay as they were when it
    stops; the shared folder is the host's own directory and keeps what is written into it.
  - **Start from the ISO (rescue, live)**: `vmctl start --boot-iso`, shown when the profile's ISO
    is on the host. The ISO is inserted and booted first with the installed disk attached, like a
    Windows repair DVD; the install record is untouched.

  A hand-driven install (`vmctl install`, `vmctl provision`) puts the disk first and the ISO second:
  the firmware reaches the ISO only while the disk is empty, so the reboot an installer asks for
  ("remove the installation medium, then press ENTER") lands on the installed system. Over a
  disk that already holds a system, `--boot-iso` boots the ISO first for a reinstall.
- **Customize**: three tabs separate **Resources**, **Advanced JSON** and the read-only
  **Catalog template**. Memory and vCPUs have presets, editable values and **Use catalog**
  buttons to restore each field independently. Empty fields inherit the catalog; local values
  are labelled. Validation and **Save changes** stay visible while scrolling. The catalog stays
  unchanged; edits go to the ignored `vms/profiles/local.json`, with the previous document in
  `local.json.bak`. The complete candidate configuration is validated before writing, and an
  editor opened before another change cannot overwrite it. Objects merge recursively and
  arrays append, matching the CLI. **Restore all defaults** clears this VM's draft override;
  **Save changes** applies the reset. Other overrides are preserved by per-field resets;
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
  The toolbar groups display/input and capture controls, with highlighted buttons for open tools.
  **Keyboard** sends Ctrl+Alt+Del, Super/Windows, Alt+Tab, Esc, Ctrl+Esc, Print Screen,
  F1–F12 and Ctrl+Alt+F1–F12, and can release held modifiers. Actual size keeps scrollbars
  available for a desktop larger than its viewport. **Screenshot** downloads the current VNC
  frame as a PNG at guest resolution. The **Record: … fps** selector only affects recording.
  **SSH** opens the existing browser terminal below the desktop; drag the divider or focus it
  and use ↑/↓ (Home/End for the limits). Closing SSH keeps the desktop open. Closing the
  console ends its docked SSH session. Reconnecting the display preserves the SSH session.
  **Clipboard** uses one text box: paste/type text and **Send to VM**, or copy text in the
  guest and choose **Copy to computer**. Incoming text never replaces an unsent local draft;
  **Load new text copied in VM** appears instead. Sending updates the guest clipboard;
  paste inside the guest to insert it. Local clipboard access is explicit, with
  manual copy/paste as a fallback when the browser denies access. Text is not stored in browser
  storage and is cleared when the console closes or changes VM. Profiles with `clipboard: true`
  now expose the agent channel in headless mode too, after a shutdown and new start. Guest
  desktop clipboard integration still requires a working agent such as `spice-vdagent`;
  QEMU Guest Agent alone does not provide it. The panel distinguishes a missing channel from
  unverified guest support and confirms reception only after text arrives from the guest.
  **Files** browses the SSH user's home folder and its directories. Click a file to download,
  drag files onto the console, or click the upload area to choose several files. Transfers use
  the profile's existing SSH key and the guest's SFTP subsystem, independently of clipboard
  support; password-only SSH sessions are not sufficient. Files are limited to **256 MiB each**.
  Existing names get a numbered copy; uploads are staged privately and renamed only on success.
  Symlinks and special files are shown but cannot be downloaded. Zip folders before uploading.
  The browser shows upload progress followed by **Saving in VM** until SFTP confirms completion.
  If a connection fails, refresh the folder before retrying. A lost SSH connection may leave a
  hidden `.vmctl-upload-*.part` file when cleanup cannot reach the guest.
  The header is four captioned groups: **Display** (fit, keyboard, clipboard), **Guest** (SSH,
  files, integration), **Capture** (screenshot, record and its frame rate) and **Machine**
  (stop), then the window actions Detach, Full screen and Close.
  **Stop** asks the guest to power off (`vmctl stop`, confirmed in the page's own dialog, with a
  **Force** checkbox that turns it into `vmctl stop --force` for a guest that answers nothing):
  the console shows the shutdown and closes by itself once the VM is off.
  **Detach** moves the console into its own window, leaving the dashboard free to open another
  VM. Each window has its own display, tools and docked SSH session; closing one does not stop
  its VM or close the other consoles. Allow pop-ups for the local dashboard if prompted by
  the browser. Reloading a detached window restores that VM's console.
  **Auto reconnect** retries display failures after 2, 4, 8, 16 and then 30 seconds, with a
  countdown and a separate message for a stopped VM. Uncheck it to pause retries, or use
  **Reconnect** immediately. Closing the console cancels pending attempts.
  Full screen gives the entire display to the guest; move the pointer to the top edge or click
  **Controls** to reveal the toolbar. The guest is asked to resize when supported; otherwise
  fit-to-window preserves its aspect ratio, which can leave bars. Esc goes to the VM outside
  browser full screen, so the dialog closes with *Close*.
  `…/#console=<vm>` after the URL opens a VM's console directly. The noVNC client is loaded
  from cdn.jsdelivr.net (`@novnc/novnc@1.7.0`); the screenshot view needs no external assets.
- **Recording**: **Record** in the console captures on the host at **10 fps** by default; choose 1, 5, 10 or 15 fps before starting. **Stop recording**
  opens a download dialog where you choose GIF or MP4, and can download both. GIF keeps the timeline at **1 fps**
  and up to 1280 px wide; MP4 uses the selected frame rate and original resolution (rounded down to even dimensions for H.264).
  Closing the console leaves recording active; the dashboard shows its status and Stop button.
  Reloading the same tab reconnects to it while the server is running. Capture stops when the VM
  disconnects, the web server exits, or it reaches 1 hour / 512 MiB of frames. `ffmpeg` is required
  on the host. Actual capture speed depends on host load and guest resolution. Frames and exports remain in `artifacts/.web-recordings/<id>/`, independently of
  VM cleanup. **Done** clears the current download panel so another recording can be started.
- **Keyboard**: typing anywhere filters the list (Esc clears), ↑/↓ select, and function keys
  run one kind of action whatever the state offers: `F2` boot
  headless, `F3` console, `F4` SSH in the browser, `F6` screenshot, `F7` checkpoint, `F8` stop, `F9` job log,
  `F10` (or `Ctrl+K`) the commands palette, `Shift+F10` the machine's menu, `F1` the list of
  keys. The buttons show their key.
- **Commands** (`F10`, `Ctrl+K`): a palette in two panes. *This VM* lists only the commands that make
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
  **Actions ⋯** on each lab opens its stack menu; **⋯** beside each member opens that VM's
  actions. Both work with click, touch and keyboard; right-click and Shift+F10 remain available.
  Clicking a VM's name opens its details alongside only that lab's machines. The breadcrumb
  shows **Labs / lab / VM** and **← Labs** returns to the original lab search and member.
  On small screens, the machine's details appear before the member list. Choose **All** to
  return to the full catalog.
- **Double-click** on a row runs its main action, the one the machine panel shows first: the
  console (SSH for server roles) of a running VM, Start of an installed one, the automatic
  install or the ISO dialog of a VM without a disk. Nothing runs while a job holds the VM.
- **Multiple selection**: Ctrl/Cmd+click toggles machines, Shift+click selects a range. Clicking a distribution icon
  toggles it too (Enter/Space when focused). Selected rows have a teal edge and a check badge
  on the icon. Right-click a selected row for a menu scoped to the entire selection; right-click
  outside it for single-machine actions. The selection toolbar offers **Start**, **Stop**,
  **Start in console** and **Link network**, with the eligible count for each action. Review the machine names before
  submitting; busy or ineligible VMs are skipped. When all selected machines share a temporary
  network, the toolbar and context menu offer **Unlink** instead. If they share several networks,
  choose one before confirming; other members of that network remain connected. Dragging the
  network button onto an already linked peer, or choosing that peer in the picker, also offers
  **Unlink**, scoped to those two machines. Search/filter changes clear the selection.
  With a selection, **F2** starts and **F8** stops the selected machines (same confirmation as
  the toolbar); the other function keys act on the machine shown in the details panel.
- **Start in console** (selection toolbar and menu): the selected machines side by side on one
  page, `/multi#vms=a,b`, opened in a new tab. Each pane is the detached console of one VM with
  all its tools (keyboard, clipboard, files, SSH, screenshot, recording), so clicking a pane
  gives that machine the keyboard; the page starts the stopped ones (headless, in the
  background) and connects each pane as soon as its VM runs. The pane header shows the state,
  **Start**/**Stop**, **Reload**, **Open alone** (that console in its own window) and **×**
  (remove from the page); **Add a machine…** adds another pane, the layout buttons switch
  between side by side, stacked and grid. **Link network** in the header runs `vmctl link` on
  the machines of the page (**Unlink network** when they already share a segment): each pane
  then shows the machine's address on the private segment (192.168.100.x; "at next start"
  while the NIC is only recorded for a stopped VM). Closing the tab stops nothing.
- **Link**: use the network button shown on hover, focus or the selected row: click it to choose
  another VM, or drag the network button onto another machine to connect them on a private
  segment (`vmctl link`, confirmed first): a running one gets a hot-plugged NIC, a stopped one
  gets it at its next start; Linux, FreeBSD and Windows 10/11 guests get their 192.168.100.x address over SSH,
  the panel shows a *Linked* row (*at next start* while pending) with an *Unlink* button, and the
  segment is listed under Labs as a session lab. Details and limits: [LABS.md](LABS.md#temporary-links-between-vms-vmctl-link).
- **Jobs**: everything runs as a detached job with its log followed live. A job started for a VM
  uses the same `artifacts/<vm>/runtime/tui-job` as the TUI, so `vmtui` shows installs started
  in the browser and the other way round; jobs keep running when the page or the server closes.
  A running job can be cancelled (a VM it started is stopped; disk and logs are kept).
  VM badges distinguish starting, running, stopping and installing using the recorded command;
  a running job alone does not mean an installation is taking place. Starting/stopping stays
  visible until a fresh VM state confirms completion; a delayed or disconnected refresh does
  not briefly re-enable the old controls. An unexpected state is reported explicitly.
  **Stop** asks the guest first (guest agent, then ACPI for the profile's grace, 60 s by default,
  then a power-off over SSH when the guest accepts the connection) and signals QEMU only after
  that: a guest sitting at a boot menu or inside an installer answers none of it, so its Stop
  takes the whole ACPI grace before the SIGTERM. **Force stop** skips guest shutdown, signals
  QEMU directly and escalates to SIGKILL after 2 seconds if necessary: the choice for a guest
  that has no OS to ask. **Clean** shows *Cleaning disk* and logs removed paths; its own
  log, lock and completion status survive cleanup, along with checkpoints unless explicitly removed.
  The bottom bar shows the latest active job with **View job** and **Cancel job**. It remains
  during the final state check, disappears on completion, and leaves the log in Recent activity.
  Hovering a distribution icon only previews the live screen; linking uses the network button.

![vmctl web: My VMs, two of them running, the selected VM's actions and facts](screenshots/web-dashboard.png)

![Labs in the web dashboard: member addresses, live VM states, stack controls and a session lab](screenshots/web-labs.png)

## Welcome and the guest identity

The tracked catalog installs every machine as `lab` (password `lab`). The first `vmctl web` on a
checkout without `vms/profiles/local.json` opens **Welcome**: one form with the guest user, the
real name and the password, prefilled with those defaults. *Save* creates `local.json` with a
top-level `identity` (the user, a SHA-512 crypt hash of the password and, unless the box is
unticked, the password in clear: Windows, Arch and Alpine installers take only a plain one), and
from then on every tracked profile installs as that user (`docs/PROVISIONING.md`). *Not now* keeps
`lab`/`lab`. The **Identity** button in the header reopens the same form later: an empty password
keeps the current one, and the per-VM overrides, *My VMs* and the protected list of `local.json`
stay as they are (a `.bak` of the previous file is kept). The file is personal and git ignores it.
The form also takes the guest **language, keyboard and time zone**: saved as a top-level
`locale` block, applied to every installer section that has such fields, in each installer's
own format (`docs/PROVISIONING.md`); empty fields keep each profile's own values.
The terminal twin is `vmctl identity` (`--user`, `--ask-password`, `--realname`, `--language`,
`--keyboard`, `--timezone`; no option shows it), which the command center does not list so
that no password lands in a job log.

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
| GET | `/api/vm/<vm>/console-info` | running process, actual clipboard channel (null if unknown), and profile clipboard setting; channel presence does not establish guest agent readiness |
| GET | `/api/vm/<vm>/files?path=.` | directory listing over SFTP, canonical path, home, parent and transfer limit |
| GET | `/api/vm/<vm>/file?path=…` | download a regular guest file, staged before HTTP headers are sent |
| POST | `/api/vm/<vm>/files-upload?path=…&name=…` | raw file body, at most 256 MiB; returns the saved name/path/size without replacing existing files |
| POST | `/api/vm/<vm>/files-session` / `files-session-close` | open (and close) one SFTP session that several sequential uploads share (`?session=` on `files-upload`); 8 sessions at most, idle ones expire after 60 s |
| GET / POST | `/api/identity` | the guest identity of local.json: `{exists, path, identity: {user, realname, has_password, has_hash}, defaults}`; save `{"user", "password" (empty keeps the current one), "realname", "store_password"}`: creates the file on a fresh checkout, keeps every other key afterwards |
| POST | `/api/catalog` | `{"action": add|remove|set|clear|hide|unhide, "names": [...]}`: My VMs and the hidden profiles (`vmctl catalog`), saved in local.json |
| POST | `/api/protect` | `{"action": "add"|"remove", "names": [...]}`: `vmctl protect`/`unprotect`, saved in local.json; the 🔒 of the details panel. A VM in *My VMs* whose disk holds data is protected as well (`protected_by: star` in the rows): the star, not this call, unlocks it |
| GET | `/api/vm/<vm>/connections` | the *Integration* panel's checks: console, SSH (key rejected / algorithm mismatch / port closed), guest agent, SFTP, clipboard channel; read-only probes of 3 s, cached 5 s |
| POST | `/api/vm/<vm>/diagnostics` | a text report of fixed read-only commands (system, network, storage, services, logs, APT/DNF state; Windows has its own list; sysvinit guests get no `journalctl`), as root when `sudo -n` works, 10 s / 128 KiB per command, plus the tails of the serial logs; also saved as `artifacts/<vm>/logs/diagnostics-<utc>.txt` |
| POST | `/api/vm/<vm>/guest-command` | `{"command": "...", "confirmed": true}`: one shell command over SSH as a job in the VM's slot (60 s, 1 MiB); stdout, stderr and exit code in the job log; cancelling closes SSH, not the VM |
| GET | `/api/vm/<vm>/history` | the VM's current job and the archived ones (`runtime/tui-job/history/`, the last 100), each openable as a log |
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
