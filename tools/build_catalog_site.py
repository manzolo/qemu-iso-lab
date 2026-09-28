#!/usr/bin/env python3
"""Build the catalog site: every tracked profile as a static page (GitHub Pages).

    tools/build_catalog_site.py            # -> site/ (index.html, catalog.json, icons)
    tools/build_catalog_site.py --out DIR

One self-contained page: search, filters by family / kind of install / role / status, a card
per profile (facts, version and its history, the last live PASS, the commands to run) and a
basket that turns the profiles you tick into one `vmctl catalog add ...` line. catalog.json is
the same data for scripts. The icons are the dashboard's (vmctl/web/icons.js + the sprite).
Reads the tracked catalog only (never local.json) and needs no VM, ISO or QEMU.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import vmctl  # noqa: E402
from vmctl import config, lifecycle, profile_versions, state  # noqa: E402

SITE_URL = "https://manzolo.github.io/qemu-iso-lab/"
REPO_URL = "https://github.com/manzolo/qemu-iso-lab"
FAMILY_LABELS = {
    "debian": "Debian, Ubuntu and flavours", "arch": "Arch family", "fedora": "Fedora", "rhel": "RHEL family",
    "opensuse": "openSUSE", "nix": "NixOS", "alpine": "Alpine", "void": "Void", "mint": "Linux Mint", "kali": "Kali",
    "bsd": "BSD and pfSense", "proxmox": "Proxmox VE", "haiku": "Haiku", "reactos": "ReactOS", "windows": "Windows",
    "kolibrios": "Hobby systems", "redox": "Hobby systems", "menuetos": "Hobby systems", "serenityos": "Hobby systems",
}
FAMILY_ORDER = ["debian", "arch", "fedora", "rhel", "opensuse", "nix", "alpine", "void", "mint", "kali", "bsd",
                "proxmox", "windows", "haiku", "reactos", "kolibrios", "redox", "menuetos", "serenityos"]


def medium_of(vm: dict[str, Any]) -> str:
    """Where the install medium comes from: public (vmctl downloads and checksums it), manual
    (your own ISO, the profile says which), image (a disk image built or downloaded), none."""
    if isinstance(vm.get("disk_image"), dict):
        return "image"
    if any(vm.get(key) for key in ("iso_url", "iso_urls", "iso_discovery", "iso_archive")):
        return "public"
    if vm.get("iso_help") or vm.get("iso"):
        return "manual"
    return "none"


def flow_of(vm: dict[str, Any]) -> str:
    try:
        flow = str(lifecycle.local_test_mode(vm)[0] or "")
    except Exception:
        flow = ""
    return flow if flow.startswith("bootstrap-") else ""


def commands_for(name: str, vm: dict[str, Any], flow: str, medium: str) -> list[str]:
    if flow:
        return [f"vmctl {flow} {name}", f"vmctl start {name}"]
    if medium == "image":
        return [f"vmctl prep {name}", f"vmctl start {name}"]
    if medium == "manual":
        return [f"vmctl fetch-iso {name}   # says which ISO to provide", f"vmctl provision {name}"]
    return [f"vmctl provision {name}", f"vmctl start {name}"]


def profile_record(name: str, vm: dict[str, Any], lock: dict[str, Any]) -> dict[str, Any]:
    meta = vm.get("meta") or {}
    flow, medium = flow_of(vm), medium_of(vm)
    locked = lock.get(name) or {}
    return {
        "name": name, "label": str(vm.get("name") or name),
        "family": str(meta.get("family") or ""), "family_label": FAMILY_LABELS.get(str(meta.get("family") or ""), str(meta.get("family") or "Other")),
        "role": str(meta.get("role") or ""), "release_model": str(meta.get("release_model") or ""),
        "status": str(meta.get("status") or "manual"), "verified": meta.get("verified"),
        "version": meta.get("version"), "history": list(locked.get("history") or []),
        "groups": list(meta.get("groups") or []), "extends": str(vm.get("extends") or ""),
        "memory_mb": vm.get("memory_mb"), "cpus": vm.get("cpus"),
        "firmware": str((vm.get("firmware") or {}).get("type") or "").upper(),
        "disk": str((vm.get("disk") or {}).get("size") or ""),
        "flow": flow, "medium": medium, "ssh": isinstance(vm.get("ssh_provision"), dict),
        "shared_dir": isinstance(vm.get("shared_dir"), dict), "lab": isinstance(vm.get("networks"), list) and bool(vm.get("networks")),
        "notes": str(vm.get("notes") or ""), "iso_help": str(vm.get("iso_help") or ""),
        "commands": commands_for(name, vm, flow, medium),
    }


def catalog_data(root: Path) -> dict[str, Any]:
    state.ROOT = root
    state.CONFIG_DIR = root / "vms"
    cfg = config.load_config(local_profiles={"vms": {}})
    lock = profile_versions.read_lock(root)["profiles"]
    profiles = [profile_record(name, vm, lock) for name, vm in config.sorted_vm_items(cfg)]
    families = sorted({p["family"] for p in profiles}, key=lambda f: (FAMILY_ORDER.index(f) if f in FAMILY_ORDER else 99, f))
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, check=False).stdout.strip() or None
    except OSError:
        commit = None
    return {"generated": _dt.date.today().isoformat(), "commit": commit, "vmctl_version": vmctl.__version__,
            "site": SITE_URL, "repo": REPO_URL, "families": families,
            "counts": {"profiles": len(profiles), "unattended": sum(p["status"] == "unattended" for p in profiles),
                       "verified": sum(bool(p["verified"]) for p in profiles)},
            "profiles": profiles}


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QEMU ISO Lab · catalog</title>
<meta name="description" content="Every VM profile of QEMU ISO Lab: Linux, BSD and Windows on QEMU/KVM, installed with zero clicks. Pick the ones you want.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Crect width='24' height='24' rx='6' fill='%237ddfc5'/%3E%3Cpath d='M7 8l4 4-4 4M12 16h5' stroke='%230c111b' stroke-width='2.2' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<style>
:root { color-scheme: dark; --bg:#0c111b; --panel:#141d2b; --panel2:#1b2738; --line:#2a374a; --text:#e6edf7; --muted:#9caec5; --accent:#7ddfc5; --ok:#7ddfc5; --warn:#efc582; --bad:#ff9aab; --btn:#223047; }
* { box-sizing:border-box; }
body { margin:0; background:radial-gradient(ellipse at 80% 0%,#19283b 0,transparent 55%),var(--bg); color:var(--text); font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; min-height:100vh; }
code,pre,.mono { font-family:ui-monospace,"Cascadia Code",monospace; }
a { color:var(--accent); }
button,input,select { font:inherit; }
button { cursor:pointer; background:var(--btn); color:var(--text); border:1px solid var(--line); border-radius:8px; padding:6px 10px; }
button:hover { background:#2a3a56; }
button.primary { background:var(--accent); color:#0f1722; border-color:var(--accent); font-weight:600; }
header { padding:28px 24px 12px; max-width:1240px; margin:0 auto; }
.brand { display:flex; align-items:center; gap:12px; font-size:22px; font-weight:600; }
.brand .logo { width:38px; height:38px; border-radius:10px; background:var(--accent); display:grid; place-items:center; color:#0c111b; font-weight:800; }
.brand span b { color:var(--accent); }
.brand small { color:var(--muted); font-size:13px; font-weight:400; margin-left:auto; }
h1 { font-size:30px; margin:18px 0 6px; }
.lead { color:var(--muted); max-width:760px; margin:0 0 16px; }
.metrics { display:flex; gap:12px; flex-wrap:wrap; margin:14px 0 6px; }
.metric { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:10px 18px; min-width:110px; }
.metric strong { display:block; font-size:24px; }
.metric span { color:var(--muted); font-size:12px; }
.start { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:12px 16px; margin:14px 0 0; }
.start pre { margin:6px 0 0; white-space:pre-wrap; font-size:13px; }
main { max-width:1240px; margin:0 auto; padding:0 24px 120px; }
#toolbar { position:sticky; top:0; z-index:5; background:rgba(12,17,27,.92); backdrop-filter:blur(6px); padding:12px 0; display:flex; gap:10px; flex-wrap:wrap; align-items:center; border-bottom:1px solid var(--line); }
#search { flex:1 1 260px; background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:9px 12px; color:var(--text); }
.chips { display:flex; gap:6px; flex-wrap:wrap; }
.chips button { border-radius:999px; padding:5px 11px; font-size:12px; }
.chips button.active { background:var(--accent); color:#0f1722; border-color:var(--accent); }
select { background:var(--panel); color:var(--text); border:1px solid var(--line); border-radius:10px; padding:8px 10px; }
#count { color:var(--muted); font-size:12px; margin-left:auto; }
h2.family { font-size:16px; margin:26px 0 10px; color:var(--muted); font-weight:600; letter-spacing:.04em; text-transform:uppercase; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); gap:12px; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:14px 14px 12px; display:flex; flex-direction:column; gap:8px; }
.card.picked { border-color:var(--accent); box-shadow:inset 0 0 0 1px var(--accent); }
.card-head { display:flex; align-items:center; gap:12px; }
.card-head .text { min-width:0; flex:1; }
.card-head .name { font-weight:600; font-size:15px; overflow:hidden; text-overflow:ellipsis; }
.card-head .desc { color:var(--muted); font-size:12.5px; }
.pick { flex:0 0 auto; width:34px; height:34px; border-radius:10px; font-size:18px; line-height:1; padding:0; }
.card.picked .pick { background:var(--accent); color:#0f1722; border-color:var(--accent); }
.badges { display:flex; gap:6px; flex-wrap:wrap; font-size:11px; }
.badge { border:1px solid currentColor; border-radius:6px; padding:1px 7px; font-weight:600; }
.b-unattended { color:var(--ok); } .b-manual { color:var(--muted); } .b-experimental { color:var(--warn); } .b-version { color:#9ebdff; } .b-verified { color:var(--ok); } .b-medium { color:var(--warn); } .b-lab { color:#c9a7ff; } .b-base { color:var(--muted); }
.facts { display:flex; gap:14px; flex-wrap:wrap; color:var(--muted); font-size:12.5px; }
.facts b { color:var(--text); font-weight:600; }
.cmds { background:#0f1622; border:1px solid var(--line); border-radius:9px; padding:8px 10px; font-size:12.5px; position:relative; }
.cmds pre { margin:0; white-space:pre-wrap; }
.cmds button { position:absolute; top:6px; right:6px; padding:2px 8px; font-size:11px; }
details { font-size:12.5px; color:var(--muted); }
details summary { cursor:pointer; color:var(--text); }
details p, details ul { margin:6px 0 0; }
.history li { list-style:none; } .history { padding:0; }
.history .v { color:#9ebdff; font-family:ui-monospace,monospace; margin-right:6px; }
.catalog-icon { --icon-color:var(--accent); --icon-ink:#fff; display:inline-flex; align-items:center; justify-content:center; flex:0 0 40px; width:40px; height:40px; border-radius:11px; color:var(--icon-ink); background:linear-gradient(160deg, color-mix(in srgb,var(--icon-color) 92%,#fff), color-mix(in srgb,var(--icon-color) 78%,#0b1220)); box-shadow:inset 0 0 0 1px color-mix(in srgb,#fff 18%,transparent), 0 1px 2px rgba(0,0,0,.35); }
.catalog-icon svg { width:24px; height:24px; }
.clip { position:relative; border-radius:10px; overflow:hidden; background:#000; border:1px solid var(--line); aspect-ratio:16/10; }
.clip img, .clip video { display:block; width:100%; height:100%; object-fit:contain; background:#000; }
.clip .play { position:absolute; inset:0; display:grid; place-items:center; background:linear-gradient(180deg,transparent 40%,#000a); color:#fff; cursor:pointer; border:0; padding:0; }
.clip .play.mini { inset:auto 8px 8px auto; display:inline-flex; align-items:center; gap:6px; background:#ff0000e6; color:#fff; border-radius:999px; padding:5px 11px 5px 8px; font:600 11.5px system-ui; box-shadow:0 3px 12px #0009; }
.clip .play.mini::before { content:""; border-style:solid; border-width:6px 0 6px 10px; border-color:transparent transparent transparent #fff; }
.clip .play.mini:hover { background:#ff2b2b; }
.clip .play span { width:58px; height:58px; border-radius:50%; background:#ff0000e6; display:grid; place-items:center; box-shadow:0 4px 18px #0008; }
.clip .play span::after { content:""; border-style:solid; border-width:11px 0 11px 20px; border-color:transparent transparent transparent #fff; margin-left:5px; }
.clip .play b { position:absolute; left:10px; bottom:8px; font:600 11px system-ui; text-shadow:0 1px 2px #000; }
.clip .tag { position:absolute; top:8px; left:8px; font:600 10px system-ui; letter-spacing:.06em; text-transform:uppercase; color:#fff; background:#0009; padding:2px 6px; border-radius:4px; }
.clip .len { position:absolute; right:8px; bottom:8px; font:600 11px ui-monospace,monospace; color:#fff; background:#000b; padding:1px 5px; border-radius:3px; }
dialog#player { background:#0b0f16; color:var(--text); border:1px solid #2a374a; border-radius:14px; padding:0; width:min(1100px,94vw); box-shadow:0 30px 100px #000c; }
dialog#player::backdrop { background:#000c; }
dialog#player header { display:flex; align-items:center; gap:12px; padding:12px 16px; border-bottom:1px solid #1f2b3d; }
dialog#player header .t { font-weight:600; } dialog#player header .s { color:var(--muted); font-size:12px; }
dialog#player header button { margin-left:auto; }
dialog#player video { display:block; width:100%; max-height:76vh; background:#000; }
dialog#player footer { padding:10px 16px; color:var(--muted); font-size:12px; display:flex; gap:14px; flex-wrap:wrap; }
dialog#player footer a { color:var(--accent); }
#basket { position:fixed; left:0; right:0; bottom:0; z-index:6; background:#16283a; border-top:1px solid var(--accent); padding:12px 24px; display:none; gap:12px; align-items:center; flex-wrap:wrap; }
#basket.on { display:flex; }
#basket .cmd { flex:1 1 320px; background:#0f1622; border:1px solid var(--line); border-radius:9px; padding:8px 10px; font-size:13px; white-space:pre-wrap; overflow-wrap:anywhere; }
#basket small { color:var(--muted); flex-basis:100%; }
footer { color:var(--muted); font-size:12px; padding:30px 0 0; }
.empty { color:var(--muted); padding:30px 0; }
@media (max-width:520px) { header, main { padding-left:16px; padding-right:16px; } .grid { grid-template-columns:1fr; } }
</style>
</head>
<body>
<header>
  <div class="brand"><span class="logo">&gt;_</span><span>QEMU <b>ISO Lab</b> · catalog</span><small>vmctl __VMCTL__ · built __GENERATED____COMMIT__</small></div>
  <h1>Pick the virtual machines you want</h1>
  <p class="lead">Every profile of <a href="__REPO__">qemu-iso-lab</a>: Linux, BSD and Windows on QEMU/KVM, most of them installed with zero clicks. Tick the ones you care about, copy the command, and your dashboards open on them.</p>
  <div class="metrics"><div class="metric"><strong id="m-profiles">—</strong><span>Profiles</span></div><div class="metric"><strong id="m-unattended">—</strong><span>Install themselves</span></div><div class="metric"><strong id="m-verified">—</strong><span>Verified live</span></div></div>
  <div class="start"><b>Get started</b> · a Linux host with KVM, or Windows 11 with WSL2
<pre>git clone __REPO__.git &amp;&amp; cd qemu-iso-lab
./setup.sh              # links vmctl + vmtui, installs what is missing, checks the host
vmctl web --open        # the dashboard: install, boot, use, checkpoint from the browser</pre></div>
</header>
<main>
  <div id="toolbar">
    <input id="search" type="search" placeholder="Search profiles, distributions, desktops…" autocomplete="off" aria-label="Search">
    <div class="chips" id="kind" role="group" aria-label="Kind of install"><button data-v="" class="active">All</button><button data-v="unattended">Installs itself</button><button data-v="manual">Manual install</button><button data-v="experimental">Experimental</button></div>
    <div class="chips" id="role" role="group" aria-label="Role"><button data-v="" class="active">Any role</button><button data-v="desktop">Desktop</button><button data-v="server">Server</button><button data-v="other">Other</button></div>
    <select id="family" aria-label="Family"><option value="">Every family</option></select>
    <span id="count"></span>
  </div>
  <div id="list"></div>
  <footer>Generated from <a href="__REPO__/tree/main/vms/profiles">vms/profiles</a> and <a href="__REPO__/blob/main/vms/profiles.lock">profiles.lock</a> by <code>tools/build_catalog_site.py</code>. Versions: a <i>patch</i> is a fix to the recipe, a <i>minor</i> adds something, a <i>major</i> means an installed VM is no longer comparable. "Verified live" is the last date the maintainer's validation matrix reinstalled the profile from scratch and it passed.</footer>
</main>
<dialog id="player"><header><span class="t" id="player-title"></span><span class="s" id="player-sub"></span><button id="player-close">Close</button></header><video id="player-video" controls playsinline preload="metadata"></video><footer><span>Unattended install recorded with <code>vmctl record</code>: one frame a second, idle screens shortened.</span><a id="player-download" download>Download MP4</a><a id="player-gif" download>GIF</a></footer></dialog>
<div id="basket"><span id="basket-count"></span><div class="cmd mono" id="basket-cmd"></div><button class="primary" id="basket-copy">Copy</button><button id="basket-clear">Clear</button><small>Run it in your checkout: the profiles land in My VMs (vms/profiles/local.json) and the dashboards open on them. Nothing is downloaded until you install one.</small></div>
__SPRITE__
<script>window.ICON_SPRITE = "";</script>
<script src="icons.js"></script>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const picked = new Set(JSON.parse(localStorage.getItem("qil-picked") || "[]").filter(n => DATA.profiles.some(p => p.name === n)));
let kind = "", role = "", family = "";
$("m-profiles").textContent = DATA.counts.profiles; $("m-unattended").textContent = DATA.counts.unattended; $("m-verified").textContent = DATA.counts.verified;
for (const f of DATA.families) { const o = document.createElement("option"); o.value = f; o.textContent = DATA.profiles.find(p => p.family === f).family_label + " (" + DATA.profiles.filter(p => p.family === f).length + ")"; $("family").appendChild(o); }
const ram = (p) => p.memory_mb >= 1024 ? (p.memory_mb / 1024) + " GB" : p.memory_mb + " MB";
const roleGroup = (p) => ["desktop", "server"].includes(p.role) ? p.role : "other";
function visible() {
  const words = $("search").value.toLowerCase().split(/\s+/).filter(Boolean);
  return DATA.profiles.filter(p => words.every(w => (p.name + " " + p.label + " " + p.family + " " + p.family_label + " " + p.groups.join(" ") + " " + p.notes).toLowerCase().includes(w))
    && (!kind || p.status === kind) && (!role || roleGroup(p) === role) && (!family || p.family === family));
}
function clipBlock(p) {
  const c = p.clip; if (!c) return "";
  const style = DATA.clip_style, parts = [];
  if (style === "combo") {
    if (c.gif) return `<div class="clip"><span class="tag">install</span><img src="${esc(c.gif)}" alt="Time-lapse of the ${esc(p.name)} install" loading="lazy">${c.mp4 ? `<button class="play mini" data-play="${esc(p.name)}" aria-label="Play the install video of ${esc(p.name)}">Video</button>` : ""}</div>`;
    if (c.mp4) return `<div class="clip"><span class="tag">install · video</span><img src="${esc(c.poster || "")}" alt="" loading="lazy"><button class="play" data-play="${esc(p.name)}" aria-label="Play the install of ${esc(p.name)}"><span></span><b>Watch the install</b></button></div>`;
    return "";
  }
  if ((style === "gif" || style === "both") && c.gif) parts.push(`<div class="clip"><span class="tag">install · gif</span><img src="${esc(c.gif)}" alt="Time-lapse of the ${esc(p.name)} install" loading="lazy"></div>`);
  if ((style === "video" || style === "both") && c.mp4) parts.push(`<div class="clip"><span class="tag">install · video</span><img src="${esc(c.poster || c.gif || "")}" alt="" loading="lazy"><button class="play" data-play="${esc(p.name)}" aria-label="Play the install of ${esc(p.name)}"><span></span><b>Watch the install</b></button></div>`);
  return parts.join("");
}
function openPlayer(name) {
  const p = DATA.profiles.find(x => x.name === name); if (!p || !p.clip || !p.clip.mp4) return;
  $("player-title").textContent = p.name; $("player-sub").textContent = p.label + " · unattended install";
  const v = $("player-video"); v.src = p.clip.mp4; v.poster = p.clip.poster || "";
  $("player-download").href = p.clip.mp4; $("player-gif").href = p.clip.gif || ""; $("player-gif").style.display = p.clip.gif ? "" : "none";
  $("player").showModal(); v.play().catch(() => {});
}
$("player-close").onclick = () => $("player").close();
$("player").addEventListener("close", () => { const v = $("player-video"); v.pause(); v.removeAttribute("src"); v.load(); });
function card(p) {
  const badges = [`<span class="badge b-${p.status}">${p.status === "unattended" ? "installs itself" : p.status}</span>`];
  if (p.version) badges.push(`<span class="badge b-version" title="Profile version">v${esc(p.version)}</span>`);
  if (p.verified) badges.push(`<span class="badge b-verified" title="Last live PASS of the validation matrix">verified ${esc(p.verified)}</span>`);
  if (p.medium === "manual") badges.push(`<span class="badge b-medium" title="No public download: the profile says which medium to provide">your own ISO</span>`);
  if (p.lab) badges.push(`<span class="badge b-lab">lab member</span>`);
  if (p.extends) badges.push(`<span class="badge b-base" title="The shared recipe this profile extends (vms/profiles: bases)">on ${esc(p.extends)}</span>`);
  const facts = [`<b>${esc(ram(p))}</b> RAM`, `<b>${esc(p.cpus)}</b> vCPU`, `<b>${esc(p.firmware)}</b>`, p.disk ? `<b>${esc(p.disk)}</b> disk` : "", p.ssh ? `SSH` : "", p.shared_dir ? `shared folder` : ""].filter(Boolean);
  const history = p.history.length ? `<details><summary>Changes (${p.history.length})</summary><ul class="history">${p.history.map(h => `<li><span class="v">${esc(h.version)}</span>${esc(h.date)} · ${esc(h.note)}</li>`).join("")}</ul></details>` : "";
  const notes = p.notes ? `<details><summary>Notes</summary><p>${esc(p.notes)}</p></details>` : "";
  const help = p.iso_help ? `<details><summary>Which medium</summary><p>${esc(p.iso_help)}</p></details>` : "";
  return `<article class="card${picked.has(p.name) ? " picked" : ""}" data-name="${esc(p.name)}">
    <div class="card-head">${catalogIcon(osIconKey(p))}<div class="text"><div class="name mono">${esc(p.name)}</div><div class="desc">${esc(p.label)}</div></div><button class="pick" data-pick="${esc(p.name)}" title="${picked.has(p.name) ? "Remove from" : "Add to"} the command below" aria-pressed="${picked.has(p.name)}">${picked.has(p.name) ? "✓" : "+"}</button></div>
    ${clipBlock(p)}
    <div class="badges">${badges.join("")}</div>
    <div class="facts">${facts.map(f => `<span>${f}</span>`).join("")}</div>
    <div class="cmds"><pre>${p.commands.map(esc).join("\n")}</pre><button data-copy="${esc(p.commands.join("\n"))}">Copy</button></div>
    ${history}${notes}${help}
  </article>`;
}
function render() {
  const rows = visible(), byFamily = new Map();
  for (const p of rows) { if (!byFamily.has(p.family)) byFamily.set(p.family, []); byFamily.get(p.family).push(p); }
  $("count").textContent = `${rows.length} of ${DATA.profiles.length}`;
  $("list").innerHTML = rows.length ? [...byFamily].map(([f, ps]) => `<h2 class="family">${esc(ps[0].family_label)} · ${ps.length}</h2><div class="grid">${ps.map(card).join("")}</div>`).join("") : `<p class="empty">No profile matches. Try fewer words or another filter.</p>`;
  renderBasket();
}
function renderBasket() {
  const names = DATA.profiles.filter(p => picked.has(p.name)).map(p => p.name);
  $("basket").classList.toggle("on", names.length > 0);
  $("basket-count").textContent = `${names.length} picked`;
  $("basket-cmd").textContent = "vmctl catalog add " + names.join(" ");
  localStorage.setItem("qil-picked", JSON.stringify(names));
}
async function copy(text, button) {
  try { await navigator.clipboard.writeText(text); const old = button.textContent; button.textContent = "Copied"; setTimeout(() => button.textContent = old, 1200); }
  catch (e) { window.prompt("Copy the command:", text); }
}
document.addEventListener("click", (e) => {
  const pick = e.target.closest("[data-pick]"), copyButton = e.target.closest("[data-copy]");
  if (pick) { const n = pick.dataset.pick; picked.has(n) ? picked.delete(n) : picked.add(n); const c = pick.closest(".card"); c.classList.toggle("picked", picked.has(n)); pick.textContent = picked.has(n) ? "✓" : "+"; pick.setAttribute("aria-pressed", picked.has(n)); renderBasket(); }
  if (copyButton) copy(copyButton.dataset.copy, copyButton);
  const play = e.target.closest("[data-play]"); if (play) openPlayer(play.dataset.play);
});
for (const group of ["kind", "role"]) $(group).addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; [...$(group).children].forEach(x => x.classList.toggle("active", x === b)); if (group === "kind") kind = b.dataset.v; else role = b.dataset.v; render(); });
$("family").addEventListener("change", () => { family = $("family").value; render(); });
$("search").addEventListener("input", render);
$("basket-copy").onclick = () => copy($("basket-cmd").textContent, $("basket-copy"));
$("basket-clear").onclick = () => { picked.clear(); render(); };
document.addEventListener("keydown", (e) => { if (e.key === "/" && document.activeElement !== $("search")) { e.preventDefault(); $("search").focus(); } });
render();
</script>
</body>
</html>
"""


