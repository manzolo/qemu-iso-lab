#!/usr/bin/env python3
"""Migrate renamed profile directories without overwriting or deleting data.

Defaults to a preview. Stop VMs and switch to the renamed catalog before --apply.
Only the explicit aliases of vmctl.config.PROFILE_ALIASES are migrated, plus the three manual
twins renamed on 2026-09-26 (TWIN_RENAMES); unrelated directories stay put.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vmctl.config import PROFILE_ALIASES  # noqa: E402


def rename_no_replace(source: Path, destination: Path) -> None:
    """Linux atomic directory rename: even an empty destination must survive."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


# 2026-09-26: the manual profiles freebsd, haiku and pearos-nicecore became <name>-installer and
# their old names went to the unattended profiles, so these cannot be aliases. An old directory
# under such a name holds the *manual* disk: it moves first, when it is recognisably manual (its
# state.json names no bootstrap flow) or when the old <name>-unattended directory still exists.
TWIN_RENAMES = {"freebsd": "freebsd-installer", "haiku": "haiku-installer", "pearos-nicecore": "pearos-nicecore-installer"}
TWIN_FLOWS = {"freebsd": "bootstrap-freebsd", "haiku": "bootstrap-haiku", "pearos-nicecore": "bootstrap-pearos"}


def twin_is_manual(root: Path, old: str) -> bool:
    directory = root / "artifacts" / old
    if not directory.is_dir() or directory.is_symlink():
        return False
    if (root / "artifacts" / f"{old}-unattended").exists():
        return True
    try:
        flow = json.loads((directory / "state.json").read_text()).get("install", {}).get("flow")
    except (OSError, ValueError, AttributeError):
        return False  # no record: it may already be the renamed unattended profile, leave it
    return bool(flow) and flow != TWIN_FLOWS[old]


def ordered_renames(root: Path) -> list[tuple[str, str]]:
    twins = [(old, new) for old, new in TWIN_RENAMES.items() if twin_is_manual(root, old)]
    return twins + list(PROFILE_ALIASES.items())


def migration_plan(root: Path) -> list[tuple[Path, Path]]:
    moves: list[tuple[Path, Path]] = []
    vacated: set[Path] = set()
    for old, new in ordered_renames(root):
        source, destination = root / "artifacts" / old, root / "artifacts" / new
        if not os.path.lexists(source) or source in {d for _, d in moves}:
            continue
        if source.is_symlink() or not source.is_dir():
            raise ValueError(f"Refusing non-directory or symlink: {source}")
        if os.path.lexists(destination) and destination not in vacated:
            raise ValueError(f"Conflict: both {source} and {destination} exist; nothing was overwritten")
        for pidfile in (source / "runtime").glob("*.pid"):
            try:
                pid = int(pidfile.read_text().strip())
                if pid <= 0:
                    raise ValueError(f"Invalid PID in {pidfile}")
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            except PermissionError:
                pass
            raise ValueError(f"Refusing migration while a process may be active: {pidfile}")
        moves.append((source, destination))
        vacated.add(source)
    return moves


def migrate_local_data(data: dict[str, Any]) -> dict[str, Any]:
    # A file still using this batch's old names predates the 2026-09-26 rename: its keys named
    # after a twin are the manual profiles. One mapping over the original keys, no chaining.
    old_file = any(key.endswith("-unattended") and key in PROFILE_ALIASES for key in data["vms"])
    mapping = {**(TWIN_RENAMES if old_file else {}), **PROFILE_ALIASES}
    def rewrite(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, str):
            # Exact path components, including absolute host source paths.
            return "/".join(mapping.get(part, part) for part in value.split("/"))
        return value
    result = dict(data)
    profiles: dict[str, Any] = {}
    for old, vm in data["vms"].items():
        new = mapping.get(old, old)
        if new in profiles:
            raise ValueError(f"Conflicting old and new local overrides for {new}; merge them explicitly")
        profiles[new] = rewrite(vm)
    result["vms"] = profiles
    return result


def migrate_local_file(path: Path, *, apply: bool) -> None:
    original = path.read_bytes()
    data = json.loads(original)
    updated = migrate_local_data(data)
    if updated == data:
        print("Local overrides already use canonical names")
        return
    print(f"Update local overrides: {path}")
    if not apply:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(path.name + ".backup-" + stamp)
    with backup.open("xb") as stream:
        os.chmod(backup, 0o600)
        stream.write(original)
    # Refuse to overwrite a concurrent edit after taking the backup.
    if path.read_bytes() != original:
        raise ValueError(f"Local overrides changed concurrently; backup kept at {backup}")
    path.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n")
    print(f"Backup saved: {backup}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--apply", action="store_true", help="Apply the previewed migration")
    parser.add_argument("--local-only", action="store_true", help="Only update local.json, with a backup")
    parser.add_argument("--local-config", type=Path, help="Also migrate this local.json file")
    args = parser.parse_args()
    try:
        moves = [] if args.local_only else migration_plan(args.root)
        # Validate local collisions before moving any directory.
        if args.local_config:
            migrate_local_data(json.loads(args.local_config.read_text()))
        for source, destination in moves:
            print(f"{source} -> {destination}")
            if args.apply:
                rename_no_replace(source, destination)
        if args.local_config:
            migrate_local_file(args.local_config, apply=args.apply)
        print(f"{'Migrated' if args.apply else 'Would migrate'} {len(moves)} artifact directories")
        return 0
    except (OSError, ValueError, AttributeError) as exc:
        print(f"Migration stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
