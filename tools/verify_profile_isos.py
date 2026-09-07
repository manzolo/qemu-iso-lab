#!/usr/bin/env python3
"""Read-only SHA-256 audit of cached, pinned profile ISOs."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iso-root", type=Path, default=root, help="Checkout that owns isos/")
    args = parser.parse_args()
    expected: dict[Path, set[str]] = {}
    for profile in sorted((root / "vms/profiles").glob("*.json")):
        if profile.name == "local.json":
            continue
        for vm in json.loads(profile.read_text())["vms"].values():
            if vm.get("iso_sha256") and not vm.get("iso_discovery"):
                path = args.iso_root / vm["iso"]
                expected.setdefault(path, set()).add(vm["iso_sha256"])
                if path.name == "alpine-virt-3.24.1-x86_64.iso":
                    legacy = path.with_name("alpine-virt-latest-stable-x86_64.iso")
                    if legacy.is_file():
                        expected.setdefault(legacy, set()).add(vm["iso_sha256"])

    def audit(item: tuple[Path, set[str]]) -> tuple[str, bool]:
        path, hashes = item
        if len(hashes) != 1:
            return f"CONFLICT {path.name}: profiles disagree on SHA-256", False
        if not path.is_file():
            return f"MISSING {path.name}", True
        before = path.stat()
        try:
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            after = path.stat()
        except OSError as exc:
            return f"ERROR {path.name}: {exc}", False
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            return f"CHANGED DURING AUDIT {path.name}", False
        wanted = next(iter(hashes))
        if actual != wanted:
            return f"MISMATCH {path.name}\n  expected: {wanted}\n  actual:   {actual}", False
        return f"PASS {path.name}: {actual}", True

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(audit, sorted(expected.items())))
    for message, _ in results:
        print(message)
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
