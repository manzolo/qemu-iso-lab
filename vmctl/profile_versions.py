"""Profile versions: every tracked profile carries ``meta.version`` (semver) and ``vms/profiles.lock``
remembers, per profile, that version, a fingerprint of the recipe and the history of bumps.

The fingerprint is what an install depends on: the tracked entry without its labels and metadata
(``name``, ``meta``, help texts) plus the bytes of every ``vms/profile-files/`` file the entry
copies into the guest. A test compares the lock with the catalog, so a recipe that changed without
a bump fails ``make check`` and names the profile; ``tools/bump_profile.py`` bumps, records the
note and refreshes the lock. A ``meta.verified`` date or a new description never needs a bump.

``vmstate.begin_install`` records the lock's version (and vmctl's) in ``state.json``, so the
dashboards can say "installed with 1.0.0, the catalog is at 1.0.2".
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from vmctl import profile_bases, state
from vmctl.errors import VMError

LOCK_FILE = Path("vms") / "profiles.lock"
PROFILES_DIR = Path("vms") / "profiles"
PROFILE_FILES_DIR = Path("vms") / "profile-files"
LAB_FILES_DIR = Path("vms") / "labs"  # a lab's provision/ scripts, copied in by its members (labs.py)
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
PARTS = ("major", "minor", "patch")
# Keys that describe or document a profile without changing what gets installed.
FINGERPRINT_EXCLUDED = frozenset({"name", "meta", "iso_help", "notes", "description", "_comment", profile_bases.EXTENDS_KEY})
INITIAL_VERSION = "1.0.0"


def _root(root: Path | None) -> Path:
    return Path(root) if root is not None else Path(state.ROOT)


def is_semver(text: Any) -> bool:
    return isinstance(text, str) and bool(SEMVER_RE.match(text))


def tracked_profile_files(root: Path | None = None) -> list[Path]:
    """The catalog files, in load order; local.json (and its backups) are personal, never versioned."""
    return sorted(p for p in (_root(root) / PROFILES_DIR).glob("*.json") if not p.name.startswith("local"))


def tracked_documents(root: Path | None = None) -> tuple[dict[str, tuple[Path, dict[str, Any]]], dict[str, dict[str, Any]]]:
    """The raw ``vms`` (with the file each lives in) and the ``bases`` of the tracked catalog."""
    vms: dict[str, tuple[Path, dict[str, Any]]] = {}
    bases: dict[str, dict[str, Any]] = {}
    for path in tracked_profile_files(root):
        document = json.loads(path.read_text(encoding="utf-8"))
        for name, entry in (document.get("vms") or {}).items():
            vms[name] = (path, entry)
        bases.update(profile_bases.bases_of(document, str(path)))
    return vms, bases


def tracked_entries(root: Path | None = None) -> dict[str, tuple[Path, dict[str, Any]]]:
    """Every tracked profile, resolved over its base (``profile_bases``): the recipe an install
    depends on is the resolved one, so a base edit changes every child's fingerprint."""
    vms, bases = tracked_documents(root)
    resolved = profile_bases.resolve_extends({name: entry for name, (_, entry) in vms.items()}, bases)
    return {name: (vms[name][0], resolved[name]) for name in vms}


def children_of(base: str, root: Path | None = None) -> list[str]:
    """The tracked profiles built on *base*, directly or through another base, in catalog order."""
    vms, bases = tracked_documents(root)
    if base not in bases:
        raise VMError(f"{base} is not a base (bases: {', '.join(sorted(bases)) or 'none'})")
    raw = {name: entry for name, (_, entry) in vms.items()}
    return [name for name in vms if base in profile_bases.chain_of(name, raw, bases)]


def referenced_files(entry: dict[str, Any]) -> list[str]:
    """The ``vms/profile-files/`` and ``vms/labs/`` sources an entry copies into the guest
    (copy_from_host of the SSH and cloud-init provisioning), sorted; anything outside those
    directories is the user's."""
    sources: set[str] = set()
    for section in ("ssh_provision", "cloud_init"):
        for item in ((entry.get(section) or {}).get("copy_from_host") or []):
            source = str((item or {}).get("source") or "")
            if source.startswith((str(PROFILE_FILES_DIR) + "/", str(LAB_FILES_DIR) + "/")):
                sources.add(source.rstrip("/"))
    return sorted(sources)


def fingerprint(entry: dict[str, Any], root: Path | None = None) -> str:
    """sha256 over the recipe: the entry minus the excluded keys, then every referenced
    profile file (a directory contributes its files in sorted order; a missing file its name)."""
    recipe = {key: value for key, value in entry.items() if key not in FINGERPRINT_EXCLUDED}
    digest = hashlib.sha256(json.dumps(recipe, sort_keys=True, ensure_ascii=True).encode())
    base = _root(root)
    for source in referenced_files(entry):
        path = base / source
        files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
        for file in files:
            digest.update(f"\0{file.relative_to(base).as_posix()}\0".encode())
            digest.update(file.read_bytes() if file.is_file() else b"<missing>")
    return "sha256:" + digest.hexdigest()


def lock_path(root: Path | None = None) -> Path:
    return _root(root) / LOCK_FILE


def read_lock(root: Path | None = None) -> dict[str, Any]:
    path = lock_path(root)
    if not path.exists():
        return {"profiles": {}}
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise VMError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(lock, dict) or not isinstance(lock.get("profiles"), dict):
        raise VMError(f"{path} must be an object with a 'profiles' object")
    return lock


