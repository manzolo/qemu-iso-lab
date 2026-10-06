#!/usr/bin/env python3
"""Build the catalog site: every tracked profile as a static page (GitHub Pages).

    tools/build_catalog_site.py            # -> site/ (index.html, catalog.json, icons)
    tools/build_catalog_site.py --out DIR

With docs/media/tour/tour.json (the intro clips, on the media branch like the install clips) the
site also gets tour.html: a player, the playlist and the subtitles in English and Italian.

One self-contained page: search, filters by family / kind of install / role / status, a card
per profile (facts, version and its history, the last live PASS, the commands to run) and a
basket that turns the profiles you tick into one `vmctl catalog add ...` line. catalog.json is
the same data for scripts. The icons are the dashboard's (vmctl/web/icons.js + the sprite).
Reads the tracked catalog only (never local.json) and needs no VM, ISO or QEMU.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html
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
LINUX_INSTALL = f'sh -c "$(curl -fsSL {SITE_URL}install.sh)"'
WINDOWS_INSTALL = f"irm {SITE_URL}install.ps1 | iex"
FAMILY_LABELS = {
    "debian": "Debian, Ubuntu and flavours", "arch": "Arch family", "fedora": "Fedora", "rhel": "RHEL family",
    "opensuse": "openSUSE", "nix": "NixOS", "alpine": "Alpine", "void": "Void", "mint": "Linux Mint", "kali": "Kali",
    "bsd": "BSD and pfSense", "proxmox": "Proxmox VE", "haiku": "Haiku", "reactos": "ReactOS", "windows": "Windows",
    "kolibrios": "Hobby systems", "redox": "Hobby systems", "menuetos": "Hobby systems", "serenityos": "Hobby systems",
    "slackware": "Slackware",
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
        "manual": meta.get("manual"), "automated_as": meta.get("automated_as"),
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


def labs_data(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """The tracked labs (vms/labs/<lab>/ content, or a group on a segment such as proxmox-lab):
    title, summary, members with their roles, how many exercises and tests, the guides on GitHub,
    the commands. The build adds ``clip`` when the tour has a lesson on the lab."""
    from vmctl import labs
    groups = sorted(set(labs.lab_groups(cfg)) | set(labs.content_groups()))
    found = []
    for group in groups:
        model = labs.model(cfg, group)
        content = model.get("content") or {}
        members = [{"name": m["name"], "role": str(m.get("role") or ""), "label": str(m.get("label") or m["name"])} for m in model["members"]]
        cluster = any(str((cfg["vms"][m["name"]].get("meta") or {}).get("family")) == "proxmox" for m in members)
        title = content.get("title") or (f"Proxmox VE: {len(members) - 1} nodes and a client on one segment" if cluster
                                         else f"{len(members)} machines on a shared network")
        found.append({"group": group, "title": title, "summary": content.get("summary") or "", "members": members,
                      "cluster": cluster, "exercises": len(content.get("exercises") or []), "tests": len(content.get("tests") or []),
                      "guides": {lang: f"{REPO_URL}/blob/main/{path}" for lang, path in (content.get("guides") or {}).items()},
                      "commands": [f"vmctl group install {group}", f"vmctl group test {group}", f"vmctl group map {group} --open"]})
    return found


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
            "profiles": profiles, "labs": labs_data(cfg)}


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QEMU ISO Lab · catalog</title>
<link rel="icon" type="image/svg+xml" href="qemu-iso-lab.svg">
<meta name="description" content="Every VM profile of QEMU ISO Lab: Linux, BSD and Windows on QEMU/KVM, installed with zero clicks. Pick the ones you want.">
<style>
:root { color-scheme:dark; --bg:#0b1017; --panel:#131c27; --panel2:#192432; --line:#293646; --text:#edf3fa; --muted:#9aabbe; --accent:#8aead0; --ok:#8aead0; --warn:#f1ca8a; --btn:#202d3d; }
* { box-sizing:border-box; }
html { scroll-behavior:smooth; scroll-padding-top:180px; }
body { margin:0; background:radial-gradient(ellipse 70% 650px at 80% 0%,#19333765,transparent),var(--bg); color:var(--text); font:14px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif; }
code,pre,.mono { font-family:ui-monospace,"Cascadia Code",monospace; }
a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
button,input,select { font:inherit; }
button { cursor:pointer; background:var(--btn); color:var(--text); border:1px solid var(--line); border-radius:8px; padding:7px 12px; }
button:hover { background:#2b3c50; }
:focus-visible { outline:2px solid var(--accent); outline-offset:4px; }
button.primary,.primary-link { background:var(--accent); color:#10251f; border:1px solid var(--accent); font-weight:650; }
.site-header,main { max-width:1320px; margin:auto; padding:0 36px; }
.brand { display:flex; align-items:center; gap:12px; min-height:64px; border-bottom:1px solid var(--line); }
.logo { width:36px; height:36px; border:1px solid #8aead050; border-radius:10px; display:grid; place-items:center; color:var(--accent); background:#8aead012; font:700 18px ui-monospace,monospace; }
.brand-name { color:var(--text); font-size:17px; font-weight:700; letter-spacing:-.5px; }
.brand-name b { color:var(--accent); }
.brand-label { margin-left:4px; padding-left:16px; border-left:1px solid var(--line); color:var(--muted); font-size:12px; }
.brand nav { margin-left:auto; display:flex; align-items:center; gap:24px; font-size:12px; }
.brand nav a { color:var(--muted); } .brand nav a:hover { color:var(--accent); }
.brand nav a.tour { color:var(--accent); border:1px solid #8aead040; border-radius:7px; padding:3px 10px; background:#8aead010; }
h1 { font-size:26px; line-height:1.2; letter-spacing:-.8px; margin:0 0 6px; }
.start { margin:12px 0; max-width:680px; background:var(--panel); border:1px solid var(--line); border-radius:10px; overflow:hidden; }
.setup { margin:0 0 8px; }
.setup > summary { width:fit-content; color:var(--accent); font-size:12px; }
.terminal-bar { display:flex; align-items:center; gap:6px; border-bottom:1px solid var(--line); padding:13px 17px; background:#ffffff03; }
.terminal-bar i { width:8px; height:8px; border-radius:50%; background:#ed8e86; } .terminal-bar i:nth-child(2) { background:#e5c17a; } .terminal-bar i:nth-child(3) { background:#88c4a4; }
.terminal-bar span { color:var(--muted); font:11px ui-monospace,monospace; margin:auto; padding-right:36px; }
.terminal-body { padding:19px 22px 10px; }
.terminal-step { margin-bottom:18px; }
.terminal-step small { display:block; font-size:10px; color:var(--muted); margin-bottom:5px; letter-spacing:.04em; }
.terminal-step code { display:block; font-size:11px; overflow-wrap:anywhere; color:#dbe7ef; }
.terminal-step code::before { content:"$ "; color:var(--accent); }
.terminal-note { padding:11px 22px; border-top:1px solid var(--line); color:var(--muted); font-size:10px; display:flex; justify-content:space-between; align-items:center; gap:12px; }
.terminal-note button { padding:3px 9px; font-size:10px; }
.metrics { display:flex; flex-wrap:wrap; gap:6px 16px; color:var(--muted); font-size:11px; }
.metrics strong { color:var(--text); font-weight:600; }
main { padding-bottom:180px; }
.catalog-heading { display:flex; align-items:center; justify-content:space-between; padding:22px 0 12px; gap:15px; }
.catalog-heading h2 { font-size:23px; letter-spacing:-.6px; margin:0 0 2px; }
.catalog-heading p { color:var(--muted); margin:0; font-size:12px; }
#count { color:var(--muted); font:11px ui-monospace,monospace; white-space:nowrap; }
#toolbar { position:sticky; top:0; z-index:5; background:#0b1017f5; backdrop-filter:blur(14px); padding:14px 0; border-bottom:1px solid var(--line); }
.search-row,.filter-row { display:flex; gap:14px; align-items:center; flex-wrap:wrap; }
.search-wrap { flex:1 1 280px; position:relative; }
.search-wrap::before { content:""; position:absolute; left:16px; top:15px; width:10px; height:10px; border:1.5px solid var(--muted); border-radius:50%; }
.search-wrap::after { content:""; position:absolute; left:26px; top:26px; width:5px; height:1.5px; background:var(--muted); transform:rotate(45deg); }
#search { width:100%; background:var(--panel); border:1px solid var(--line); border-radius:9px; padding:11px 40px; color:var(--text); font-size:12px; }
#search::placeholder { color:var(--muted); }
.search-wrap kbd { position:absolute; right:13px; top:12px; color:var(--muted); border:1px solid var(--line); border-radius:4px; font-size:10px; padding:0 5px; }
.filter-row { margin-top:12px; gap:10px; }
.filter-label { font-size:10px; color:var(--muted); margin-right:4px; }
.chips { display:flex; gap:5px; flex-wrap:wrap; }
.chips button { border-radius:6px; border-color:transparent; background:transparent; color:var(--muted); padding:6px 10px; font-size:11px; }
.chips button:hover { color:var(--text); background:var(--panel2); }
.chips button.active { background:#8aead015; color:var(--accent); border-color:#8aead033; }
#kind { background:var(--panel); border:1px solid var(--line); border-radius:9px; padding:4px; }
select { background:var(--panel); color:var(--text); border:1px solid var(--line); border-radius:7px; padding:6px 28px 6px 10px; font-size:11px; max-width:100%; }
#family { margin-left:auto; }
h2.family { display:flex; gap:12px; align-items:center; font-size:14px; margin:18px 0 12px; font-weight:600; letter-spacing:-.15px; }
h2.family span { font:10px ui-monospace,monospace; background:var(--panel2); color:var(--muted); padding:2px 7px; border-radius:5px; }
h2.family::after { content:""; height:1px; background:var(--line); flex:1; margin-left:4px; }
.grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:18px; align-items:start; }
.card { min-width:0; background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; transition:border-color .2s,box-shadow .2s,transform .2s; }
.card:hover { border-color:#526174; box-shadow:0 12px 30px #0003; transform:translateY(-3px); }
.card.picked { border-color:var(--accent); box-shadow:0 0 0 1px #8aead050; }
.cover { position:relative; height:66px; padding:12px 16px; overflow:hidden; display:flex; align-items:flex-end; background:radial-gradient(ellipse at 80% 90%,color-mix(in srgb,var(--tint) 30%,transparent),transparent 75%),linear-gradient(125deg,#1a2634,#101820); border-bottom:1px solid #ffffff09; }
.cover::before { content:""; position:absolute; width:180px; height:180px; right:-20px; top:-30px; border:1px solid #ffffff0c; border-radius:50%; box-shadow:0 0 0 27px #ffffff03,0 0 0 54px #ffffff02; }
.cover > .catalog-icon { position:absolute; right:54px; top:10px; width:44px; height:44px; background:transparent; box-shadow:none; border-radius:0; color:color-mix(in srgb,var(--tint) 60%,white); transform:rotate(-10deg); }
.cover > .catalog-icon svg { width:40px; height:40px; filter:drop-shadow(0 8px 14px #0004); }
.cover-label { color:#d4dee9; text-transform:uppercase; letter-spacing:.15em; font-size:9px; z-index:1; }
.cover-top { position:absolute; top:14px; left:16px; color:#c3cfda; font-size:9px; display:flex; align-items:center; gap:6px; }
.cover-top::before { content:""; width:5px; height:5px; border-radius:50%; background:var(--tint); }
.pick { position:absolute; right:12px; top:12px; width:29px; height:29px; display:grid; place-items:center; border-radius:8px; font-size:19px; line-height:1; padding:0; background:#0b101766; border-color:#ffffff26; z-index:2; }
.card.picked .pick { background:var(--accent); color:#10251f; border-color:var(--accent); }
.card-body { padding:17px; display:flex; flex-direction:column; gap:13px; }
.card.lab .desc { font-size:12px; color:var(--muted); line-height:1.5; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }
.card.lab .members { list-style:none; margin:0; padding:0; display:grid; gap:4px; font-size:11px; }
.card.lab .members li { display:flex; justify-content:space-between; gap:10px; } .card.lab .members span { color:var(--muted); }
.lab-links { display:flex; gap:12px; flex-wrap:wrap; font-size:12px; align-items:center; }
.lab-links .lesson { font-weight:650; border:1px solid #8aead040; border-radius:7px; padding:4px 10px; background:#8aead010; }
.lab-links .src { color:var(--muted); margin-left:auto; }
.labs-lead { color:var(--muted); font-size:12px; margin:-4px 0 14px; }
.labs-folded { display:flex; align-items:center; justify-content:space-between; gap:10px 16px; flex-wrap:wrap; margin:0 0 18px; padding:10px 14px; border:1px dashed var(--line); border-radius:10px; color:var(--muted); font-size:13px; }
.labs-folded b { color:var(--text); }
#labs[hidden] { display:none; }
.card-head { min-width:0; min-height:48px; }
.card-head .name { font-weight:650; font-size:15px; letter-spacing:-.25px; overflow-wrap:anywhere; line-height:1.4; }
.card-head .desc { color:var(--muted); font-size:11px; margin-top:4px; overflow-wrap:anywhere; }
.badges { display:flex; gap:5px; flex-wrap:wrap; font-size:10px; min-height:23px; align-items:flex-start; }
.badge { border:1px solid #ffffff10; background:#ffffff04; border-radius:5px; padding:2px 6px; font-weight:500; }
.b-link-ok { color:var(--ok); } .b-link-warn { color:var(--warn); border-color:#f1ca8a55; } .b-link-bad { color:#f39a9a; border-color:#f39a9a66; background:#f39a9a12; }
.b-unattended,.b-verified { color:var(--ok); background:#8aead009; } .b-manual,.b-base { color:var(--muted); } .b-manual a { color:var(--accent); } .b-todo { color:var(--warn); border-color:#f1ca8a55; background:#f1ca8a10; } .b-experimental,.b-medium { color:var(--warn); } .b-version { color:#aec4f0; } .b-lab { color:#d1b1f7; }
.facts { display:flex; gap:15px; flex-wrap:wrap; color:var(--muted); font-size:11px; padding:11px 0; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }
.facts b { color:var(--text); font-weight:550; }
.cmds { background:#0b111a; border:1px solid #253242; border-radius:7px; overflow:hidden; }
.cmd-bar { display:flex; justify-content:space-between; align-items:center; padding:5px 9px; border-bottom:1px solid #ffffff07; color:var(--muted); font-size:9px; }
.cmds pre { margin:0; padding:9px; white-space:pre-wrap; overflow-wrap:anywhere; font-size:11px; line-height:1.8; color:#c5d6e7; }
.cmds button { border:0; background:transparent; padding:1px 4px; font-size:9px; color:var(--accent); }
.card-details { display:flex; flex-wrap:wrap; gap:8px 15px; }
details { font-size:11px; color:var(--muted); min-width:0; } details[open] { flex-basis:100%; }
details summary { cursor:pointer; } details summary:hover { color:var(--text); }
details p,details ul { margin:8px 0 0; overflow-wrap:anywhere; }
.history { padding:0; } .history li { list-style:none; margin-top:7px; } .history .v { color:#aec4f0; margin-right:6px; }
.catalog-icon { display:inline-flex; align-items:center; justify-content:center; flex-shrink:0; width:40px; height:40px; color:var(--icon-ink,#fff); background:var(--icon-color,var(--accent)); border-radius:10px; }
.catalog-icon svg { width:24px; height:24px; }
.clip { position:relative; border-radius:8px; overflow:hidden; background:#000; border:1px solid #253242; aspect-ratio:16/10; }
.clip img,.clip video { display:block; width:100%; height:100%; object-fit:contain; }
.clip .play { position:absolute; inset:0; display:grid; place-items:center; background:linear-gradient(180deg,transparent 40%,#000a); color:#fff; border:0; padding:0; }
.clip .play.mini { inset:auto 8px 8px auto; display:inline-flex; gap:6px; background:#172e28; border:1px solid #8aead080; color:var(--accent); border-radius:6px; padding:5px 10px; font-size:11px; }
.clip .play.mini::before { content:"▶"; }
.clip .play span { width:48px; height:48px; border-radius:50%; background:var(--accent); display:grid; place-items:center; }
.clip .play span::after { content:"▶"; color:#10251f; margin-left:3px; }
.clip .play b { position:absolute; left:10px; bottom:8px; font-size:11px; }
.clip .tag { position:absolute; top:8px; left:8px; font-size:9px; color:#fff; background:#0009; padding:2px 6px; border-radius:4px; }
dialog#player { background:var(--bg); color:var(--text); border:1px solid var(--line); border-radius:14px; padding:0; width:min(1100px,94vw); box-shadow:0 30px 100px #000c; }
dialog#player::backdrop { background:#000c; backdrop-filter:blur(6px); }
dialog#player header { display:flex; align-items:center; flex-wrap:wrap; gap:12px; padding:12px 16px; border-bottom:1px solid var(--line); }
dialog#player .t { font-weight:600; } dialog#player .s { color:var(--muted); font-size:12px; }
dialog#player header button { margin-left:auto; }
dialog#player video { display:block; width:100%; max-height:76vh; background:#000; }
dialog#player footer { padding:10px 16px; display:flex; gap:14px; flex-wrap:wrap; }
#basket { position:fixed; left:24px; right:24px; bottom:16px; max-width:1248px; margin:auto; z-index:6; background:#17252ef5; backdrop-filter:blur(16px); border:1px solid #8aead060; border-radius:12px; box-shadow:0 12px 50px #0008; padding:14px 18px; display:none; gap:12px; align-items:center; flex-wrap:wrap; }
#basket.on { display:flex; }
#basket-count { font-size:12px; font-weight:600; }
#basket .cmd { flex:1 1 320px; min-width:0; background:#0b111a; border:1px solid var(--line); border-radius:7px; padding:8px 10px; font-size:11px; white-space:pre-wrap; overflow-wrap:anywhere; max-height:100px; overflow:auto; }
#basket small { color:var(--muted); flex-basis:100%; font-size:10px; }
footer { color:var(--muted); font-size:11px; padding:30px 0 0; }
.site-footer { border-top:1px solid var(--line); margin-top:40px; display:flex; flex-wrap:wrap; gap:12px 36px; justify-content:space-between; }
.site-footer details { max-width:760px; }
.empty { text-align:center; padding:65px 20px; color:var(--muted); border:1px dashed var(--line); border-radius:12px; margin-top:24px; }
.empty strong { display:block; color:var(--text); font-size:18px; margin-bottom:8px; }
.empty button { margin-top:15px; }
@media (min-width:1500px) { .site-header,main { max-width:1440px; } }
@media (max-width:1050px) { .grid { grid-template-columns:repeat(2,minmax(0,1fr)); } .search-row { gap:10px; } #kind { flex-shrink:0; } }
@media (max-width:760px) { .site-header,main { padding-left:22px; padding-right:22px; } .brand nav { gap:14px; } .brand-label { display:none; } #kind { width:100%; } #kind button { flex:1; } .filter-label { display:none; } .filter-row { gap:8px; } }
@media (max-width:520px) { .site-header,main { padding-left:16px; padding-right:16px; } .brand nav a.doc { display:none; } h1 { font-size:23px; } .grid { grid-template-columns:1fr; } .catalog-heading { align-items:flex-start; gap:8px; } #count { padding-top:5px; font-size:10px; } .chips button { padding:6px 8px; font-size:10px; } #family { flex:1 1 100%; margin:0; } #basket { left:8px; right:8px; bottom:8px; padding:12px; gap:8px; } #basket .cmd { order:3; flex-basis:100%; } #basket small { order:4; } #basket-count { margin-right:auto; } .terminal-body { padding:18px 16px 8px; } }
@media (prefers-reduced-motion:reduce) { html { scroll-behavior:auto; } .card { transition:none; } .card:hover { transform:none; } }
</style>
</head>
<body>
<header class="site-header">
  <div class="brand"><a class="logo" href="__REPO__" aria-label="QEMU ISO Lab repository">&gt;_</a><a class="brand-name" href="__REPO__">QEMU <b>ISO Lab</b></a><span class="brand-label">THE VM CATALOG</span><nav aria-label="Main navigation">__TOUR_NAV__<a href="#labs">Labs</a><a class="doc" href="__REPO__#readme">Documentation</a><a href="__REPO__">GitHub ↗</a></nav></div>
</header>
<main id="catalog">
  <div class="catalog-heading"><div><h1>Find your next machine</h1><div class="metrics"><span><strong id="m-profiles">—</strong> profiles</span><span><strong id="m-unattended">—</strong> automated</span><span><strong id="m-verified">—</strong> verified</span></div></div><span id="count" role="status" aria-live="polite"></span></div>
  <details class="setup" open><summary>Install with one command</summary>
    <div class="start" id="get-started"><div class="terminal-bar"><i></i><i></i><i></i><span>your lab starts here</span></div><div class="terminal-body">
      <div class="terminal-step"><small>Linux / Terminal · curl required</small><code>__LINUX_INSTALL__</code><button data-copy="__LINUX_INSTALL__">Copy Linux</button></div>
      <div class="terminal-step"><small>Windows 11 / Double-click installer · WSL2 + Ubuntu</small><a href="install-windows.cmd" download="Install QEMU ISO Lab.cmd">Download Windows installer (.cmd)</a><small>Or paste into PowerShell:</small><code>__WINDOWS_INSTALL__</code><button data-copy="__WINDOWS_INSTALL__">Copy Windows</button></div>
    </div><div class="terminal-note"><span>Then open QEMU ISO Lab from your applications menu (Linux), desktop or Start menu (Windows). Linux terminal: <code>qemu-iso-lab</code>. Keep its terminal window open while using the dashboard.</span></div><div class="terminal-note"><span>Installs into ~/qemu-iso-lab. Setup asks before installing host tools. On Windows, rerun the installer after WSL setup or a reboot.</span></div>__TOUR_START__<div class="terminal-note"><span>Read the scripts: <a href="install.sh">Linux</a> · <a href="install.ps1">Windows</a></span><a href="__REPO__#quick-start">Manual setup ↗</a></div></div>
  </details>
  <div id="toolbar">
    <div class="search-row"><div class="search-wrap"><input id="search" type="search" placeholder="Search profiles… e.g. ubuntu 26, fedora kde" autocomplete="off" aria-label="Search profiles"><kbd aria-hidden="true">/</kbd></div>
    <div class="chips" id="kind" role="group" aria-label="Kind of install"><button data-v="" class="active" aria-pressed="true">All profiles</button><button data-v="unattended" aria-pressed="false">Automated</button><button data-v="manual" aria-pressed="false">Manual</button><button data-v="experimental" aria-pressed="false">Experimental</button><button data-v="todo" aria-pressed="false" title="Manual profiles whose automation is still to write">To automate</button><button data-v="links" id="links-chip" aria-pressed="false" title="Profiles whose ISO download link is broken or answers only from an alternate source (checked by tools/check_iso_urls.py)">Broken links</button></div></div>
    <div class="filter-row"><span class="filter-label">BUILT FOR</span><div class="chips" id="role" role="group" aria-label="Role"><button data-v="" class="active" aria-pressed="true">Any role</button><button data-v="desktop" aria-pressed="false">Desktop</button><button data-v="server" aria-pressed="false">Server</button><button data-v="other" aria-pressed="false">Other</button></div>
    <select id="family" aria-label="Family"><option value="">All OS families</option></select></div>
  </div>
  <section id="labs" aria-label="Labs"></section>
  <div id="list"></div>
  <footer class="site-footer"><span>QEMU ISO Lab · vmctl __VMCTL__<br>Updated __GENERATED____COMMIT__</span><details><summary>About this catalog &amp; verification</summary><p>Generated from <a href="__REPO__/tree/main/vms/profiles">vms/profiles</a> and <a href="__REPO__/blob/main/vms/profiles.lock">profiles.lock</a>. Versions: a <i>patch</i> fixes the recipe, a <i>minor</i> adds something, a <i>major</i> means an installed VM is no longer comparable. “Verified live” is the last date the maintainer's validation matrix reinstalled the profile from scratch and it passed.</p></details><a href="catalog.json">Catalog JSON ↗</a></footer>
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
if (!DATA.profiles.some(p => p.link && p.link.verdict !== "OK")) $("links-chip").style.display = "none";
$("m-profiles").textContent = DATA.counts.profiles; $("m-unattended").textContent = DATA.counts.unattended; $("m-verified").textContent = DATA.counts.verified;
// Sections and the family filter go by label: several families can share one ("Hobby systems").
const LABELS = [...new Set(DATA.families.map(f => DATA.profiles.find(p => p.family === f)?.family_label).filter(Boolean))];
for (const l of LABELS) { const o = document.createElement("option"); o.value = l; o.textContent = l + " (" + DATA.profiles.filter(p => p.family_label === l).length + ")"; $("family").appendChild(o); }
const ram = (p) => p.memory_mb >= 1024 ? (p.memory_mb / 1024) + " GB" : p.memory_mb + " MB";
const roleGroup = (p) => ["desktop", "server"].includes(p.role) ? p.role : "other";
// Match the profile identity, never incidental mentions in notes or a broad family label.
// Keep numeric release queries out of metadata (dates and recipe versions are not OS releases).
function searchProfiles(profiles, query) {
  const normalize = value => String(value || "").toLowerCase().replace(/[-_]+/g, " ").replace(/\s+/g, " ").trim();
  const phrase = normalize(query), words = phrase.split(" ").filter(Boolean);
  if (!words.length) return profiles;
  return profiles.map(p => {
    const name = normalize(p.name), label = normalize(p.label), identity = name + " " + label;
    const tags = normalize([...(p.groups || []), p.role, p.family].join(" "));
    if (!words.every(w => identity.includes(w) || (!/\d/.test(w) && tags.includes(w)))) return null;
    const score = (name === phrase ? 1000 : name.startsWith(phrase) ? 500 : label.startsWith(phrase) ? 400 : 0)
      + words.reduce((sum, w) => sum + (name.split(" ").some(t => t.startsWith(w)) ? 20 : identity.includes(w) ? 10 : 0), 0);
    return {profile:p, score};
  }).filter(Boolean).sort((a, b) => b.score - a.score || a.profile.name.localeCompare(b.profile.name, undefined, {numeric:true})).map(r => r.profile);
}
function visible() {
  return searchProfiles(DATA.profiles, $("search").value).filter(p =>
    (!kind || (kind === "todo" ? p.manual === "todo" : kind === "links" ? (p.link && p.link.verdict !== "OK") : p.status === kind)) && (!role || roleGroup(p) === role) && (!family || p.family_label === family));
}
function clipBlock(p) {
  const c = p.clip; if (!c) return "";
  const style = DATA.clip_style, parts = [];
  if (style === "combo") {
    if (c.gif) return `<div class="clip"><span class="tag">install</span><img src="${esc(c.gif)}" alt="Time-lapse of the ${esc(p.name)} install" loading="lazy" decoding="async">${c.mp4 ? `<button class="play mini" data-play="${esc(p.name)}" aria-label="Play the install video of ${esc(p.name)}">Video</button>` : ""}</div>`;
    if (c.mp4) return `<div class="clip"><span class="tag">install · video</span><img src="${esc(c.poster || "")}" alt="" loading="lazy" decoding="async"><button class="play" data-play="${esc(p.name)}" aria-label="Play the install of ${esc(p.name)}"><span></span><b>Watch the install</b></button></div>`;
    return "";
  }
  if ((style === "gif" || style === "both") && c.gif) parts.push(`<div class="clip"><span class="tag">install · gif</span><img src="${esc(c.gif)}" alt="Time-lapse of the ${esc(p.name)} install" loading="lazy" decoding="async"></div>`);
  if ((style === "video" || style === "both") && c.mp4) parts.push(`<div class="clip"><span class="tag">install · video</span><img src="${esc(c.poster || c.gif || "")}" alt="" loading="lazy" decoding="async"><button class="play" data-play="${esc(p.name)}" aria-label="Play the install of ${esc(p.name)}"><span></span><b>Watch the install</b></button></div>`);
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
const familyColors = {debian:"#de769b",arch:"#60b5e8",fedora:"#749cf5",rhel:"#76bcc8",opensuse:"#8dc96e",nix:"#8aafe8",alpine:"#65afcc",void:"#91bf7f",mint:"#9fcf7c",kali:"#90a5de",bsd:"#eb9681",windows:"#70b8f8",proxmox:"#ecaa71",slackware:"#7fa6d9",haiku:"#f0c25a",reactos:"#6fb3e0"};
function card(p) {
  // A manual profile says why (meta.manual): live media, a disk image, an import template, a CI boot
  // check, the manual twin of an automated profile, or still to automate (the amber badge, filter "To automate").
  const manualLabel = {live:"Manual · live media", image:"Manual · disk image", template:"Manual · import template", ci:"Manual · CI boot check", twin:"Manual · automated as", todo:"Awaiting automation"};
  const statusBadge = p.status === "unattended" ? `<span class="badge b-unattended">Auto install</span>`
    : p.status === "manual" ? (p.manual === "todo" ? `<span class="badge b-todo" title="Manual for now: the notes say which flow is the road to unattended">Awaiting automation</span>`
      : p.manual === "twin" ? `<span class="badge b-manual" title="The same system installs itself as ${esc(p.automated_as)}">Manual · automated as <a href="#${esc(p.automated_as)}" data-goto="${esc(p.automated_as)}">${esc(p.automated_as)}</a></span>`
      : `<span class="badge b-manual">${esc(manualLabel[p.manual] || "Manual install")}</span>`)
    : `<span class="badge b-${esc(p.status)}">${esc(p.status)}</span>`;
  const badges = [statusBadge];
  if (p.version) badges.push(`<span class="badge b-version" title="Profile version">v${esc(p.version)}</span>`);
  if (p.verified) badges.push(`<span class="badge b-verified" title="Last live PASS of the validation matrix">✓ ${esc(p.verified)}</span>`);
  if (p.medium === "manual") badges.push(`<span class="badge b-medium" title="The profile says which medium to provide">Your own ISO</span>`);
  if (p.link) {
    const when = DATA.links_checked ? ` (checked ${DATA.links_checked})` : "";
    if (p.link.verdict === "OK") badges.push(`<span class="badge b-link-ok" title="The ISO download link answers${esc(when)}">ISO link ✓</span>`);
    else if (p.link.verdict === "DEGRADED") badges.push(`<span class="badge b-link-warn" title="Only an alternate source answers${esc(when)}: ${esc(p.link.why)}">ISO link: alternate only</span>`);
    else badges.push(`<span class="badge b-link-bad" title="No download source answers${esc(when)}: ${esc(p.link.why)}">ISO link broken</span>`);
  }
  if (p.lab) badges.push(`<span class="badge b-lab">Lab member</span>`);
  const facts = [`<b>${esc(ram(p))}</b> RAM`, `<b>${esc(p.cpus)}</b> vCPU`, `<b>${esc(p.firmware)}</b>`, p.disk ? `<b>${esc(p.disk)}</b> disk` : "", p.ssh ? "SSH" : "", p.shared_dir ? "Shared folder" : ""].filter(Boolean);
  const history = p.history.length ? `<details><summary>Changelog · ${p.history.length}</summary><ul class="history">${p.history.map(h => `<li><span class="v">${esc(h.version)}</span>${esc(h.date)} · ${esc(h.note)}</li>`).join("")}</ul></details>` : "";
  const notes = p.notes || p.extends ? `<details><summary>Profile notes</summary>${p.extends ? `<p>Based on <code>${esc(p.extends)}</code>.</p>` : ""}${p.notes ? `<p>${esc(p.notes)}</p>` : ""}</details>` : "";
  const help = p.iso_help ? `<details><summary>Install medium</summary><p>${esc(p.iso_help)}</p></details>` : "";
  const clips = clipBlock(p);
  return `<article class="card${picked.has(p.name) ? " picked" : ""}" id="${esc(p.name)}" data-name="${esc(p.name)}" style="--tint:${familyColors[p.family] || "#b3a0df"}">
    <div class="cover"><span class="cover-top">${esc(p.role || "Virtual machine")}</span>${catalogIcon(osIconKey(p))}<span class="cover-label">${esc(p.family_label)}</span><button class="pick" data-pick="${esc(p.name)}" aria-label="${picked.has(p.name) ? "Remove" : "Select"} ${esc(p.name)}" aria-pressed="${picked.has(p.name)}">${picked.has(p.name) ? "✓" : "+"}</button></div>
    <div class="card-body"><div class="card-head"><div class="name">${esc(p.label)}</div><div class="desc mono">${esc(p.name)}</div></div>
    <div class="badges">${badges.join("")}</div><div class="facts">${facts.map(f => `<span>${f}</span>`).join("")}</div>
    <div class="cmds"><div class="cmd-bar"><span>RUN IN YOUR TERMINAL</span><button data-copy="${esc(p.commands.join("\n"))}" aria-label="Copy commands for ${esc(p.name)}">Copy ↗</button></div><pre>${p.commands.map(esc).join("\n")}</pre></div>
    ${clips}<div class="card-details">${history}${notes}${help}</div></div>
  </article>`;
}
// A lab: its members (each a link to its own card), exercises, tests, guides, and the tour's lesson on it.
function labCard(l) {
  const facts = [`<b>${l.members.length}</b> machine${l.members.length === 1 ? "" : "s"}`, l.exercises ? `<b>${l.exercises}</b> exercises` : "", l.tests ? `<b>${l.tests}</b> tests` : "", l.cluster ? "cluster" : ""].filter(Boolean);
  const members = l.members.map(m => `<li><a href="#${esc(m.name)}" data-goto="${esc(m.name)}" class="mono">${esc(m.name)}</a>${m.role ? `<span>${esc(m.role)}</span>` : ""}</li>`).join("");
  const guides = Object.entries(l.guides).map(([lang, url]) => `<a href="${esc(url)}">Guide ${lang.toUpperCase()}</a>`).join("") + (l.source ? `<a href="${esc(l.source)}" target="_blank" rel="noopener" class="src">Source ↗</a>` : "");
  const lesson = (l.clips || []).map((c, i, all) => `<a class="lesson" href="${esc(c.href)}" title="${esc((c.title || {}).en || "")}">▶ ${all.length > 1 ? esc(((c.title || {}).en || "Lesson").replace(/^.*?:\s*/, "")) : "Watch the lesson"}</a>`).join("");
  return `<article class="card lab" id="lab-${esc(l.group)}" style="--tint:#8aead0">
    <div class="cover"><span class="cover-top">${l.cluster ? "Cluster" : "Lab"}</span>${catalogIcon(l.cluster ? "cluster" : "network")}<span class="cover-label">${esc(l.group)}</span></div>
    <div class="card-body"><div class="card-head"><div class="name">${esc(l.title)}</div>${l.summary ? `<div class="desc">${esc(l.summary)}</div>` : ""}</div>
    <div class="facts">${facts.map(f => `<span>${f}</span>`).join("")}</div><ul class="members">${members}</ul>
    <div class="cmds"><div class="cmd-bar"><span>RUN IN YOUR TERMINAL</span><button data-copy="${esc(l.commands.join("\n"))}" aria-label="Copy commands for ${esc(l.group)}">Copy ↗</button></div><pre>${l.commands.map(esc).join("\n")}</pre></div>
    <div class="lab-links">${lesson}${guides}</div></div>
  </article>`;
}
// The labs sit between the filters and the profiles and no filter applies to them (a lab is no
// desktop or server): with every card still on top, a click on Desktop seemed to change nothing
// but the count (Manzolo, 2026-10-06). While a filter is on they fold into one line that leads
// back to them; a search hides them as before.
const filtering = () => !!(kind || role || family);
function resetFilters() {
  $("search").value = ""; $("family").value = ""; kind = role = family = "";
  for (const group of ["kind", "role"]) for (const b of $(group).children) { b.classList.toggle("active", b.dataset.v === ""); b.setAttribute("aria-pressed", b.dataset.v === ""); }
  render();
}
function renderLabs() {
  const searching = !!$("search").value.trim();
  $("labs").hidden = searching || !DATA.labs.length;
  if (!searching && filtering()) {
    $("labs").innerHTML = `<p class="labs-folded"><span><b>${DATA.labs.length} labs</b> are left out while a filter is on: each holds several machines.</span><button data-show-labs>Show the labs</button></p>`;
    return;
  }
  $("labs").innerHTML = searching ? "" : `<h2 class="family">Labs <span>${DATA.labs.length}</span></h2><p class="labs-lead">Several machines on a private network, installed and started as one stack, with a guide, exercises and tests. <a href="__REPO__/blob/main/docs/LABS.md">About labs ↗</a> · make your own with <code>vmctl group new</code>.</p><div class="grid">${DATA.labs.map(labCard).join("")}</div>`;
}
function render() {
  renderLabs();
  const rows = visible(), byFamily = new Map();
  for (const p of rows) { if (!byFamily.has(p.family_label)) byFamily.set(p.family_label, []); byFamily.get(p.family_label).push(p); }
  $("count").textContent = `${rows.length} / ${DATA.profiles.length} profiles`;
  $("list").innerHTML = rows.length ? ($("search").value.trim() ? `<h2 class="family">Search results <span>${rows.length}</span></h2><div class="grid">${rows.map(card).join("")}</div>` : [...byFamily].sort(([a], [b]) => LABELS.indexOf(a) - LABELS.indexOf(b)).map(([label, ps]) => `<h2 class="family">${esc(label)} <span>${ps.length}</span></h2><div class="grid">${ps.map(card).join("")}</div>`).join("")) : `<div class="empty"><strong>No matching machines</strong>Try another search or give your filters a little more room.<br><button data-reset>Reset filters</button></div>`;
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
  if (pick) { const n = pick.dataset.pick; picked.has(n) ? picked.delete(n) : picked.add(n); const c = pick.closest(".card"); c.classList.toggle("picked", picked.has(n)); pick.textContent = picked.has(n) ? "✓" : "+"; pick.setAttribute("aria-pressed", picked.has(n)); pick.setAttribute("aria-label", `${picked.has(n) ? "Remove" : "Select"} ${n}`); renderBasket(); }
  if (copyButton) copy(copyButton.dataset.copy, copyButton);
  if (e.target.closest("[data-reset]")) { resetFilters(); $("search").focus(); }
  // "Show the labs", and the header's Labs link while a filter or a search hides them.
  const toLabs = e.target.closest('[data-show-labs], nav a[href="#labs"]');
  if (toLabs && (filtering() || $("search").value.trim())) { e.preventDefault(); resetFilters(); $("labs").scrollIntoView({ behavior: "smooth", block: "start" }); history.replaceState(null, "", "#labs"); }
  const play = e.target.closest("[data-play]"); if (play) openPlayer(play.dataset.play);
});
for (const group of ["kind", "role"]) $(group).addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; [...$(group).children].forEach(x => { x.classList.toggle("active", x === b); x.setAttribute("aria-pressed", x === b); }); if (group === "kind") kind = b.dataset.v; else role = b.dataset.v; render(); });
$("family").addEventListener("change", () => { family = $("family").value; render(); });
$("search").addEventListener("input", render);
$("basket-copy").onclick = () => copy($("basket-cmd").textContent, $("basket-copy"));
$("basket-clear").onclick = () => { picked.clear(); render(); };
document.addEventListener("keydown", (e) => { if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey && !$("player").open && !document.activeElement.matches("input,textarea,select,[contenteditable]")) { e.preventDefault(); $("search").focus(); } });
render();
</script>
</body>
</html>
"""


