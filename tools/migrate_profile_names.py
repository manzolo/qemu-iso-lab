#!/usr/bin/env python3
"""Migrate renamed profile directories without overwriting or deleting data.

Defaults to a preview. Stop VMs and switch to the renamed catalog before --apply.
Only the sixteen explicit aliases are migrated; unrelated directories stay put.
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


def migration_plan(root: Path) -> list[tuple[Path, Path]]:
    moves = []
    for old, new in PROFILE_ALIASES.items():
        source, destination = root / "artifacts" / old, root / "artifacts" / new
        if not os.path.lexists(source):
            continue
        if source.is_symlink() or not source.is_dir():
            raise ValueError(f"Refusing non-directory or symlink: {source}")
        if os.path.lexists(destination):
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
    return moves


def migrate_local_data(data: dict[str, Any]) -> dict[str, Any]:
    def rewrite(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, str):
            # Exact path components, including absolute host source paths.
            return "/".join(PROFILE_ALIASES.get(part, part) for part in value.split("/"))
        return value
    result = dict(data)
    profiles: dict[str, Any] = {}
    for old, vm in data["vms"].items():
        new = PROFILE_ALIASES.get(old, old)
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
