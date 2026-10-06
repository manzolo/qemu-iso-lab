# The tour: recording, narrating and publishing the intro clips

The catalog site's [tour](https://manzolo.github.io/qemu-iso-lab/tour.html) is six short clips (the
catalog, the setup, a first VM, the console in the browser, a lab, your own lab), spoken and subtitled in English
and Italian. They are recorded on a real desktop, inside a VM, by a script that drives the browser
and the terminal like a person would: the pointer moves, the commands are typed, the waits are sped
up and marked. Everything that makes them lives in `tools/tour/`; this page is how to do it again.

```
tools/tour/
├── session.sh       host: prepare the recording VM (--setup) and start a recording session
├── rec.mjs          host: record one clip (Playwright over CDP + xdotool + ffmpeg in the VM)
├── clips/*.mjs      the clips: their cues (en/it text) and what happens on screen
├── build.py         host: subtitles (.srt), MP4 with both subtitle tracks, phone preview, review frames
├── narrate.py       host: the voice (XTTS-v2), one MP4 with an Italian and an English track
├── tts_cues.py      runs in the XTTS venv: one WAV per cue and language, cached by text
├── publish.py       host: the narrated clips -> docs/media/tour/ (media branch) + tour.json
├── setup-tts.sh     host: the XTTS venv under artifacts/tts/
└── vm/              copied to ~/lab/video/ in the VM: setup.sh, chrome.sh, mv.py, demo.bashrc, open-in-cdp.sh
```

Outputs go to `artifacts/tour/out/<clip>/` (gitignored): `raw.mkv`, `cues.json`, the `.srt`, the MP4s,
`voice/` (one WAV per sentence), `review/`.

## 1. The recording VM

Any X11 desktop VM works; the clips assume Lubuntu (LXQt, qterminal) at 1600x900 with autologin on
`:0`, SSH with your key, and nested KVM (`/sys/module/kvm_intel/parameters/nested` = Y on the
host, `host-passthrough` CPU) because the demo installs VMs inside it. Size: 8 vCPU, 16 GB RAM
(two lab members and the desktop), a data disk of 100 GB or more for `~/lab/`.

The maintainer's is the libvirt VM **`lubuntu22-studio`** (hostname `lubuntu-studio`), a clone of
the `lubuntu22` lab VM kept for this, on libvirt's `default` network (NAT + DHCP: no lab router
needed). Its address comes from the lease:

```bash
virsh start lubuntu22-studio
virsh domifaddr lubuntu22-studio --source lease     # 192.168.122.x
```

A clone of a VM whose netplan matches its NIC by MAC gets no address at all (the clone has a new
MAC): the studio's netplan matches `en*` with `dhcp4: true`; when SSH is out of reach, the QEMU
guest agent (`virsh qemu-agent-command ... guest-exec`) still runs commands as root.

