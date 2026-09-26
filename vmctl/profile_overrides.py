"""Review and save one local profile override without modifying the catalog."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from vmctl import config, state
from vmctl.errors import VMError

_LOCK = threading.Lock()


def _revision() -> str:
    digest = hashlib.sha256()
    for path in sorted((state.CONFIG_DIR / "profiles").glob("*.json")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _document() -> tuple[Path, dict[str, Any]]:
    path = state.CONFIG_DIR / "profiles" / "local.json"
    try:
        document = json.loads(path.read_text()) if path.exists() else {"vms": {}}
    except (ValueError, OSError) as exc:
        raise VMError(f"Cannot read local overrides: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("vms"), dict):
        raise VMError("local.json must contain a 'vms' object")
    return path, document


def _local_key(document: dict[str, Any], name: str) -> str:
    return next((key for key in document["vms"] if config.canonical_vm_name(key, warn=False) == name), name)


def read_override(name: str) -> dict[str, Any]:
    name = config.canonical_vm_name(name, warn=False)
    revision = _revision()
    effective = config.get_vm(config.load_config(), name)
    _, document = _document()
    base = None
    for path in sorted((state.CONFIG_DIR / "profiles").glob("*.json")):
        if path.name != "local.json":
            entry = json.loads(path.read_text()).get("vms", {}).get(name)
            if entry is not None:
                base = entry
                break
    if revision != _revision():
        raise VMError("Profiles changed while loading. Open the editor again.")
    return {"name": name, "base": base, "effective": effective,
            "override": document["vms"].get(_local_key(document, name), {}),
            "revision": revision, "local_only": base is None}


def _atomic_write(path: Path, contents: bytes) -> None:
    # mkstemp keeps potentially private provisioning values readable only by the owner.
    fd, temporary = tempfile.mkstemp(prefix=".vmctl-local-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_override(name: str, override: Any, revision: str) -> dict[str, Any]:
    if not isinstance(override, dict):
        raise VMError("The override must be a JSON object")
    name = config.canonical_vm_name(name, warn=False)
    with _LOCK:
        current = read_override(name)
        if revision != current["revision"]:
            raise VMError("Profiles changed since this editor was opened. Reopen it before saving.")
        if current["local_only"] and not override:
            raise VMError("This profile exists only locally and has no catalog template to restore")
        path, document = _document()
        key = _local_key(document, name)
        if override:
            document["vms"][key] = override
        else:
            document["vms"].pop(key, None)
        # Validate the entire candidate configuration, including placeholders and port conflicts.
        candidate = config.load_config(local_profiles=document)
        effective = config.get_vm(candidate, name)
        for field in ("memory_mb", "cpus"):
            if type(effective[field]) is not int or effective[field] <= 0:
                raise VMError(f"{field} must be a positive integer")
        if revision != _revision():
            raise VMError("Profiles changed during validation. Reopen the editor before saving.")
        if path.exists():
            _atomic_write(path.with_suffix(".json.bak"), path.read_bytes())
        _atomic_write(path, (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode())
        return read_override(name)
