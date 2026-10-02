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
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vmctl import profile_versions, runtime, state
from vmctl.errors import VMError

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

def _versions(vm_name: str) -> dict[str, Any]:
    """What installed this disk: the catalog's version of the profile (None for a clone or a
    local-only VM) and vmctl's own, so the dashboards can say which recipe the disk carries."""
    import vmctl  # the package: __version__ (lazy, vmctl/__init__ imports the CLI)
    return {"profile_version": profile_versions.catalog_version(vm_name), "vmctl_version": vmctl.__version__}


# Protected VMs: nothing that would delete or overwrite their disk runs (clean, a new installation
# on a disk with data, a checkpoint restore; check-vms moves them aside and back). Two sources,
# both in local.json and read here, the lowest module every one of those paths goes through:
# the explicit flag of ``vmctl protect`` ("protected": {"vms": [...]}, written by
# catalog.update_protected) and My VMs ("catalog": {"selected": [...]}): a starred VM whose disk
# holds data is protected as long as the star is on it (decision of 2026-09-29: the machines one
# keeps are the ones one would not want a stray clean to take).
PROTECTED_KEY = "protected"
CATALOG_KEY = "catalog"


def _local_names(key: str, field: str) -> set[str]:
    path = state.CONFIG_DIR / "profiles" / "local.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (ValueError, OSError):
        return set()
    section = document.get(key) if isinstance(document, dict) else None
    names = section.get(field) if isinstance(section, dict) else None
    return {name for name in names if isinstance(name, str)} if isinstance(names, list) else set()


def flagged_names() -> set[str]:
    """The names ``vmctl protect`` flagged, whatever their disk holds."""
    return _local_names(PROTECTED_KEY, "vms")


def starred_names() -> set[str]:
    """My VMs, as local.json spells them (catalog.selected resolves aliases; the writers store canonical names)."""
    return _local_names(CATALOG_KEY, "selected")


def protection_reason(vm_name: str) -> str | None:
    """Why *vm_name* is protected: ``flag`` (vmctl protect), ``star`` (in My VMs with a disk that
    holds data) or None. The flag wins in the wording because only it needs ``vmctl unprotect``."""
    if vm_name in flagged_names():
        return "flag"
    if vm_name in starred_names() and artifact_disk_has_data(vm_name):
        return "star"
    return None


def protected_names() -> set[str]:
    """Every protected VM: the flagged ones plus the starred ones whose disk holds data."""
    return flagged_names() | {name for name in starred_names() if artifact_disk_has_data(name)}


def is_protected(vm_name: str) -> bool:
    return protection_reason(vm_name) is not None


def refuse_if_protected(vm_name: str, action: str) -> None:
    reason = protection_reason(vm_name)
    if reason == "flag":
        raise VMError(f"'{vm_name}' is protected: refusing to {action}. "
                      f"vmctl unprotect {vm_name} first if that is really what you want.")
    if reason == "star":
        raise VMError(f"'{vm_name}' is in My VMs and its disk holds data, so it is protected: refusing to {action}. "
                      f"vmctl catalog remove {vm_name} first if that is really what you want.")


def qcow2_has_backing_file(path: Path) -> bool:
    """A qcow2 header whose backing_file_offset is set: the image is an overlay (a cloud image
    VM, vmctl/cloudimg.py). Read from the first 16 bytes, no qemu-img: the dashboards ask this
    for every disk on every refresh."""
    try:
        with path.open("rb") as handle:
            header = handle.read(16)
    except OSError:
        return False
    return len(header) == 16 and header[:4] == b"QFI\xfb" and int.from_bytes(header[8:16], "big") != 0


def artifact_disk_has_data(vm_name: str) -> bool:
    """Whether artifacts/<vm>/ holds a disk image with data (any format, allocated blocks; an
    overlay counts whatever it has written, its base is the data)."""
    base = runtime.vm_artifact_base(vm_name)
    for path in base.glob("disk.*") if base.is_dir() else ():
        try:
            if path.is_file() and (path.stat().st_blocks * 512 > DATA_MIN_BYTES or qcow2_has_backing_file(path)):
                return True
        except OSError:
            continue
    return False


def begin_install(vm_name: str, flow: str, interactive: bool = False, dry_run: bool = False,
                  guest_user: str | None = None) -> None:
    """A new installation starts on this disk: whatever the record said no longer holds.

    The record also keeps the guest user this install creates (``guest``): the identity of
    local.json can move later, and SSH must still log in as the user that exists on the disk."""
    if not dry_run and artifact_disk_has_data(vm_name):
        refuse_if_protected(vm_name, "install over its disk")
    user = guest_user if guest_user is not None else declared_user(vm_name)
    record = {
        "origin": {"kind": "install", "flow": flow, "at": now()},
        "install": {"flow": flow, "started_at": now(), "completed_at": None,
                    "mode": FLOW_INTERACTIVE if interactive else "unattended", **_versions(vm_name)},
        "verify": None,
        "guest": {"user": user, "recorded_by": "install", "at": now()} if user else None,
    }
    save(vm_name, record, dry_run=dry_run)


# --- the guest user on the disk -----------------------------------------------------------
# Tracked profiles install `lab`; a local.json identity moves every profile to another user, also
# the ones whose disk was installed before (debian-12 on 2026-10-02: installed as lab on 09-29,
# the profile said manzolo, SSH, Files and link-settle knocked as a user the guest never had).
# The record says who is on the disk; ssh_target logs in as that user when it differs.