def collect_media(root: Path, out: Path, media: Path | None, names: list[str]) -> dict[str, dict[str, str]]:
    """Per profile, the install clip files found under ``<media>/<vm>/`` (``vmctl record`` output:
    recording.mp4, recording.gif, poster.png), copied to ``<out>/media/<vm>/``; site-relative paths."""
    source = media if media is not None else root / "docs" / "media"
    found: dict[str, dict[str, str]] = {}
    for name in names:
        entry: dict[str, str] = {}
        for kind, filename in (("mp4", "recording.mp4"), ("gif", "recording.gif"), ("poster", "poster.png")):
            path = source / name / filename
            if path.is_file():
                target = out / "media" / name / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                entry[kind] = f"media/{name}/{filename}"
        if entry:
            found[name] = entry
    return found


def build(root: Path, out: Path, media: Path | None = None, clip_style: str = "combo") -> dict[str, Any]:
    data = catalog_data(root)
    out.mkdir(parents=True, exist_ok=True)
    clips = collect_media(root, out, media, [p["name"] for p in data["profiles"]])
    for profile in data["profiles"]:
        profile["clip"] = clips.get(profile["name"])
    data["clip_style"] = clip_style
    (out / "catalog.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    web = root / "vmctl" / "web"
    shutil.copy2(web / "icons.js", out / "icons.js")
    # The sprite is inlined (hidden) so <use href="#arch"> works from file:// too, not only over HTTP.
    sprite = (web / "distro-icons.svg").read_text(encoding="utf-8").replace('<svg xmlns="http://www.w3.org/2000/svg">', '<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">', 1)
    page = (PAGE.replace("__SPRITE__", sprite).replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__VMCTL__", data["vmctl_version"]).replace("__GENERATED__", data["generated"])
            .replace("__COMMIT__", f" · {data['commit']}" if data["commit"] else "").replace("__REPO__", REPO_URL))
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "site", help="output directory (default: site/)")
    parser.add_argument("--media", type=Path, default=None, help="directory with <vm>/recording.mp4|recording.gif|poster.png (default: docs/media)")
    parser.add_argument("--clip-style", choices=["combo", "gif", "video", "both"], default="combo", help="how a profile's install clip is shown on its card: combo = the GIF loops in the card and a small button opens the video player (default), gif, video (poster + play), both")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    data = build(args.root, args.out, media=args.media, clip_style=args.clip_style)
    print(f"{args.out}: {data['counts']['profiles']} profiles, {data['counts']['unattended']} unattended, {data['counts']['verified']} verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
