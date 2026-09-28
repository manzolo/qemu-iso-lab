"""Profile bases: the recipe several profiles share, written once and extended.

A profile file may carry, next to ``vms``, a ``bases`` object. A base looks like a profile but is
not a VM: it is never listed, installed or versioned on its own, and it needs no ``iso``, disk or
port. A VM profile (or another base) names its base with ``"extends": "<base>"`` and carries only
what differs; ``resolve_extends`` merges it over the base with the rules of a local override
(objects merge, lists append, the child wins on scalars) and then replaces ``{{name}}`` in every
string with the profile's own key, so a base can say ``artifacts/{{name}}/disk.qcow2``.

The resolved entry is what every consumer sees: ``load_config`` (the CLI, the dashboards, the
flows), the profile versions (a base edit changes the fingerprint of every child, so it needs a
bump of each: ``tools/bump_profile.py <base> patch --children -m '...'``), the catalog site and
the web editor's catalog template. Nothing downstream knows about bases.
"""
from __future__ import annotations

import copy
from typing import Any, cast

from vmctl.errors import VMError

BASES_KEY = "bases"
EXTENDS_KEY = "extends"
NAME_PLACEHOLDER = "{{name}}"
# What a base must not carry: a version is bumped per profile and a live PASS is earned per profile.
PER_PROFILE_META = ("version", "verified")


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """*override* on top of *base*: objects merge recursively, lists append, anything else replaces."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge(cast(dict[str, Any], merged[key]), value)
        elif isinstance(value, list) and isinstance(merged.get(key), list):
            merged[key] = copy.deepcopy(merged[key]) + copy.deepcopy(value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def replace_placeholder(value: Any, placeholder: str, text: str) -> Any:
    if isinstance(value, str):
        return value.replace(placeholder, text)
    if isinstance(value, dict):
        return {k: replace_placeholder(v, placeholder, text) for k, v in value.items()}
    if isinstance(value, list):
        return [replace_placeholder(v, placeholder, text) for v in value]
    return value


def bases_of(document: dict[str, Any], origin: str) -> dict[str, dict[str, Any]]:
    """The ``bases`` object of one profile document (empty when absent), each base an object."""
    section = document.get(BASES_KEY)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise VMError(f"Invalid profile file: {origin}: '{BASES_KEY}' must be an object")
    for name, entry in section.items():
        if not isinstance(entry, dict):
            raise VMError(f"Invalid base '{name}' in {origin}: expected an object")
    return cast(dict[str, dict[str, Any]], section)


def resolve_extends(vms: dict[str, dict[str, Any]], bases: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every VM entry merged over its base chain, ``{{name}}`` replaced by its key.

    The resolved VM keeps ``extends`` (the catalog can say what it is built on; the fingerprint
    ignores it). A base that extends a base is resolved first; a cycle, an unknown base and a
    name that is both a VM and a base are errors that name the entry."""
    collision = sorted(set(vms) & set(bases))
    if collision:
        raise VMError(f"'{collision[0]}' is both a VM profile and a base")
    resolved_bases: dict[str, dict[str, Any]] = {}

    def parent_of(entry: dict[str, Any], name: str) -> str | None:
        parent = entry.get(EXTENDS_KEY)
        if parent is None:
            return None
        if not isinstance(parent, str) or not parent:
            raise VMError(f"{name}: '{EXTENDS_KEY}' must be the name of a base")
        return parent

    def resolve_base(name: str, chain: tuple[str, ...]) -> dict[str, Any]:
        if name in resolved_bases:
            return resolved_bases[name]
        if name in chain:
            raise VMError(f"base '{name}' extends itself: {' -> '.join(chain + (name,))}")
        if name not in bases:
            known = ", ".join(sorted(bases)) or "none"
            raise VMError(f"{chain[-1]}: extends unknown base '{name}' (bases: {known})")
        entry = bases[name]
        for key in PER_PROFILE_META:
            if key in (entry.get("meta") or {}):
                raise VMError(f"base '{name}': meta.{key} belongs to each profile, not to a base")
        parent = parent_of(entry, name)
        own = {key: value for key, value in entry.items() if key != EXTENDS_KEY}
        resolved = copy.deepcopy(own) if parent is None else merge(resolve_base(parent, chain + (name,)), own)
        resolved_bases[name] = resolved
        return resolved

    out: dict[str, dict[str, Any]] = {}
    for name, entry in vms.items():
        parent = parent_of(entry, name)
        if parent is not None:
            own = {key: value for key, value in entry.items() if key != EXTENDS_KEY}
            entry = merge(resolve_base(parent, (name,)), own)
            entry[EXTENDS_KEY] = parent
        out[name] = cast(dict[str, Any], replace_placeholder(entry, NAME_PLACEHOLDER, name))
    return out


def chain_of(name: str, vms: dict[str, dict[str, Any]], bases: dict[str, dict[str, Any]]) -> list[str]:
    """The bases a VM (or base) is built on, nearest first; empty when it extends nothing."""
    chain: list[str] = []
    entry = vms.get(name) or bases.get(name) or {}
    parent = entry.get(EXTENDS_KEY)
    while isinstance(parent, str) and parent and parent not in chain:
        chain.append(parent)
        parent = (bases.get(parent) or {}).get(EXTENDS_KEY)
    return chain
