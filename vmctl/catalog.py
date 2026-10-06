"""My VMs: the personal subset of the catalog, chosen by name and kept in local.json.

The catalog ships every profile; most people want a handful. The chosen names live in
``vms/profiles/local.json`` (already the home of everything personal) under a top-level
``"catalog": {"selected": [...]}`` next to ``"vms"``: ``load_config`` ignores the key, every
writer of local.json rewrites the whole document, so nothing loses it. The dashboards read the
selection as the ``mine`` fact of each row; their *My VMs* view shows the selection plus
whatever is running right now (a running VM never hides). An empty selection means
the whole catalog (the key is then removed), which is what a fresh checkout shows.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from vmctl import config, state, vmstate
from vmctl.errors import VMError

KEY = "catalog"
ACTIONS = ("list", "add", "remove", "set", "clear", "hide", "unhide")
HIDDEN_FIELD = "hidden"  # "catalog": {"hidden": [...]}: the profiles the dashboards leave out of their lists
_LOCK = threading.Lock()


def local_path() -> Path:
    return state.CONFIG_DIR / "profiles" / "local.json"


def _document() -> dict[str, Any]:
    path = local_path()
    try:
        document = json.loads(path.read_text()) if path.exists() else {"vms": {}}
    except (ValueError, OSError) as exc:
        raise VMError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.setdefault("vms", {}), dict):
        raise VMError(f"{path} must be a JSON object with a 'vms' object")
    return document


def read_document() -> dict[str, Any]:
    """local.json as a document (``{"vms": {}}`` while it does not exist): what every writer of
    the file starts from, so no key another feature keeps is lost (identity, catalog, protected, vms)."""
    return _document()


def write_document(document: dict[str, Any]) -> Path:
    """Write the whole document back: ``.bak`` of the previous file, then an atomic replace."""
    path = local_path()
    if path.exists():
        _atomic_write(path.with_suffix(".json.bak"), path.read_bytes())
    _atomic_write(path, (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode())
    return path


def selection_of(document: dict[str, Any], key: str = KEY, field: str = "selected") -> list[str]:
    """The selected names of a local.json document: canonical, in order, without duplicates."""
    section = document.get(key)
    if section is None:
        return []
    if not isinstance(section, dict) or not isinstance(section.get(field, []), list) \
            or not all(isinstance(entry, str) for entry in section.get(field, [])):
        raise VMError(f"local.json: '{key}' must be an object with a '{field}' list of profile names")
    names: list[str] = []
    for entry in section.get(field, []):
        name = config.canonical_vm_name(entry, warn=False)
        if name not in names:
            names.append(name)
    return names


def in_my_vms(mine: bool, running: bool, lab: str | None) -> bool:
    """Who the My VMs view lists: the selection, plus what runs right now unless a declared lab
    owns it. A running lab fills the list with members nobody chose one by one, and they already
    have their place in the Labs view (Manzolo, 2026-10-06); a starred member still shows. The
    dashboards and ``vmctl list --mine`` all ask this; the web page says the same in JS."""
    return mine or (running and not lab)


def selected() -> list[str]:
    """The saved selection. Names no longer in the catalog (a cleaned clone) stay until removed."""
    return selection_of(_document())


def _atomic_write(path: Path, contents: bytes) -> None:
    # Owner-only like profile_overrides: local.json holds passwords and keys.
    fd, temporary = tempfile.mkstemp(prefix=".vmctl-local-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def hidden() -> list[str]:
    """The profiles hidden from the dashboards' lists (``vmctl catalog hide``): the opposite of a
    star, so a name is never in both. A hidden VM still shows while it runs or holds a disk."""
    return selection_of(_document(), KEY, HIDDEN_FIELD)


def update_hidden(action: str, names: list[str], cfg: dict[str, Any]) -> dict[str, Any]:
    """``add``/``remove`` hidden profiles; hiding a starred VM takes its star away."""
    return update(action, names, cfg, key=KEY, field=HIDDEN_FIELD)


def protected() -> list[str]:
    """The protected VMs (``vmctl protect``): what vmstate.refuse_if_protected guards."""
    return selection_of(_document(), vmstate.PROTECTED_KEY, "vms")


def update_protected(action: str, names: list[str], cfg: dict[str, Any]) -> dict[str, Any]:
    """``add``/``remove`` protected VMs; the same checks and atomic write as My VMs."""
    return update(action, names, cfg, key=vmstate.PROTECTED_KEY, field="vms")


def update(action: str, names: list[str], cfg: dict[str, Any], key: str = KEY, field: str = "selected") -> dict[str, Any]:
    """``add``/``remove``/``set``/``clear`` the selection and save it. Every name must be a
    profile of *cfg* (aliases resolve), so a typo never lands in the file. Returns the new
    selection with what changed; nothing is written when nothing changed. *key*/*field* name
    the list: My VMs by default, the protected VMs for ``update_protected``."""
    if action not in ("add", "remove", "set", "clear"):
        raise VMError(f"Unknown catalog action: {action}")
    resolved: list[str] = []
    for entry in names:
        name = config.canonical_vm_name(entry, warn=False)
        if name not in resolved:
            resolved.append(name)
    unknown = [name for name in resolved if name not in cfg["vms"]]
    if unknown:
        raise VMError(f"Not in the catalog: {', '.join(unknown)} (vmctl list --names shows every profile)")
    if action != "clear" and not resolved:
        raise VMError(f"catalog {action} needs at least one profile name")
    with _LOCK:
        document = _document()
        current = selection_of(document, key, field)
        if action == "add":
            new = current + [name for name in resolved if name not in current]
        elif action == "remove":
            new = [name for name in current if name not in resolved]
        elif action == "set":
            new = resolved
        else:
            new = []
        result = {"selected": new, "added": [n for n in new if n not in current],
                  "removed": [n for n in current if n not in new]}
        if new == current:
            return result
        section: dict[str, Any] = dict(document[key]) if isinstance(document.get(key), dict) else {}
        if new:
            section[field] = new
        else:
            section.pop(field, None)
        # A star and "hidden" exclude each other: the list just written wins over the other one.
        other = {"selected": HIDDEN_FIELD, HIDDEN_FIELD: "selected"}.get(field)
        if key == KEY and other and action in ("add", "set"):
            kept = [name for name in selection_of(document, key, other) if name not in new]
            if kept:
                section[other] = kept
            else:
                section.pop(other, None)
        if section:
            document[key] = section
        else:
            document.pop(key, None)
        write_document(document)
        return result