def write_lock(lock: dict[str, Any], root: Path | None = None) -> None:
    lock["profiles"] = dict(sorted(lock["profiles"].items()))
    lock_path(root).write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def catalog_version(name: str, root: Path | None = None) -> str | None:
    """The tracked version of a profile as the lock knows it; None for a clone or a local-only VM."""
    try:
        entry = read_lock(root)["profiles"].get(name)
    except VMError:
        return None
    return str(entry["version"]) if isinstance(entry, dict) and entry.get("version") else None


def check(root: Path | None = None) -> list[str]:
    """Every way the catalog and the lock can disagree, one line each (empty = consistent)."""
    problems: list[str] = []
    lock = read_lock(root)["profiles"]
    entries = tracked_entries(root)
    for name, (path, entry) in sorted(entries.items()):
        version = (entry.get("meta") or {}).get("version")
        if not is_semver(version):
            problems.append(f"{name} ({path.name}): meta.version missing or not MAJOR.MINOR.PATCH")
            continue
        locked = lock.get(name)
        if not locked:
            problems.append(f"{name}: not in {LOCK_FILE} (tools/bump_profile.py --init records it)")
            continue
        if locked.get("version") != version:
            problems.append(f"{name}: meta.version {version} but {LOCK_FILE} says {locked.get('version')}")
        if locked.get("fingerprint") != fingerprint(entry, root):
            problems.append(f"{name}: the recipe changed since {locked.get('version')}: "
                            f"tools/bump_profile.py {name} patch|minor|major -m '...'")
    for name in sorted(set(lock) - set(entries)):
        problems.append(f"{name}: in {LOCK_FILE} but not in the catalog (tools/bump_profile.py --prune)")
    vms, bases = tracked_documents(root)
    raw = {name: entry for name, (_, entry) in vms.items()}
    used = {base for name in list(raw) + list(bases) for base in profile_bases.chain_of(name, raw, bases)}
    for base in sorted(set(bases) - used):
        problems.append(f"{base}: a base nothing extends (drop it, or extend it)")
    return problems


def _write_profile_file(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _set_version(path: Path, name: str, version: str) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    entry = document["vms"][name]
    meta = entry.setdefault("meta", {})
    meta["version"] = version
    _write_profile_file(path, document)
    return dict(entry)


def _today(date: str | None) -> str:
    return date or _dt.date.today().isoformat()


def bump(name: str, part: str, note: str, root: Path | None = None, date: str | None = None) -> tuple[str, str]:
    """Raise *name*'s version by *part*, write the note into the lock's history, refresh the
    fingerprint. Returns (old, new). The note is what changed: it is the changelog."""
    if part not in PARTS:
        raise VMError(f"part must be one of {', '.join(PARTS)}")
    if not note.strip():
        raise VMError("a bump needs a note (-m): what changed for whoever installed the previous version")
    entries = tracked_entries(root)
    if name not in entries:
        raise VMError(f"{name} is not a tracked profile (local.json profiles are not versioned)")
    path, entry = entries[name]
    old = str((entry.get("meta") or {}).get("version") or "")
    match = SEMVER_RE.match(old)
    if not match:
        raise VMError(f"{name}: meta.version {old!r} is not MAJOR.MINOR.PATCH (tools/bump_profile.py --init)")
    major, minor, patch = (int(g) for g in match.groups())
    new = {"major": f"{major + 1}.0.0", "minor": f"{major}.{minor + 1}.0", "patch": f"{major}.{minor}.{patch + 1}"}[part]
    _set_version(path, name, new)
    entry = tracked_entries(root)[name][1]  # resolved over its base: the recipe the fingerprint covers
    lock = read_lock(root)
    record = lock["profiles"].setdefault(name, {"history": []})
    record.update({"version": new, "fingerprint": fingerprint(entry, root)})
    record.setdefault("history", []).insert(0, {"version": new, "date": _today(date), "note": note.strip()})
    write_lock(lock, root)
    return old, new


def init(note: str, root: Path | None = None, date: str | None = None) -> list[str]:
    """Stamp INITIAL_VERSION on every tracked profile without a version and record every profile
    missing from the lock (version, fingerprint, one history line). Returns the names touched."""
    touched: list[str] = []
    lock = read_lock(root)
    for name, (path, entry) in sorted(tracked_entries(root).items()):
        version = (entry.get("meta") or {}).get("version")
        if not is_semver(version):
            version = INITIAL_VERSION
            _set_version(path, name, version)
            entry = tracked_entries(root)[name][1]
            touched.append(name)
        if name not in lock["profiles"]:
            lock["profiles"][name] = {"version": version, "fingerprint": fingerprint(entry, root),
                                      "history": [{"version": version, "date": _today(date), "note": note.strip()}]}
            if name not in touched:
                touched.append(name)
    write_lock(lock, root)
    return touched


def prune(root: Path | None = None) -> list[str]:
    """Forget lock entries whose profile left the catalog."""
    lock = read_lock(root)
    gone = sorted(set(lock["profiles"]) - set(tracked_entries(root)))
    for name in gone:
        del lock["profiles"][name]
    if gone:
        write_lock(lock, root)
    return gone


def history(name: str, root: Path | None = None) -> list[dict[str, Any]]:
    entry = read_lock(root)["profiles"].get(name) or {}
    return list(entry.get("history") or [])