Once, from the host (asks for the VM user's sudo password):

```bash
TOUR_VM=user@192.168.122.x tools/tour/session.sh --setup
```

`vm/setup.sh` installs ffmpeg, xdotool, qterminal and the **Chromium snap** (Chrome for Testing,
what Playwright downloads, shows a "for automated testing" banner that no flag hides), copies the
helpers to `~/lab/video/`, hides qterminal's menu and tab bar (font 17) and turns Chromium's password
manager off (its "Save password?" bubble covered the Welcome form).

## 2. A session

```bash
TOUR_VM=user@192.168.122.x tools/tour/session.sh
```

Every time: the screen goes to 1600x900 and stays there (`spice-vdagent` is stopped: an open
virt-manager window resizes the guest to its own size, which broke a take at 1152x768), blanking
off, the recorded Chromium started in kiosk mode at 125 % zoom with DevTools on `127.0.0.1:9222`,
tunnelled to `127.0.0.1:19222` on the host.

The demo lives in `~/lab/demo/qemu-iso-lab`, a fresh clone made by clip 2, with the guest user
`demo` (password `demo`) chosen in its Welcome form: never a real name on screen. Clip 2 runs
`./setup.sh`, which points `~/.local/bin/vmctl` at the demo clone.

## 3. Recording a clip

```bash
export TOUR_VM=user@192.168.122.x
export PLAYWRIGHT_MODULE=/path/to/node_modules/playwright/index.mjs   # Playwright 1.5x+
node tools/tour/rec.mjs tools/tour/clips/01-catalog.mjs [--dry]
python3 tools/tour/build.py artifacts/tour/out/01-catalog
```

A clip exports `title`, `cues` (`id`, `en`, `it`, optional `top: true` to put that subtitle at the
top), an optional `setup(d)` (before the recording starts) and `run(d)`. The helpers of `d`:

| | |
|---|---|
| `cue(id)` | the next subtitle starts now; the previous one stayed at least its reading time (the longer language, ~14 characters a second, 3.5 s minimum) |
| `click(locator)`, `hover(locator)` | the real X pointer glides there (`mv.py`) and clicks: the video shows a pointer, CDP input would not |
| `type(text)`, `key(keys)` | xdotool into whatever has the focus |
| `scroll(dy, ms)` | a smooth page scroll |
| `openTerminal()`, `run(cmd)`, `focusTerminal()`, `focusBrowser()` | qterminal with the demo shell; `run` waits for the next prompt |
| `newestPage()` | the tab a link or `vmctl web --open` just opened |
| `restartWeb()` | a fresh `vmctl web` of the demo checkout; returns its URL with the token |
| `ff(factor)`, `ffEnd()` | from here to there the edit runs `factor` times faster, with a "▶▶ N×" badge |
| `vm(cmd)` | a command in the VM over SSH (one ControlMaster connection) |

Clips 3–5 start from the state the previous one left (a demo checkout with an identity, then an
installed `ubuntu-24.04-cloud`): record them in order. Changing a sentence only needs the cue text
edited and `build.py` again, as long as the timing still fits.

Send the `*.it-burned.mp4` preview to a phone: soft subtitles rarely show there.

## 4. The voice

```bash
tools/tour/setup-tts.sh                                          # once
python3 tools/tour/narrate.py artifacts/tour/out/01-catalog      # --speaker "Claribel Dervla" is the default
```

XTTS-v2 (Coqui, run on the GPU; the model is under the Coqui Public Model License, non-commercial)
reads every cue in both languages. A sentence starts with its subtitle; where it is longer than its
cue, the last frame of the cue is held (both languages share one video, so the longer one wins).
The WAVs are cached by text and voice: after a correction only the changed sentences are spoken again.

What the subtitles write and the voice should say differently is `spoken()` in `narrate.py`
(the `SPOKEN` table plus, for Italian, every number, dotted version and file name spelled out in
words: `24.04` -> "ventiquattro punto zero quattro", `setup.sh` -> "setup punto esse acca"), and
full stops are dropped everywhere, also before a closing quote or bracket: XTTS read a
sentence-ending "." aloud and invented words for versions and file names (2026-10-05).
`qemu-iso-lab` becomes "QEMU ISO Lab", `VM` "V M", and in Italian `console` is written
`consolle` so it is said the Italian way; the buttons' own names (Open console, Consoles) stay
English. **Before synthesizing, read what the voice will get**: `narrate.py --show <clip>` prints
every cue next to its spoken form. Both voices are synthesized by default
(`--voice it,en`); `--voice it` puts the Italian voice on both tracks. `tests/test_tour_narrate.py` pins the filter.

Another voice: `--speaker "<name>"` (XTTS's built-in speakers), or a recording of your own with
`--speaker-wav file.wav` (15–25 s of plain speech, quiet room). On a PipeWire host:
`pw-record --rate=22050 --channels=1 --sample-count=551250 my-voice.wav` (25 s); stopping
`pw-record` with a signal (`timeout`) truncates the file to a couple of seconds.

## Lab lessons

A lab may have more than one lesson: `export const order = 0` puts a clip before the lab's others on the tour page and on its card (`lab-git-basics`, for beginners, comes before `lab-git`); without it a lesson has order 1 and lessons of one lab sort by name.

Besides the tour's chapters, a clip can be a **lesson on one lab**: `export const series = "labs"`
and `export const lab = "<group>"` in the clip. Eight so far, one per lab with content:
`lab-lvm` (LVM from scratch), `lab-vpn` (WireGuard by hand, the two captures, the iptables fence),
`lab-ssh` (keys, port knocking, hardening, fail2ban, nmap), `lab-docker` (engine, images,
run/exec/logs, volumes, Compose, a build), `lab-netlab` (Pi-hole, the desktop, pfSense, the
forwards and the map), `lab-git` (Git from underneath: objects, refs, a conflict made and
resolved, stash, remote, rebase, git flow, the reset), `lab-zfs` (a RAIDZ pool, a compressed
dataset, snapshots and rollback, a disk offline and its resilver, a scrub, send/receive, mirrors)
and `lab-mdadm` (a RAID1 mirror, a failed and replaced disk, a RAID5 whose hot spare rebuilds by
itself, an online grow, stop and assemble: every read of /proc/mdstat waits for the sync in the guest). The tour page lists the lessons in their own playlist after the tour, a lesson
does not play on into the next clip, and the catalog's **Labs** section shows "▶ Watch the lesson"
on that lab's card (`tour.json` carries `series` and `lab`, `tools/build_catalog_site.py` joins
them). Lessons are terminal-driven, with the helpers of `rec.mjs`: `d.session(vm)` opens `vmctl shell`
into a member and waits until the guest sees a pts, `d.guest(cmd)` types one command and waits for
the guest's own prompt (a `PROMPT_COMMAND` stamp read through a second shell) before the next,
`d.leave()` exits and waits for the host's prompt; a cue per concept, the output left on screen
long enough to read. The last step runs `vmctl group test <lab>`, so the lab must be back in its
initial state first. Lessons learned the hard way (five takes of the LVM lesson): a fixed delay
is never enough (a `mkfs` or an `lvextend` overran it and the next command queued up); teardown
with `;`, never `&&` (one failed `umount` left the volume group behind and the tests red); wait
for a snapshot merge to finish before removing the group; `exit` closes the session, so its wait
is on the host's prompt; a loop of failed SSH logins needs `ConnectTimeout`, or fail2ban's ban
mid-loop hangs the rest on the DROP; Docker 29 moved a container's `IPAddress` under
`.NetworkSettings.Networks` (the lesson found the lab's own exercise wrong).

## The Windows chapter

`02w-windows` (after `02-setup`, "On Windows: WSL2 from nothing to the dashboard") is recorded on
the Windows 11 studio, `tools/tour/win/README.md`: a libvirt copy of the `windows-11` disk with the
Hyper-V enlightenments WSL needs, a `demo` desktop and an agent that types and records inside it.
No `.mjs` drives it: it is two segments (`02w-a.mkv` up to the restart WSL asks for, `02w-b.mkv`
after it) recorded step by step through `tools/tour/win/wq` from the snapshot
`1-windows-installato` (`RecStart`/`RecStop`, clicks through `SetCursorPos` + `mouse_event` in
the agent's scope, see `tools/tour/win/README.md`), then `tools/tour/win/assemble_02w.py` keeps
only the useful ranges of the two files (the operator's thinking time between steps goes), writes
`raw.mkv` and `cues.json` with the cue starts and fast-forwards mapped onto that timeline
(`tools/tour/win/02w-windows.cues.json` is its output), then `build.py` and `narrate.py` as for
every clip. Since v0.20.0 (2026-10-05) it shows the catalog site, Edge's *Keep* and *Open file*,
Windows' *Run*, the three runs of `install-windows.cmd` and the desktop icon. Retaking it: revert
the snapshot, follow the cues' order, answer each question only after a screenshot shows it (WSL's
user prompt is pre-filled, setup.sh asks `[y/N]` and twice for sudo, the welcome screen waits for
Enter), note the host time of every step and read the real cut points off a timestamped contact
sheet of each segment (`ffmpeg -vf fps=1/5,tile=10x13`) before editing `RANGES`/`CUES`.

## Before a take (2026-10-04)

The mysql-lab lesson took four takes, each lost to something a rehearsal would have shown. The order
now, for every lesson:

1. **Restore the studio's snapshot** `pronta-per-registrare` (virt-manager, or `virsh snapshot-revert
   lubuntu22-studio pronta-per-registrare`), start it, `git pull` in `~/lab/demo/qemu-iso-lab`,
   `tools/tour/session.sh`. Taken with the VM off: its virtiofs share forbids a running snapshot.
   Do not open a viewer on the studio while recording: it resizes the screen.
2. **The lab works in the studio**, not only on the host: `vmctl group install <lab>` and `vmctl group
   test <lab>` from a stopped VM there. The host's tests are not interactive and find the VM running;
   the studio's interactive shell and cold start are what the lesson meets.
3. **`tools/tour/lint_commands.py`** (also part of `make check`): no `!` inside double quotes (bash's
   history expansion broke `Reader123!`), no command that stops to ask (`mdadm --create` without
   `--run`, `apt` without `-y`, `mkfs` without `-F`/`-f`, `adduser`, `passwd`).
4. **The rehearsal**: `node tools/tour/rec.mjs tools/tour/clips/<clip>.mjs --dry`. The whole clip runs
   in the studio, nothing recorded, the reading pauses cut; one screenshot per cue in
   `artifacts/tour/out/<clip>.rehearsal/`. Read them: the right terminal (the guest's prompt, not the
   studio's), no error on screen, the browser where it should be.
5. **The take.** The recorder checks on its own that the screen is 1600x900, that ffmpeg is recording
   and that the guest session opened, and stops at once otherwise.

## Background music

`narrate.py --music FILE` lays a track under the voice: looped to the clip's length, faded in (3 s)
and out (4 s), mixed in mono like the voices (a stereo bed made the mono voice 3 dB quieter in the
mix), and ducked by a `sidechaincompress` keyed on the voice, so it drops while she speaks and comes
back in the pauses and the fast-forwards. `--music-db` (default -8) sets the bed: the default puts the
piano about 17 dB under the voice in the pauses. The voice's own chain is unchanged, so a clip with
music speaks exactly as one without. Only public-domain or CC0 tracks, kept outside the repository in
`artifacts/tour/music/` with `CREDITS.md` (source, performer, license); the first is Satie's
Gymnopédie No. 1 played by Robin Alciatore for Musopen, public domain on Wikimedia Commons. FreePD,
the obvious CC0 source, closed in 2026.

## 5. Publishing

```bash
python3 tools/tour/publish.py                                    # every narrated clip -> docs/media/tour/
git -C docs/media add -A && git -C docs/media commit --amend --no-edit
git -C docs/media push --force origin media
gh workflow run pages.yml --ref main                             # a push to media alone does not rebuild
```

`docs/media/` is the worktree of the orphan `media` branch (one commit, replaced every time, like
the install clips). Per clip: `<clip>.{it,en}.mp4` (the video with that language's voice; on
2026-10-05/06 the clips carried the Italian voice only, one file),
`<clip>.{en,it}.vtt`, `<clip>.jpg`, and `tour.json`, whose `video` names the file per language. `tools/build_catalog_site.py` turns them into `tour.html` and the
catalog's "▶ Tour" link; without `tour/tour.json` the site has neither.

## Pitfalls met on the way (2026-10-03)

- `pkill -f "<pattern>"` inside an `ssh host '...'` whose command line also contains the pattern's
  text kills that very shell: write the pattern as `vmct[l] web`, or use two SSH calls.
- `a && b || c && d` is not an if: a job-wait loop believed every job was done.
- The welcome screen at the end of `./setup.sh` waits for "What now? [1-4]": the clip answers 1
  (which opens the dashboard through `BROWSER`).
- The first lab install used to be announced as destructive (fixed in 0.17.5); a cloud-image
  profile starred before its first install refused it (fixed in 0.17.5).
- The console clipboard needs a graphical guest with its agent: not on the text-mode server of
  the demo.
- A cloned recording VM inherits the Chromium profile's lock from the original (hostname `lubuntu`,
  the clone is `lubuntu-studio`): Chromium refuses to start ("profile in use by another Chromium
  process on another computer", in `/tmp/chrome.log` in the VM) and `session.sh` waits in vain for
  DevTools. With no Chromium running: `rm -f ~/snap/chromium/common/video-profile/Singleton*`.
- `python3 -m http.server` does not serve byte ranges, so a local test cannot seek in a video;
  GitHub Pages does (206).
