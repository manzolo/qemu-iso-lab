"""Read shared VM facts and reuse guarded shell workflows with Textual widgets."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Any

from vmctl.config import natural_key

Facts = dict[str, Any]


def visible_rows(rows: list[Facts], query: str, mode: str) -> list[Facts]:
    words = query.casefold().split()
    selected = [row for row in rows
                if all(word in f"{row['name']} {row['label']} {row['family']}".casefold()
                       for word in words)
                and (mode != "disk" or row["prepared"])
                and (mode != "running" or row["running"])]
    return sorted(selected, key=lambda row: (
        not row["running"], not row["installed"], not row["prepared"],
        natural_key(row["name"]),
    ))


def status_label(row: Facts) -> tuple[str, str]:
    job = row["job_status"]
    if job and job != "completed":
        return ("Installing", "#e8be78") if job == "running" else (job.capitalize(), "#f08a8a")
    if row["running"]:
        return "Running", "#86d5ab"
    labels = {
        "verified": ("Boot verified", "#86d5ab"),
        "installed": ("Installed", "#84c9e7"),
        "unverified": ("Unverified", "#e8be78"),
        "incomplete": ("Incomplete", "#f08a8a"),
        "empty": ("Empty disk", "#a6b4c8"),
        "no disk": ("No disk", "#a6b4c8"),
    }
    return labels.get(row["install_label"], ("Unknown", "#a6b4c8"))


def primary_action(row: Facts) -> tuple[str, str]:
    if row["job_status"] == "running":
        return "Installation log", "alt-l"
    if row["running"]:
        return "Open display", "alt-a"
    if row["installed"]:
        return "Boot desktop", "alt-d"
    unattended = any(value for key, value in row.items() if key in {
        "has_autoinstall", "has_archinstall", "has_omarchy", "has_preseed",
        "has_kickstart", "has_autoyast", "has_alpine", "has_windows",
        "has_freebsd", "has_proxmox", "has_pfsense", "has_reactos",
    })
    return ("Unattended install…" if unattended else "Boot ISO…"), "alt-u"


def quick_actions(row: Facts) -> list[tuple[str, str]]:
    actions = [primary_action(row)]
    if row["job_status"] == "running":
        actions.append(("Open display", "alt-a"))
    else:
        if row["running"]:
            if row.get("has_ssh"):
                actions.append(("SSH console", "alt-s"))
            actions.append(("Stop VM", "alt-x"))
        elif row["installed"]:
            actions.append(("Boot headless", "alt-h"))
        if not row["running"]:
            actions.append(("Video profile…", "Video Profile"))
    actions.append(("All actions…", "menu"))
    return actions


def resources(row: Facts) -> str:
    memory = row["memory_mb"]
    ram = f"{memory / 1024:g}G" if memory >= 1024 else f"{memory}M"
    return f"{ram} / {row['cpus']}"


class ClassicBridge:
    def __init__(self) -> None:
        self.script = Path(__file__).resolve().parent.parent / "bin" / "vmtui"
        self.env = {**os.environ, "VMTUI_TEST_MODE": "1"}

    def snapshot(self) -> list[Facts]:
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; dashboard_snapshot', "vmtui-textual", str(self.script)],
            env={**self.env, "VMTUI_UI": "textual"}, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Unable to read VM state")
        rows: list[Facts] = json.loads(result.stdout)
        return rows

    def run(self, name: str, action: str) -> int:
        if action not in {"menu", "classic", "alt-l", "alt-a", "alt-d", "alt-u",
                          "alt-h", "alt-s", "alt-x", "Profile Details", "Video Profile"}:
            raise ValueError(f"Unsupported dashboard action: {action}")
        # Values are positional arguments, never interpolated into shell code.
        script = '''source "$1"
install_interrupt_guard
case "$3" in
    classic) main_menu_loop ;;
    menu) current_vm="$2"; state_set last-vm "$current_vm"; vm_menu_loop ;;
    "Profile Details"|"Video Profile")
        current_vm="$2"; load_vm_facts "$current_vm"; run_vm_menu_action "$3" ;;
    *) run_dashboard_hotkey "$3" "$2" ;;
esac
'''
        previous = signal.signal(signal.SIGINT, lambda *_: None)
        try:
            return subprocess.run(
                ["bash", "-c", script, "vmtui-textual", str(self.script), name, action],
                env=({key: value for key, value in self.env.items()
                      if (key, value) != ("VMTUI_UI", "textual")} if action == "classic" else {
                    **self.env, "VMTUI_UI": "textual", "VMTUI_TEXTUAL_PYTHON": sys.executable,
                }), check=False,
            ).returncode
        finally:
            signal.signal(signal.SIGINT, previous)