USER_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def declared_user(vm_name: str) -> str | None:
    """The guest user the catalog declares for *vm_name* right now (local.json identity applied),
    None when the catalog cannot be read here (a bare test root). config sits beside vmstate in
    the import order, hence the lazy import."""
    try:
        from vmctl import config  # noqa: PLC0415 - same level of the import order, no module-level cycle
        return config.resolve_vm_user(config.get_vm(config.load_config(), vm_name))[0]
    except Exception:  # noqa: BLE001 - a record must never fail over the catalog's state
        return None


def installed_user(vm_name: str) -> str | None:
    """The guest user recorded for the data on this disk: None without a record or a disk."""
    if not artifact_disk_has_data(vm_name):
        return None
    guest = load(vm_name).get("guest")
    user = guest.get("user") if isinstance(guest, dict) else None
    return str(user).strip() or None if isinstance(user, str) else None


def vm_name_for(vm: dict[str, Any]) -> str | None:
    """The profile key of a resolved profile, read from its disk path: ``artifacts/<name>/disk.*``
    is the only layout the record is kept in (a disk elsewhere has no record here)."""
    try:
        base = runtime.resolve_path(vm["disk"]["path"]).parent
    except (KeyError, TypeError, AttributeError):
        return None
    if base.parent.resolve() != (state.ROOT / "artifacts").resolve():
        return None
    return base.name


def installed_user_for(vm: dict[str, Any]) -> str | None:
    """`installed_user` from a resolved profile (what ssh_target needs)."""
    name = vm_name_for(vm)
    return installed_user(name) if name else None


def set_installed_user(vm_name: str, user: str, dry_run: bool = False) -> None:
    """``vmctl guest-user <vm> <user>``: record the user of a disk installed before vmctl kept it
    (or installed outside vmctl). The install ladder is untouched."""
    if not USER_NAME_RE.match(user):
        raise VMError(f"{user!r} is not a POSIX login name (lower-case letters, digits, '_' and '-')")
    if not artifact_disk_has_data(vm_name):
        raise VMError(f"{vm_name}: no disk with data under {runtime.vm_artifact_base(vm_name)}; install it first")
    record = load(vm_name)
    record["guest"] = {"user": user, "recorded_by": "vmctl guest-user", "at": now()}
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
        install = {"flow": flow, "started_at": None, "mode": "unattended", **_versions(vm_name)}
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
    facts["has_data"] = facts["host_bytes"] >= DATA_MIN_BYTES or qcow2_has_backing_file(path)
    if fmt == "raw":
        facts["virtual_bytes"] = int(st.st_size)
    elif shutil.which("qemu-img") is not None:
        try:
            facts["virtual_bytes"] = int(runtime.image_info(path, quiet=True, force_share=True).get("virtual-size", 0) or 0)
        except Exception:
            facts["virtual_bytes"] = None
    return facts


def summary(vm_name: str, vm: dict[str, Any], profile_user: str | None = None) -> dict[str, Any]:
    """Everything the CLI and the TUI show about the disk, decided in one place.

    ``label`` is the compact ladder: ``no disk`` < ``empty`` < ``unverified`` (data, no
    record, or an installer booted by hand) < ``incomplete`` (an unattended install that
    started and never sent its token) < ``installed`` (token arrived) < ``verified``
    (booted and checked). ``detail`` spells the same out in one sentence.
    """
    return describe(load(vm_name), disk_facts(vm), catalog_version=(vm.get("meta") or {}).get("version"),
                    profile_user=profile_user)


def describe(record: dict[str, Any], facts: dict[str, Any], catalog_version: str | None = None,
             profile_user: str | None = None) -> dict[str, Any]:
    """`summary` for a record and image facts that do not belong to a live VM (a checkpoint)."""
    install = record.get("install") if isinstance(record.get("install"), dict) else None
    verify = record.get("verify") if isinstance(record.get("verify"), dict) else None
    origin = record.get("origin") if isinstance(record.get("origin"), dict) else None
    guest = record.get("guest") if isinstance(record.get("guest"), dict) else None

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
        "profile_version": None, "vmctl_version": None, "catalog_version": catalog_version,
        # The user on the disk (recorded at install or by vmctl guest-user) and the profile's today.
        "guest_user": (str(guest.get("user") or "").strip() or None) if guest and facts["has_data"] else None,
        "profile_user": profile_user,
    }

    if install:
        out["install_flow"] = install.get("flow")
        out["install_interactive"] = install.get("mode") == FLOW_INTERACTIVE
        out["profile_version"] = install.get("profile_version")
        out["vmctl_version"] = install.get("vmctl_version")
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
    elif out["origin_kind"] in ("import", "restore", "clone", "image"):
        out["label"] = LABEL_UNVERIFIED
        out["detail"] = f"disk from {out['origin_kind']} of {out['origin_source']} on {_day(out['origin_at'])}, not verified here"
    else:
        out["label"] = LABEL_UNVERIFIED
        out["detail"] = "disk has data but no installation record (pre-existing disk); run a boot-check or post-install to verify it"
    if facts["has_data"] and out["profile_version"] and out["install_state"]:
        # The recipe on the disk vs the catalog's: a newer catalog is a reason to reinstall, not a fault.
        out["detail"] += f" (profile {out['profile_version']}"
        if catalog_version and catalog_version != out["profile_version"]:
            out["detail"] += f", the catalog is at {catalog_version} now"
        out["detail"] += ")"
    if out["guest_user"] and profile_user and profile_user != out["guest_user"]:
        out["detail"] += f"; installed as {out['guest_user']}, the profile names {profile_user} now (SSH logs in as {out['guest_user']})"
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
