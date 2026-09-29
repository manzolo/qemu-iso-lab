"""The guest identity of ``local.json`` (its top-level ``identity``): read and written by the
web's *Welcome* / *Identity* panel and by ``vmctl identity``.

The tracked catalog stays ``lab``/``lab``; the identity moves every tracked profile to one
user (``config.apply_identity``). The first ``vmctl web`` on a checkout without a local.json
opens the panel with the catalog's defaults, and the same panel edits the identity later.
The password hash is SHA-512 crypt (``$6$``, what ``openssl passwd -6`` and ``mkpasswd -m
sha-512`` produce) computed here, because Python's ``crypt`` module left the standard library
in 3.13 and the web must not shell out for it.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from typing import Any

from vmctl import catalog, config, ui
from vmctl.errors import VMError

DEFAULT_USER = "lab"
DEFAULT_PASSWORD = "lab"
# A POSIX login name (every installer and Windows accept it); the placeholder rules of
# config.validate_identity apply on top.
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
CRYPT_ALPHABET = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
CRYPT_ROUNDS = 5000  # glibc's default, left implicit in the string like the tools do
# The permutation of the digest bytes in a SHA-512 crypt string (Drepper's specification).
_CRYPT_ORDER = ((0, 21, 42), (22, 43, 1), (44, 2, 23), (3, 24, 45), (25, 46, 4), (47, 5, 26), (6, 27, 48),
                (28, 49, 7), (50, 8, 29), (9, 30, 51), (31, 52, 10), (53, 11, 32), (12, 33, 54), (34, 55, 13),
                (56, 14, 35), (15, 36, 57), (37, 58, 16), (59, 17, 38), (18, 39, 60), (40, 61, 19), (62, 20, 41))


def _b64(b2: int, b1: int, b0: int, count: int) -> str:
    word = (b2 << 16) | (b1 << 8) | b0
    out = []
    for _ in range(count):
        out.append(CRYPT_ALPHABET[word & 0x3F])
        word >>= 6
    return "".join(out)


def sha512_crypt(password: str, salt: str | None = None) -> str:
    """``$6$<salt>$<hash>`` of *password*: the same string ``openssl passwd -6 -salt <salt>`` prints."""
    if salt is None:
        salt = "".join(secrets.choice(CRYPT_ALPHABET) for _ in range(16))
    if not 1 <= len(salt) <= 16 or any(c not in CRYPT_ALPHABET for c in salt):
        raise VMError("The salt of a SHA-512 crypt hash is 1 to 16 characters of [./0-9A-Za-z]")
    key, salt_bytes = password.encode("utf-8"), salt.encode("ascii")
    digest_b = hashlib.sha512(key + salt_bytes + key).digest()
    a = hashlib.sha512()
    a.update(key + salt_bytes)
    a.update(digest_b * (len(key) // 64) + digest_b[: len(key) % 64])
    bits = len(key)
    while bits > 0:
        a.update(digest_b if bits & 1 else key)
        bits >>= 1
    digest_a = a.digest()
    digest_p = hashlib.sha512(key * len(key)).digest()
    p = digest_p * (len(key) // 64) + digest_p[: len(key) % 64]
    digest_s = hashlib.sha512(salt_bytes * (16 + digest_a[0])).digest()
    s = digest_s * (len(salt_bytes) // 64) + digest_s[: len(salt_bytes) % 64]
    c = digest_a
    for i in range(CRYPT_ROUNDS):
        step = hashlib.sha512()
        step.update(p if i & 1 else c)
        if i % 3:
            step.update(s)
        if i % 7:
            step.update(p)
        step.update(c if i & 1 else p)
        c = step.digest()
    encoded = "".join(_b64(c[x], c[y], c[z], 4) for x, y, z in _CRYPT_ORDER) + _b64(0, 0, c[63], 2)
    return f"$6${salt}${encoded}"


def read() -> dict[str, Any]:
    """What the panel shows: whether local.json exists, the identity it carries (never the
    password or the hash themselves) and the catalog's defaults."""
    path = catalog.local_path()
    exists = path.exists()
    document = catalog.read_document() if exists else {"vms": {}}
    raw = document.get("identity")
    identity: dict[str, str] = {}
    problem = ""
    if raw is not None:
        try:
            identity = config.validate_identity(raw, str(path))
        except VMError as exc:
            problem = str(exc)
    raw_locale = document.get("locale")
    locale: dict[str, str] = {}
    if raw_locale is not None:
        try:
            locale = config.validate_locale(raw_locale, str(path))
        except VMError as exc:
            problem = (problem + " " + str(exc)).strip()
    return {
        "exists": exists,
        "path": ui.pretty_path(path),
        "identity": {"user": identity.get("user", ""), "realname": identity.get("realname", ""),
                     "has_password": "password" in identity, "has_hash": "password_hash" in identity},
        # Language, keyboard and time zone for every installer section that has them (config.apply_locale).
        "locale": {key: locale.get(key, "") for key in config.LOCALE_KEYS},
        "problem": problem,
        "defaults": {"user": DEFAULT_USER, "password": DEFAULT_PASSWORD, "realname": "",
                     "language": "en_US.UTF-8", "keyboard": "us", "timezone": "UTC"},
        "overrides": len(document.get("vms") or {}),
    }


