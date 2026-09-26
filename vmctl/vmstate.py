"""What the host knows about the disk it holds for a VM: ``artifacts/<vm>/state.json``.

Three facts are kept apart on purpose, because they have three different sources:

* **data on the disk** is measured, never recorded: a disk image with more than
  ``DATA_MIN_BYTES`` allocated on the host has something on it (a fresh qcow2 allocates
  about 200 KiB). It says nothing about *what* is on it.
* **installation completed** is recorded by the unattended flows when the guest's own
  completion token arrived on the serial console (``complete_install``). An installer
  booted by hand only records that it was started: nobody watches it finish.
* **boot verified** is recorded when the *installed disk* did what a check asked of it:
  the SSH post-install ran through (``post-install``) or a boot-check of the disk passed
  (``boot-check``). A ``check-vms`` row records nothing of its own: its flows already do.

Nothing here is derived from the profile. ``meta.verified`` is the maintainer's last live
PASS of the *recipe*, and does not certify the disk sitting in ``artifacts/`` today.

Invalidation is explicit, and every operation that touches the disk owns its share:

* ``begin_install`` (every bootstrap and hand-driven installer) resets the record: a new
  installation is starting on this disk, whatever it held before.
* ``vmctl clean`` deletes the file with the disk.
* ``import-device`` records the physical source as the origin and clears the two
  recorded facts: the imported system was installed elsewhere.
* a checkpoint restore brings back the record saved with the checkpoint; a clone carries
  the origin's record and notes where it came from.
* ``check-vms --restore`` moves the whole artifact directory aside and back, so the
  record travels with the disk it describes.

One cheap sanity rule closes the gap a by-hand ``rm disk.qcow2`` + ``vmctl prep`` would
leave: a record that says "completed" over a disk without data is stale and reads as an
empty disk.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vmctl import runtime

STATE_FILE = "state.json"
STATE_VERSION = 1

# Below this many bytes allocated on the host the image holds no data: a fresh qcow2 has
# about 200 KiB of metadata, an installed system has hundreds of megabytes.
DATA_MIN_BYTES = 16 * 1024 * 1024

# How the recorded install started.
FLOW_INTERACTIVE = "interactive"

# The compact label of a disk, from the least to the most that is known about it.
LABEL_NO_DISK = "no disk"
LABEL_EMPTY = "empty"
LABEL_UNVERIFIED = "unverified"
LABEL_INCOMPLETE = "incomplete"
LABEL_INSTALLED = "installed"
LABEL_VERIFIED = "verified"


def state_path(vm_name: str) -> Path:
    return runtime.vm_artifact_base(vm_name) / STATE_FILE


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load(vm_name: str) -> dict[str, Any]:
    """The record, or ``{}`` when there is none or it is unreadable (never an error:
    this is read while drawing a menu)."""
    path = state_path(vm_name)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def save(vm_name: str, payload: dict[str, Any], dry_run: bool = False) -> None:
    """Write the record atomically (a parallel check-vms writes other VMs' files, never
    this one, but a crash mid-write must not leave half a JSON behind)."""
    if dry_run:
        return
    path = state_path(vm_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["version"] = STATE_VERSION
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def forget(vm_name: str, dry_run: bool = False) -> None:
    """Drop the record: the disk it described is gone."""
    if dry_run:
        return
    for path in (state_path(vm_name), state_path(vm_name).with_name(STATE_FILE + ".tmp")):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def copy_record(src_vm: str, dst_vm: str, dry_run: bool = False) -> None:
    """Carry one VM's record to another directory (checkpoint, clone): the disk goes with it."""
    if dry_run:
        return
    src = state_path(src_vm)
    if not src.is_file():
        forget(dst_vm)
        return
    dst = state_path(dst_vm)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


# --- writers ---------------------------------------------------------------------------

def begin_install(vm_name: str, flow: str, interactive: bool = False, dry_run: bool = False) -> None:
    """A new installation starts on this disk: whatever the record said no longer holds."""
    record = {
        "origin": {"kind": "install", "flow": flow, "at": now()},
        "install": {"flow": flow, "started_at": now(), "completed_at": None,
                    "mode": FLOW_INTERACTIVE if interactive else "unattended"},
        "verify": None,
    }
    save(vm_name, record, dry_run=dry_run)


