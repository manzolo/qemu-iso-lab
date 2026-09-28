#!/usr/bin/env python3
"""Carve a shared base out of existing profiles (docs/PROFILES.md, "Profile bases").

    tools/extract_profile_base.py vms/profiles/debian.json debian-preseed-base debian-server debian-xfce debian-kde debian-gnome
    tools/extract_profile_base.py vms/profiles/debian.json debian-desktop-base debian-xfce debian-kde debian-gnome --parent debian-preseed-base --write

The part the named profiles share becomes the base (scalars and objects identical everywhere,
lists by their longest common prefix; meta.version and meta.verified stay per profile), each
profile keeps only its delta with "extends", and disk.path / firmware.vars_path get {{name}}.
With --parent the new base itself extends an existing one and holds only what the parent does
not. Without --write it only reports. It writes only when every profile resolves to exactly
the entry it had before, so vms/profiles.lock does not change and nothing needs a bump or a
new live run; a profile that already extends a base is made whole first.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vmctl import profile_bases  # noqa: E402

PLACEHOLDER_KEYS = (("disk", "path"), ("firmware", "vars_path"))


def common(values):
    """The shared part of several values of the same key, or None when nothing is shared."""
    first = values[0]
    if all(v == first for v in values):
        return copy.deepcopy(first)
    if all(isinstance(v, dict) for v in values):
        out = {}
        for key in first:
            if all(key in v for v in values):
                shared = common([v[key] for v in values])
                if shared is not None and shared != {} and shared != []:
                    out[key] = shared
        return out or None
    if all(isinstance(v, list) for v in values):
        prefix = []
        for i, item in enumerate(first):
            if all(len(v) > i and v[i] == item for v in values):
                prefix.append(copy.deepcopy(item))
            else:
                break
        return prefix or None
    return None


def delta(entry, base):
    """What *entry* must carry so that merge(base, delta) == entry."""
    out = {}
    for key, value in entry.items():
        if key not in base:
            out[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(base[key], dict):
            sub = delta(value, base[key])
            if sub:
                out[key] = sub
        elif isinstance(value, list) and isinstance(base[key], list):
            assert value[:len(base[key])] == base[key], (key, value, base[key])
            rest = value[len(base[key]):]
            if rest:
                out[key] = copy.deepcopy(rest)
        elif value != base[key]:
            out[key] = copy.deepcopy(value)
    return out


def with_name_placeholder(entry, name):
    entry = copy.deepcopy(entry)
    for section, key in PLACEHOLDER_KEYS:
        value = entry.get(section, {}).get(key)
        if isinstance(value, str) and f"/{name}/" in value:
            entry[section][key] = value.replace(f"/{name}/", "/{{name}}/")
    return entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file"); ap.add_argument("base"); ap.add_argument("vms", nargs="+")
    ap.add_argument("--parent"); ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    path = Path(a.file)
    doc = json.loads(path.read_text())
    bases = doc.get("bases", {})
    original = profile_bases.resolve_extends(copy.deepcopy(doc["vms"]), bases)  # the truth to preserve
    for v in original.values():
        v.pop("extends", None)

    def resolve_raw(name):  # a base chain merged, {{name}} left as it is
        entry = dict(bases[name]); parent = entry.pop("extends", None)
        return profile_bases.merge(resolve_raw(parent), entry) if parent else entry

    def full(entry):  # an entry that already extends a base, made whole (placeholders kept)
        entry = dict(entry); parent = entry.pop("extends", None)
        return profile_bases.merge(resolve_raw(parent), entry) if parent else entry

    # Work on whole entries but with {{name}} where the name appears in paths, so the base can carry them.
    raw = {n: with_name_placeholder(full(doc["vms"][n]), n) for n in a.vms}
    base = common(list(raw.values()))
    if a.parent:
        base = delta(base, resolve_raw(a.parent))
        base["extends"] = a.parent
    for key in ("version", "verified"):  # earned per profile, never in a base
        base.get("meta", {}).pop(key, None)
    if base.get("meta") == {}:
        base.pop("meta")
    # keys a base must never carry
    for key in ("name", "extends" if not a.parent else None):
        if key and key in base and key != "extends":
            base.pop(key)
    new_vms = {}
    bases_tmp = {**bases, "_b": base}
    entry_b = dict(base); parent_b = entry_b.pop("extends", None)
    base_for_delta = profile_bases.merge(resolve_raw(parent_b), entry_b) if parent_b else entry_b
    for n in a.vms:
        d = delta(raw[n], base_for_delta)
        new_vms[n] = {"name": d.pop("name"), "extends": a.base, **d}
    bases_out = dict(bases); bases_out[a.base] = base
    check_vms = dict(doc["vms"])
    for n in a.vms:
        check_vms[n] = new_vms[n]
    resolved = profile_bases.resolve_extends(check_vms, bases_out)
    for n in a.vms:
        r = dict(resolved[n]); r.pop("extends", None)
        if r != original[n]:
            print("MISMATCH", n)
            for k in set(r) | set(original[n]):
                if r.get(k) != original[n].get(k):
                    print("   ", k, "\n     new:", json.dumps(r.get(k))[:300], "\n     old:", json.dumps(original[n].get(k))[:300])
            return 1
    before = sum(len(json.dumps(original[n])) for n in a.vms)
    after = sum(len(json.dumps(new_vms[n])) for n in a.vms) + len(json.dumps(base))
    print(f"{a.base}: {len(base)} top-level keys; {len(a.vms)} profiles {before} -> {after} bytes (deltas: "
          + ", ".join(f"{n}={len(json.dumps(new_vms[n]))}" for n in a.vms) + ")")
    print("base keys:", ", ".join(base))
    for n in a.vms:
        print(f"  {n}: {', '.join(k for k in new_vms[n] if k not in ('name', 'extends'))}")
    if a.write:
        out = {"bases": bases_out, "vms": check_vms}
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
        print("written", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