def save(user: str, password: str = "", realname: str = "", store_password: bool = True,
         locale: dict[str, str] | None = None) -> dict[str, Any]:
    """Write the identity: the user, a fresh SHA-512 hash of *password* (kept in clear too with
    *store_password*, for the profiles whose installer takes only a plain password: Windows,
    Arch, Alpine...) and the real name. An empty *password* keeps the credentials the file has.
    Everything else in local.json (per-VM overrides, My VMs, protected) is kept as it is, and
    the whole catalog is loaded with the candidate before the atomic write."""
    user = (user or "").strip()
    if not USER_RE.match(user):
        raise VMError("The user name must be a POSIX login name: lower-case letters, digits, '_' and '-', "
                      "starting with a letter or '_', at most 32 characters")
    document = catalog.read_document()
    raw_current = document.get("identity")
    current: dict[str, Any] = raw_current if isinstance(raw_current, dict) else {}
    identity: dict[str, str] = {"user": user}
    if password:
        identity["password_hash"] = sha512_crypt(password)
        if store_password:
            identity["password"] = password
    else:
        for key in ("password_hash", "password"):
            value = current.get(key)
            if isinstance(value, str) and value.strip():
                identity[key] = value
        if "password_hash" not in identity:
            raise VMError("A password is needed the first time (the catalog's default is 'lab')")
    if realname.strip():
        identity["realname"] = realname.strip()
    path = catalog.local_path()
    document["identity"] = config.validate_identity(identity, str(path))
    if locale is not None:
        # The locale block: the keys given (an empty value drops the key), the others as they were.
        raw_current_locale = document.get("locale")
        current_locale: dict[str, Any] = dict(raw_current_locale) if isinstance(raw_current_locale, dict) else {}
        for key, value in locale.items():
            if key not in config.LOCALE_KEYS:
                raise VMError(f"Unknown locale field '{key}' (allowed: {', '.join(config.LOCALE_KEYS)})")
            if value.strip():
                current_locale[key] = value.strip()
            else:
                current_locale.pop(key, None)
        if current_locale:
            document["locale"] = config.validate_locale(current_locale, str(path))
        else:
            document.pop("locale", None)
    config.load_config(local_profiles=document)  # every tracked profile must still resolve with it
    catalog.write_document(document)
    return read()


def cmd_identity(args: Any) -> int:
    """``vmctl identity [--user U] [--password P | --ask-password] [--realname R] [--no-store-password] [--json]``:
    with no change requested, show the identity local.json carries."""
    import json

    changing = any(getattr(args, key, None) is not None and getattr(args, key) is not False
                   for key in ("user", "password", "ask_password", "realname", "language", "keyboard", "timezone"))
    if changing:
        current = read()["identity"]
        password = getattr(args, "password", None) or ""
        if getattr(args, "ask_password", False):
            import getpass
            password = getpass.getpass("Guest password (empty keeps the current one): ")
        user = getattr(args, "user", None) or current["user"] or DEFAULT_USER
        realname = getattr(args, "realname", None)
        realname = current["realname"] if realname is None else realname
        if getattr(args, "dry_run", False):
            print(f"  would set identity user={user!r} realname={realname!r} password={'(new)' if password else '(kept)'} in {catalog.local_path()}")
            return 0
        locale = {key: getattr(args, key) for key in ("language", "keyboard", "timezone") if getattr(args, key, None) is not None}
        result = save(user, password, realname, store_password=not getattr(args, "no_store_password", False),
                      locale=locale or None)
        if not getattr(args, "json", False):
            ui.print_status("ok", f"identity saved in {result['path']}: user {result['identity']['user']}")
    else:
        result = read()
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return 0
    if not changing:
        identity = result["identity"]
        if not result["exists"]:
            print(f"No {result['path']} yet: every tracked profile is {DEFAULT_USER}/{DEFAULT_PASSWORD}. "
                  f"vmctl identity --user <name> --ask-password creates it (vmctl web opens the same form).")
        elif not identity["user"]:
            print(f"{result['path']} carries no identity: the tracked profiles stay {DEFAULT_USER}/{DEFAULT_PASSWORD}"
                  + (f" ({result['problem']})" if result["problem"] else ""))
        else:
            creds = "hash" + (" + password" if identity["has_password"] else " only")
            print(f"Guest identity in {result['path']}: user {identity['user']}"
                  + (f", real name {identity['realname']}" if identity["realname"] else "") + f" ({creds}); "
                  f"{result['overrides']} per-VM override(s)")
        loc = {k: v for k, v in result["locale"].items() if v}
        print("Locale: " + (", ".join(f"{k} {v}" for k, v in loc.items()) if loc else "the profiles' own (vmctl identity --language it_IT.UTF-8 --keyboard it --timezone Europe/Rome)"))
    return 0