TOUR_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QEMU ISO Lab · tour</title>
<link rel="icon" type="image/svg+xml" href="qemu-iso-lab.svg">
<meta name="description" content="Short clips, spoken and subtitled in English and Italian: the catalog, the setup, a first VM, the console in the browser, a lab, and a lab of your own.">
<style>
:root { color-scheme:dark; --bg:#0b1017; --panel:#131c27; --panel2:#192432; --line:#293646; --text:#edf3fa; --muted:#9aabbe; --accent:#8aead0; --btn:#202d3d; }
* { box-sizing:border-box; }
body { margin:0; background:radial-gradient(ellipse 70% 650px at 80% 0%,#19333765,transparent),var(--bg); color:var(--text); font:14px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif; }
a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
button { font:inherit; cursor:pointer; background:var(--btn); color:var(--text); border:1px solid var(--line); border-radius:8px; padding:6px 12px; }
:focus-visible { outline:2px solid var(--accent); outline-offset:3px; }
.site-header,main { max-width:1320px; margin:auto; padding:0 36px; }
.brand { display:flex; align-items:center; gap:12px; min-height:64px; border-bottom:1px solid var(--line); }
.logo { width:36px; height:36px; border:1px solid #8aead050; border-radius:10px; display:grid; place-items:center; color:var(--accent); background:#8aead012; font:700 18px ui-monospace,monospace; }
.brand-name { color:var(--text); font-size:17px; font-weight:700; letter-spacing:-.5px; } .brand-name b { color:var(--accent); }
.brand-label { margin-left:4px; padding-left:16px; border-left:1px solid var(--line); color:var(--muted); font-size:12px; }
.brand nav { margin-left:auto; display:flex; align-items:center; gap:20px; font-size:12px; } .brand nav a { color:var(--muted); }
.head { display:flex; align-items:flex-end; justify-content:space-between; gap:16px; padding:26px 0 18px; flex-wrap:wrap; }
h1 { font-size:30px; letter-spacing:-.8px; margin:0; line-height:1.2; }
.head p { color:var(--muted); margin:6px 0 0; max-width:640px; }
.lang { display:flex; gap:4px; background:var(--panel); border:1px solid var(--line); border-radius:9px; padding:4px; }
.lang button { border:0; background:transparent; color:var(--muted); padding:5px 12px; font-size:12px; }
.lang button[aria-pressed=true] { background:#8aead018; color:var(--accent); }
.layout { display:grid; grid-template-columns:minmax(0,1fr) 340px; gap:22px; align-items:start; padding-bottom:60px; }
.player { background:#000; border:1px solid var(--line); border-radius:14px; overflow:hidden; }
video { display:block; width:100%; aspect-ratio:16/9; background:#000; }
.now { padding:14px 18px; background:var(--panel); border-top:1px solid var(--line); display:flex; justify-content:space-between; gap:12px; align-items:center; }
.now h2 { font-size:17px; margin:0; } .now span { color:var(--muted); font-size:12px; white-space:nowrap; }
ol { list-style:none; margin:0; padding:0; display:grid; gap:10px; }
ol button { width:100%; display:grid; grid-template-columns:120px 1fr; gap:12px; text-align:left; padding:8px; border-radius:11px; background:var(--panel); align-items:center; }
ol button:hover { background:var(--panel2); }
ol button[aria-current=true] { border-color:var(--accent); background:#8aead00d; }
ol img { width:120px; aspect-ratio:16/9; object-fit:cover; border-radius:7px; display:block; background:#000; }
ol b { display:block; font-size:13px; line-height:1.3; } ol small { color:var(--muted); font-size:11px; }
ol li.series h3 { margin:10px 0 2px; font-size:11px; text-transform:uppercase; letter-spacing:.12em; color:var(--muted); } ol li.series:first-child h3 { margin-top:0; }
.note { color:var(--muted); font-size:12px; margin-top:12px; }
.follow { margin:12px 0 0; font-size:13px; color:var(--muted); } .follow a { font-weight:650; border:1px solid #8aead040; border-radius:7px; padding:3px 10px; background:#8aead010; } .follow a.now-lang { background:#8aead025; }
.follow[hidden] { display:none; }
.steps { margin-top:14px; background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; } .steps[hidden] { display:none; }
.steps-now { display:grid; grid-template-columns:auto 1fr auto; gap:10px 14px; align-items:center; padding:10px 14px; border-bottom:1px solid var(--line); }
.steps-now span { grid-column:1 / -1; font-size:10px; letter-spacing:.12em; text-transform:uppercase; color:var(--muted); }
.steps-now code { font:12.5px/1.5 ui-monospace,"Cascadia Code",monospace; color:#dbe7ef; white-space:pre-wrap; overflow-wrap:anywhere; }
.steps-now button { padding:4px 11px; font-size:12px; } .steps-now button:disabled { opacity:.5; cursor:default; }
.steps ol { list-style:none; margin:0; padding:6px 0; max-height:168px; overflow:auto; display:block; }
.steps ol button { display:grid; grid-template-columns:44px 1fr; gap:10px; width:100%; text-align:left; background:transparent; border:0; border-radius:0; padding:4px 14px; color:var(--muted); font-size:12px; }
.steps ol button:hover { background:var(--panel2); } .steps ol button span { font:11px ui-monospace,monospace; color:var(--muted); }
.steps ol button code { font:12px/1.45 ui-monospace,"Cascadia Code",monospace; color:#c8d4e0; white-space:pre-wrap; overflow-wrap:anywhere; }
.steps ol button[aria-current="true"] { background:#8aead012; } .steps ol button[aria-current="true"] code { color:var(--accent); }
@media (max-width:980px) { .layout { grid-template-columns:1fr; } }
@media (max-width:560px) { .site-header,main { padding:0 16px; } .brand-label { display:none; } h1 { font-size:24px; } ol button { grid-template-columns:96px 1fr; } ol img { width:96px; } }
</style>
</head>
<body>
<header class="site-header">
  <div class="brand"><a class="logo" href="__REPO__" aria-label="QEMU ISO Lab repository">&gt;_</a><a class="brand-name" href="./">QEMU <b>ISO Lab</b></a><span class="brand-label">THE TOUR</span><nav aria-label="Main navigation"><a href="./">Catalog</a><a href="__REPO__">GitHub ↗</a></nav></div>
</header>
<main>
  <div class="head"><div><h1 id="t-title"></h1><p id="t-lead"></p></div>
    <div class="lang" role="group" aria-label="Language"><button data-lang="en">English</button><button data-lang="it">Italiano</button></div></div>
  <div class="layout">
    <section><div class="player"><video id="video" controls playsinline preload="metadata"></video>
      <div class="now"><h2 id="now-title"></h2><span id="now-meta"></span></div></div>
      <p class="follow" id="t-follow" hidden></p>
      <section class="steps" id="steps" hidden><div class="steps-now"><span id="steps-label"></span><code id="step-now"></code><button id="step-copy">Copy</button></div><ol id="step-list"></ol></section>
      <p class="note" id="t-note"></p></section>
    <ol id="list" aria-label="Clips"></ol>
  </div>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const TOUR = JSON.parse(document.getElementById("data").textContent);
const TEXT = {
  en: { steps: "Command at this moment", stepsNone: "Commands appear here as they are typed", copied: "Copied", follow: "Follow along with the guide:", series: { tour: "The tour", labs: "Lab lessons" }, title: "Take the tour", lead: "Eight short chapters, from the catalog to the network map, then lessons on single labs. Spoken and subtitled in English and Italian (the language switch changes both); each chapter plays on into the next.", note: "Recorded on a real install of qemu-iso-lab; the waits are sped up and marked. The voice is synthetic (XTTS-v2).", clip: "Clip", of: "of" },
  it: { steps: "Comando di questo momento", stepsNone: "I comandi compaiono qui man mano che vengono battuti", copied: "Copiato", follow: "Segui la guida passo passo:", series: { tour: "Il tour", labs: "Lezioni dei lab" }, title: "Il tour", lead: "Otto capitoli brevi, dal catalogo alla mappa di rete, poi le lezioni sui singoli lab. Voce e sottotitoli in italiano e in inglese (il cambio di lingua cambia entrambi); ogni capitolo prosegue nel successivo.", note: "Registrate su un'installazione vera di qemu-iso-lab; le attese sono accelerate e segnalate. La voce è sintetica (XTTS-v2).", clip: "Clip", of: "di" },
};
let lang = "en", current = 0;
try { lang = localStorage.getItem("qil-tour-lang") || ((navigator.language || "en").startsWith("it") ? "it" : "en"); } catch { lang = (navigator.language || "en").startsWith("it") ? "it" : "en"; }
const $ = (id) => document.getElementById(id);
const video = $("video");
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]); }
function tracks() {
  video.querySelectorAll("track").forEach((t) => t.remove());
  const clip = TOUR.clips[current];
  for (const code of ["en", "it"]) {
    const t = document.createElement("track");
    t.kind = "subtitles"; t.srclang = code; t.label = code === "en" ? "English" : "Italiano"; t.src = clip.subtitles[code];
    video.appendChild(t);
  }
  showLang();
}
// One subtitle track on screen: the mode is set on each <track>'s own TextTrack.
function showLang() { video.querySelectorAll("track").forEach((el) => { el.track.mode = el.srclang === lang ? "showing" : "disabled"; }); }
function load(index, play) {
  current = index;
  const clip = TOUR.clips[index];
  video.src = videoOf(clip); video.poster = clip.poster;
  tracks();
  render();
  history.replaceState(null, "", "#" + clip.id);
  if (play) video.play().catch(() => {});
}
// A narrated clip has one file per language: the voice follows the language switch (a string = one file for both).
function videoOf(clip) { return typeof clip.video === "string" ? clip.video : (clip.video[lang] || Object.values(clip.video)[0]); }
function render() {
  const t = TEXT[lang], clip = TOUR.clips[current];
  document.documentElement.lang = lang;
  $("t-title").textContent = t.title; $("t-lead").textContent = t.lead; $("t-note").textContent = t.note;
  $("now-title").textContent = clip.title[lang];
  const steps = clip.steps || [];
  $("steps").hidden = !steps.length;
  $("steps-label").textContent = t.steps;
  $("step-list").innerHTML = steps.map((st, i) => `<li><button data-t="${st.t}" data-i="${i}"><span>${mmss(st.t)}</span><code>${esc(st.cmd)}</code></button></li>`).join("");
  currentStep(true);
  const guides = clip.guides || {};
  $("t-follow").hidden = !Object.keys(guides).length;
  $("t-follow").innerHTML = Object.keys(guides).length ? `${esc(t.follow)} ${Object.entries(guides).map(([code, url]) => `<a href="${esc(url)}"${code === lang ? ' class="now-lang"' : ""}>${code.toUpperCase()}</a>`).join(" · ")}` : "";
  const series = (c) => c.series || "tour", same = TOUR.clips.filter((c) => series(c) === series(clip));
  $("now-meta").textContent = `${t.series[series(clip)] || series(clip)} · ${t.clip} ${same.indexOf(clip) + 1} ${t.of} ${same.length} · ${mmss(clip.duration)}`;
  document.querySelectorAll(".lang button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === lang)));
  let last = null;
  $("list").innerHTML = TOUR.clips.map((c, i) => {
    const head = series(c) !== last ? `<li class="series"><h3>${esc(t.series[series(c)] || series(c))}</h3></li>` : "";
    last = series(c);
    return head + `<li><button data-i="${i}" aria-current="${i === current}"><img src="${esc(c.poster)}" alt="" loading="lazy"><span><b>${esc(c.title[lang])}</b><small>${mmss(c.duration)}</small></span></button></li>`;
  }).join("");
}
$("list").onclick = (e) => { const b = e.target.closest("button[data-i]"); if (b) load(Number(b.dataset.i), true); };
// The command typed at (or just before) the playhead: shown with a Copy button, marked in the list.
let shownStep = -1;
function currentStep(force) {
  const steps = TOUR.clips[current].steps || [];
  let i = -1;
  for (let k = 0; k < steps.length; k++) if (steps[k].t <= video.currentTime + 0.3) i = k;
  if (i === shownStep && !force) return;
  shownStep = i;
  const t = TEXT[lang];
  $("step-now").textContent = i >= 0 ? steps[i].cmd : t.stepsNone;
  $("step-copy").disabled = i < 0;
  $("step-list").querySelectorAll("button").forEach((b) => b.setAttribute("aria-current", String(Number(b.dataset.i) === i)));
  const on = $("step-list").querySelector('button[aria-current="true"]');
  if (on && !force) on.scrollIntoView({ block: "nearest" });
}
video.addEventListener("timeupdate", () => currentStep(false));
$("step-list").onclick = (e) => { const b = e.target.closest("button[data-t]"); if (b) { video.currentTime = Number(b.dataset.t); video.play().catch(() => {}); } };
$("step-copy").onclick = async () => {
  const text = $("step-now").textContent;
  try { await navigator.clipboard.writeText(text); } catch { const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
  const b = $("step-copy"), was = b.textContent; b.textContent = TEXT[lang].copied; setTimeout(() => (b.textContent = was), 1200);
};
document.querySelectorAll(".lang button").forEach((b) => (b.onclick = () => {
  lang = b.dataset.lang;
  try { localStorage.setItem("qil-tour-lang", lang); } catch {}
  const clip = TOUR.clips[current], next = videoOf(clip);
  if (!video.currentSrc.endsWith(next)) {
    const at = video.currentTime, playing = !video.paused;
    video.src = next; tracks();
    video.addEventListener("loadedmetadata", () => { video.currentTime = at; if (playing) video.play().catch(() => {}); }, { once: true });
  } else showLang();
  render();
}));
// Chromium's automatic track selection runs once the metadata arrives and can turn a second
// track on: the choice is applied again then.
video.addEventListener("loadedmetadata", showLang);
// A clip plays on into the next one of its own series; a lesson ends on its own.
video.onended = () => { const n = current + 1; if (n < TOUR.clips.length && (TOUR.clips[n].series || "tour") === (TOUR.clips[current].series || "tour")) load(n, true); };
const start = TOUR.clips.findIndex((c) => "#" + c.id === location.hash);
load(start < 0 ? 0 : start, false);
</script>
</body>
</html>
"""


GUIDE_PAGE = r"""<!doctype html>
<html lang="__LANG__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__GROUP__ · guide</title>
<link rel="icon" type="image/svg+xml" href="https://manzolo.github.io/qemu-iso-lab/qemu-iso-lab.svg">
<meta name="description" content="__DESCRIPTION__">
<style>
:root { color-scheme:dark; --bg:#0b1017; --panel:#131c27; --panel2:#192432; --line:#293646; --text:#edf3fa; --muted:#9aabbe; --accent:#8aead0; --btn:#202d3d; }
* { box-sizing:border-box; }
body { margin:0; background:radial-gradient(ellipse 70% 650px at 80% 0%,#19333765,transparent),var(--bg); color:var(--text); font:15px/1.65 system-ui,-apple-system,"Segoe UI",sans-serif; }
a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
.site-header,main { max-width:900px; margin:auto; padding:0 36px; }
.brand { display:flex; align-items:center; gap:12px; min-height:64px; border-bottom:1px solid var(--line); }
.logo { width:36px; height:36px; border:1px solid #8aead050; border-radius:10px; display:grid; place-items:center; color:var(--accent); background:#8aead012; font:700 18px ui-monospace,monospace; }
.brand-name { color:var(--text); font-size:17px; font-weight:700; letter-spacing:-.5px; } .brand-name b { color:var(--accent); }
.brand-label { margin-left:4px; padding-left:16px; border-left:1px solid var(--line); color:var(--muted); font-size:12px; }
.brand nav { margin-left:auto; display:flex; align-items:center; gap:20px; font-size:12px; } .brand nav a { color:var(--muted); }
.head { padding:26px 0 8px; display:flex; flex-wrap:wrap; gap:10px 18px; align-items:center; font-size:12px; color:var(--muted); }
.head .group { font:600 12px ui-monospace,monospace; color:var(--text); background:var(--panel2); padding:3px 9px; border-radius:6px; }
.head .lesson { color:var(--accent); border:1px solid #8aead040; border-radius:7px; padding:4px 10px; background:#8aead010; font-weight:650; }
.head b { color:var(--text); }
article { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:10px 34px 30px; margin:12px 0 60px; }
article h1 { font-size:28px; letter-spacing:-.6px; line-height:1.2; margin:22px 0 10px; }
article h2 { font-size:20px; letter-spacing:-.3px; margin:34px 0 8px; padding-top:18px; border-top:1px solid var(--line); }
article h3 { font-size:15px; margin:22px 0 6px; }
article p, article li { color:#d8e1ea; }
article code { font-family:ui-monospace,"Cascadia Code",monospace; font-size:.92em; background:#0b111a; border:1px solid var(--line); border-radius:5px; padding:1px 5px; }
article pre { background:#0b111a; border:1px solid var(--line); border-radius:9px; padding:14px 16px; overflow:auto; white-space:pre-wrap; font-size:12.5px; line-height:1.55; }
article pre code { background:none; border:0; padding:0; font-size:inherit; }
article table { border-collapse:collapse; width:100%; font-size:13px; margin:12px 0; } article th, article td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); vertical-align:top; } article th { color:var(--muted); font-weight:600; }
article blockquote { margin:12px 0; padding:8px 14px; border-left:3px solid var(--accent); color:var(--muted); }
article hr { border:0; border-top:1px solid var(--line); margin:24px 0; }
@media (max-width:560px) { .site-header,main { padding:0 16px; } article { padding:6px 18px 22px; } .brand-label { display:none; } }
</style>
</head>
<body>
<header class="site-header">
  <div class="brand"><a class="logo" href="__REPO__" aria-label="QEMU ISO Lab repository">&gt;_</a><a class="brand-name" href="../../">QEMU <b>ISO Lab</b></a><span class="brand-label">LAB GUIDE</span><nav aria-label="Main navigation"><a href="../../#labs">Labs</a><a href="../../tour.html">Tour</a><a href="__REPO__">GitHub ↗</a></nav></div>
</header>
<main>
  <div class="head"><span class="group">__GROUP__</span><span>__LANGS__</span>__LESSON__<a href="../../#lab-__GROUP__">Lab in the catalog</a><a href="__SOURCE__" target="_blank" rel="noopener">Markdown source ↗</a></div>
  <article class="guide">__BODY__</article>
</main>
</body>
</html>
"""


def write_guides(root: Path, out: Path, labs: list[dict[str, Any]]) -> None:
    """Every lab guide as a page of the site, ``labs/<lab>/guide.<lang>.html``, rendered from the
    tracked markdown by the dashboard's own renderer (labs.render_markdown): the reader follows the
    steps on their own machine, next to the lesson when the tour has one. The lab's ``guides`` then
    point at these pages; ``source`` keeps the GitHub folder."""
    import html as html_mod
    from vmctl import labs as labs_mod
    for lab in labs:
        if not lab["guides"]:
            continue
        group = lab["group"]
        target = out / "labs" / group
        target.mkdir(parents=True, exist_ok=True)
        pages = {lang: f"labs/{group}/guide.{lang}.html" for lang in lab["guides"]}
        for lang in lab["guides"]:
            source = root / "vms" / "labs" / group / f"guide.{lang}.md"
            body = labs_mod.render_markdown(source.read_text(encoding="utf-8"))
            langs = " · ".join(f"<b>{code.upper()}</b>" if code == lang else f'<a href="guide.{code}.html">{code.upper()}</a>' for code in pages)
            clips = lab.get("clips") or ([{"href": lab["clip"], "title": {}}] if lab.get("clip") else [])
            lesson = "".join(f'<a class="lesson" href="../../{html_mod.escape(c["href"])}">▶ '
                             f'{html_mod.escape((c.get("title") or {}).get(lang) or (c.get("title") or {}).get("en") or "Watch the lesson") if len(clips) > 1 else "Watch the lesson"}</a>'
                             for c in clips)
            page = (GUIDE_PAGE.replace("__LANG__", lang).replace("__GROUP__", html_mod.escape(group)).replace("__LANGS__", langs)
                    .replace("__LESSON__", lesson).replace("__SOURCE__", f"{REPO_URL}/blob/main/vms/labs/{group}/guide.{lang}.md")
                    .replace("__DESCRIPTION__", html_mod.escape(lab["title"])).replace("__BODY__", body).replace("__REPO__", REPO_URL))
            (target / f"guide.{lang}.html").write_text(page, encoding="utf-8")
        lab["source"] = f"{REPO_URL}/tree/main/vms/labs/{group}"
        lab["guides"] = pages


def collect_tour(media: Path, out: Path) -> list[dict[str, Any]]:
    """The tour (``<media>/tour/tour.json`` + its clips, posters and WebVTT subtitles, recorded by
    hand in a lab VM): copied to ``<out>/tour/``; an empty list when there is none."""
    index = media / "tour" / "tour.json"
    if not index.is_file():
        return []
    clips = json.loads(index.read_text(encoding="utf-8"))["clips"]
    target = out / "tour"
    target.mkdir(parents=True, exist_ok=True)
    for clip in clips:
        # One video, or one per language when the clips are narrated (a browser cannot be trusted
        # to switch the audio track of an MP4).
        videos = clip["video"] if isinstance(clip["video"], dict) else {"": clip["video"]}
        for name in (*videos.values(), clip["poster"], *clip["subtitles"].values()):
            shutil.copy2(media / "tour" / name, target / name)
        clip["video"] = {lang: f"tour/{name}" for lang, name in videos.items()} if isinstance(clip["video"], dict) else f"tour/{clip['video']}"
        clip["poster"] = f"tour/{clip['poster']}"
        clip["subtitles"] = {lang: f"tour/{name}" for lang, name in clip["subtitles"].items()}
    return clips


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


def link_status(links: Path | None) -> tuple[dict[str, dict[str, str]], str | None]:
    """Per profile, what tools/check_iso_urls.py --json found: verdict (OK, DEGRADED, BROKEN), the
    first bad source's reason. The Pages workflow runs the check right before the build."""
    if links is None or not links.is_file():
        return {}, None
    try:
        report = json.loads(links.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, None
    status: dict[str, dict[str, str]] = {}
    for row in report.get("profiles", []):
        bad = next((source for source in row.get("sources", []) if not source.get("good")), None)
        status[row["vm"]] = {"verdict": row["verdict"], "why": f"{bad['url']}: {bad['reason']}" if bad and row["verdict"] != "OK" else ""}
    return status, _dt.date.fromtimestamp(links.stat().st_mtime).isoformat()


def build(root: Path, out: Path, media: Path | None = None, clip_style: str = "combo", links: Path | None = None) -> dict[str, Any]:
    data = catalog_data(root)
    out.mkdir(parents=True, exist_ok=True)
    clips = collect_media(root, out, media, [p["name"] for p in data["profiles"]])
    status, checked = link_status(links)
    data["links_checked"] = checked
    for profile in data["profiles"]:
        profile["clip"] = clips.get(profile["name"])
        profile["link"] = status.get(profile["name"])
    data["clip_style"] = clip_style
    web = root / "vmctl" / "web"
    shutil.copy2(web / "icons.js", out / "icons.js")
    shutil.copy2(web / "qemu-iso-lab.svg", out / "qemu-iso-lab.svg")  # the favicon of every page
    # Stable, short installer URLs published alongside the catalog.
    shutil.copy2(root / "install.sh", out / "install.sh")
    shutil.copy2(root / "setup-windows.ps1", out / "install.ps1")
    # cmd.exe expects Windows line endings even though the source is maintained on Linux.
    (out / "install-windows.cmd").write_bytes((root / "install-windows.cmd").read_text().replace("\n", "\r\n").encode("utf-8"))
    # The sprite is inlined (hidden) so <use href="#arch"> works from file:// too, not only over HTTP.
    sprite = (web / "distro-icons.svg").read_text(encoding="utf-8").replace('<svg xmlns="http://www.w3.org/2000/svg">', '<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">', 1)
    tour = collect_tour(media if media is not None else root / "docs" / "media", out)
    # A lab may have several lessons (git-lab: one for beginners, one from underneath): `clips`
    # lists them in the tour's order, `clip` stays the first for the pages that want one link.
    lessons: dict[str, list[dict[str, Any]]] = {}
    for clip in tour:
        if clip.get("lab"):
            lessons.setdefault(clip["lab"], []).append({"href": f"tour.html#{clip['id']}", "title": clip["title"]})
    for lab in data["labs"]:
        lab["clips"] = lessons.get(lab["group"], [])
        lab["clip"] = lab["clips"][0]["href"] if lab["clips"] else None
    write_guides(root, out, data["labs"])
    guides = {lab["group"]: lab["guides"] for lab in data["labs"]}
    for clip in tour:
        clip["guides"] = guides.get(clip.get("lab") or "", {})
    if tour:
        (out / "tour.html").write_text(TOUR_PAGE.replace("__REPO__", REPO_URL).replace(
            "__DATA__", json.dumps({"clips": tour}, ensure_ascii=False).replace("</", "<\\/")), encoding="utf-8")
    (out / "catalog.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tour_nav = '<a class="tour" href="tour.html">▶ Tour</a>' if tour else ""
    tour_start = ('<div class="terminal-note"><span>New to it? Short clips show the whole road, '
                  'spoken and subtitled in English and Italian.</span><a href="tour.html">▶ Take the tour</a></div>') if tour else ""
    page = (PAGE.replace("__TOUR_NAV__", tour_nav).replace("__TOUR_START__", tour_start).replace("__SPRITE__", sprite).replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__VMCTL__", data["vmctl_version"]).replace("__GENERATED__", data["generated"])
            .replace("__COMMIT__", f" · {data['commit']}" if data["commit"] else "").replace("__REPO__", REPO_URL)
            .replace("__LINUX_INSTALL__", html.escape(LINUX_INSTALL, quote=True))
            .replace("__WINDOWS_INSTALL__", html.escape(WINDOWS_INSTALL, quote=True)))
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "site", help="output directory (default: site/)")
    parser.add_argument("--media", type=Path, default=None, help="directory with <vm>/recording.mp4|recording.gif|poster.png (default: docs/media)")
    parser.add_argument("--clip-style", choices=["combo", "gif", "video", "both"], default="combo", help="how a profile's install clip is shown on its card: combo = the GIF loops in the card and a small button opens the video player (default), gif, video (poster + play), both")
    parser.add_argument("--links", type=Path, default=None, metavar="FILE", help="tools/check_iso_urls.py --json output: a badge per card says whether the ISO link works")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    data = build(args.root, args.out, media=args.media, clip_style=args.clip_style, links=args.links)
    print(f"{args.out}: {data['counts']['profiles']} profiles, {data['counts']['unattended']} unattended, {data['counts']['verified']} verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