def complete_install(vm_name: str, flow: str, vm: dict[str, Any] | None = None, dry_run: bool = False) -> None:
    """The guest's completion token arrived: the installer finished on this disk."""
    if dry_run:
        return
    record = load(vm_name)
    install = record.get("install") if isinstance(record.get("install"), dict) else None
    if install is None or install.get("flow") != flow:
        # A completion without its start (a flow that did not call begin_install): record
        # what is known rather than dropping the fact.
        install = {"flow": flow, "started_at": None, "mode": "unattended"}
    install["completed_at"] = now()
    if vm is not None:
        facts = disk_facts(vm)
        install["disk_host_bytes"] = facts["host_bytes"]
    record["install"] = install
    record.setdefault("origin", {"kind": "install", "flow": flow, "at": install["completed_at"]})
    record["verify"] = None
    save(vm_name, record)


def record_verified(vm_name: str, kind: str, detail: str = "", dry_run: bool = False) -> None:
    """The installed disk booted and answered a check (``post-install``, ``boot-check``,
    ``check-vms``). Only the newest verification is kept: it is the one that describes
    the disk as it is now."""
    if dry_run:
        return
    record = load(vm_name)
    record["verify"] = {"kind": kind, "at": now(), "detail": detail or None}
    save(vm_name, record)


def record_origin(vm_name: str, kind: str, source: str, keep_facts: bool = False, dry_run: bool = False) -> None:
    """Where this disk came from when it was not installed here: ``import`` (a physical
    device), ``restore`` (a checkpoint), ``clone`` (another VM). Unless ``keep_facts``
    says the source's own record was carried over, the two recorded facts are cleared:
    they were about a different disk."""
    if dry_run:
        return
    record = load(vm_name)
    record["origin"] = {"kind": kind, "source": source, "at": now()}
    if not keep_facts:
        record["install"] = None
        record["verify"] = None
    save(vm_name, record)


# --- readers ---------------------------------------------------------------------------

def disk_facts(vm: dict[str, Any]) -> dict[str, Any]:
    """The image as the host sees it: present, bytes it occupies on the host, virtual
    capacity, and whether it holds data. The host bytes are the allocated blocks (what
    ``du`` shows), not the apparent size: a sparse raw image has the full capacity as
    ``st_size`` and almost nothing allocated. None of this is guest filesystem usage."""
    disk = vm.get("disk") or {}
    return image_facts(runtime.resolve_path(str(disk.get("path", ""))), str(disk.get("format", "")))


def image_facts(path: Path, fmt: str) -> dict[str, Any]:
    """`disk_facts` for any image file (the VM's own disk, a checkpoint's copy)."""
    facts: dict[str, Any] = {"path": str(path), "present": False, "host_bytes": 0, "virtual_bytes": None, "has_data": False}
    try:
        st = path.stat()
    except OSError:
        return facts
    if not path.is_file():
        return facts
    facts["present"] = True
    facts["host_bytes"] = int(st.st_blocks) * 512
    facts["has_data"] = facts["host_bytes"] >= DATA_MIN_BYTES
    if fmt == "raw":
        facts["virtual_bytes"] = int(st.st_size)
    elif shutil.which("qemu-img") is not None:
        try:
            facts["virtual_bytes"] = int(runtime.image_info(path, quiet=True, force_share=True).get("virtual-size", 0) or 0)
        except Exception:
            facts["virtual_bytes"] = None
    return facts


def summary(vm_name: str, vm: dict[str, Any]) -> dict[str, Any]:
    """Everything the CLI and the TUI show about the disk, decided in one place.

    ``label`` is the compact ladder: ``no disk`` < ``empty`` < ``unverified`` (data, no
    record, or an installer booted by hand) < ``incomplete`` (an unattended install that
    started and never sent its token) < ``installed`` (token arrived) < ``verified``
    (booted and checked). ``detail`` spells the same out in one sentence.
    """
    return describe(load(vm_name), disk_facts(vm))


