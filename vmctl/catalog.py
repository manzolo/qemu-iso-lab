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

from vmctl import config, state
from vmctl.errors import VMError

KEY = "catalog"
ACTIONS = ("list", "add", "remove", "set", "clear")
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


def selection_of(document: dict[str, Any]) -> list[str]:
    """The selected names of a local.json document: canonical, in order, without duplicates."""
    section = document.get(KEY)
    if section is None:
        return []
    if not isinstance(section, dict) or not isinstance(section.get("selected", []), list) \
            or not all(isinstance(entry, str) for entry in section.get("selected", [])):
        raise VMError(f"local.json: '{KEY}' must be an object with a 'selected' list of profile names")
    names: list[str] = []
    for entry in section.get("selected", []):
        name = config.canonical_vm_name(entry, warn=False)
        if name not in names:
            names.append(name)
    return names


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


def update(action: str, names: list[str], cfg: dict[str, Any]) -> dict[str, Any]:
    """``add``/``remove``/``set``/``clear`` the selection and save it. Every name must be a
    profile of *cfg* (aliases resolve), so a typo never lands in the file. Returns the new
    selection with what changed; nothing is written when nothing changed."""
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
        current = selection_of(document)
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
        if new:
            section: dict[str, Any] = document[KEY] if isinstance(document.get(KEY), dict) else {}
            document[KEY] = {**section, "selected": new}
        else:
            document.pop(KEY, None)
        path = local_path()
        if path.exists():
            _atomic_write(path.with_suffix(".json.bak"), path.read_bytes())
        _atomic_write(path, (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode())
        return result