def describe(record: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    """`summary` for a record and image facts that do not belong to a live VM (a checkpoint)."""
    install = record.get("install") if isinstance(record.get("install"), dict) else None
    verify = record.get("verify") if isinstance(record.get("verify"), dict) else None
    origin = record.get("origin") if isinstance(record.get("origin"), dict) else None

    out: dict[str, Any] = {
        "disk_present": facts["present"],
        "has_data": facts["has_data"],
        "host_bytes": facts["host_bytes"],
        "virtual_bytes": facts["virtual_bytes"],
        "host_size": runtime.format_bytes(facts["host_bytes"]) if facts["present"] else "-",
        "virtual_size": runtime.format_bytes(facts["virtual_bytes"]) if facts["virtual_bytes"] else ("?" if facts["present"] else "-"),
        "install_state": "none",
        "install_flow": None,
        "install_at": None,
        "install_interactive": False,
        "verified": False,
        "verify_kind": None,
        "verify_at": None,
        "origin_kind": origin.get("kind") if origin else None,
        "origin_source": origin.get("source") if origin else None,
        "origin_at": origin.get("at") if origin else None,
        "stale": False,
    }

    if install:
        out["install_flow"] = install.get("flow")
        out["install_interactive"] = install.get("mode") == FLOW_INTERACTIVE
        if install.get("completed_at"):
            out["install_state"] = "completed"
            out["install_at"] = install.get("completed_at")
        else:
            out["install_state"] = "started"
            out["install_at"] = install.get("started_at")
    if verify:
        out["verified"] = True
        out["verify_kind"] = verify.get("kind")
        out["verify_at"] = verify.get("at")

    if not facts["present"]:
        out["label"] = LABEL_NO_DISK
        out["detail"] = "no disk image"
    elif not facts["has_data"]:
        # A record over an empty image describes a disk that no longer exists.
        out["stale"] = bool(install or verify)
        out["label"] = LABEL_EMPTY
        out["detail"] = "empty disk" + (" (a previous record no longer applies)" if out["stale"] else "")
    elif out["verified"]:
        out["label"] = LABEL_VERIFIED
        out["detail"] = f"boot verified by {out['verify_kind']} on {_day(out['verify_at'])}"
        if out["install_state"] == "completed":
            out["detail"] += f", installed by {out['install_flow']} on {_day(out['install_at'])}"
    elif out["install_state"] == "completed":
        out["label"] = LABEL_INSTALLED
        out["detail"] = f"installed by {out['install_flow']} on {_day(out['install_at'])}, boot not verified yet"
    elif out["install_state"] == "started" and not out["install_interactive"]:
        out["label"] = LABEL_INCOMPLETE
        out["detail"] = f"{out['install_flow']} started on {_day(out['install_at'])} and never completed"
    elif out["install_state"] == "started":
        out["label"] = LABEL_UNVERIFIED
        out["detail"] = f"installer booted by hand on {_day(out['install_at'])}; completion is not tracked"
    elif out["origin_kind"] in ("import", "restore", "clone"):
        out["label"] = LABEL_UNVERIFIED
        out["detail"] = f"disk from {out['origin_kind']} of {out['origin_source']} on {_day(out['origin_at'])}, not verified here"
    else:
        out["label"] = LABEL_UNVERIFIED
        out["detail"] = "disk has data but no installation record (pre-existing disk); run a boot-check or post-install to verify it"
    return out


def _day(stamp: Any) -> str:
    text = str(stamp or "")
    return text[:10] if len(text) >= 10 else (text or "an unknown date")


def compact_size(size: int | None) -> str:
    """``8.3G`` / ``512M`` / ``1G``, never more than four characters before the unit: the
    dashboard column has 16 characters for the whole status and ``1003.5M`` was cut."""
    if size is None:
        return "?"
    value = float(size)
    for unit in ("B", "K", "M", "G", "T"):
        if value < 1000 or unit == "T":
            if unit == "B":
                return f"{int(value)}{unit}"
            text = f"{value:.0f}" if value >= 100 else f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{text}{unit}"
        value /= 1024
    return f"{size}B"
