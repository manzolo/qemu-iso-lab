"""``vmctl iso browse|check|set``: hand vmctl an ISO the user already has.

Windows media, signed vendor links and other mediums only the user can fetch used to need a
rename into ``isos/`` or a hand edit of ``local.json``. ``set_iso`` does either after ``check``
has looked at the file: an ISO 9660 image, the profile's pinned hashes, and for Windows the
image names inside ``install.wim``/``install.esd`` against ``windows_config.edition`` (the
check done by hand on 2026-10-02 for a 26H2 ISO, now one call).

The default keeps the file where it is and writes ``"iso"`` for the profile in ``local.json``
(instant, no 9 GB copy, the file stays in the user's own library); ``--move`` puts it under
the name the tracked profile expects in ``isos/``, renaming on the same file system and
copying across file systems. ``browse`` is the host-side file picker of the web page: the
browser never uploads the medium, the server reads the path where it already is.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import struct
import subprocess
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import IO, Any

from vmctl import catalog, config, iso, runtime, state, ui, windows
from vmctl.errors import VMError

ISO_SUFFIXES = (".iso",)
BROWSE_LIMIT = 1000
# WIMHEADER_V1_PACKED: "MSWIM\0\0\0" at 0, the XML resource header (RESHDR_DISK_SHORT: 7-byte
# size, 1 flag byte, 8-byte offset, 8-byte original size) at 72. The XML is stored uncompressed
# as UTF-16 near the end of the file, in install.esd too.
WIM_MAGIC = b"MSWIM\x00\x00\x00"
WIM_HEADER_SIZE = 208
WIM_XML_RESHDR = 72
WIM_XML_MAX = 16 * 1024 * 1024
WINDOWS_IMAGES = ("sources/install.wim", "sources/install.esd")


# --- browsing -------------------------------------------------------------------------------

def downloads_dir() -> Path:
    """The XDG download directory (``~/Scaricati`` on an Italian desktop), else ``~/Downloads``."""
    home = Path.home()
    try:
        text = (home / ".config" / "user-dirs.dirs").read_text(encoding="utf-8")
    except OSError:
        text = ""
    match = re.search(r'^XDG_DOWNLOAD_DIR="([^"]+)"', text, re.MULTILINE)
    if match:
        candidate = Path(match.group(1).replace("$HOME", str(home)))
        if candidate.is_dir():
            return candidate
    return home / "Downloads" if (home / "Downloads").is_dir() else home


def places(cfg: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Shortcuts for the picker: home, downloads, the ISO cache and every directory a profile's
    medium already lives in outside it (storage/Iso/Windows on the maintainer's host)."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(label: str, path: Path) -> None:
        key = str(path)
        if key not in seen and path.is_dir():
            seen.add(key)
            found.append({"label": label, "path": key})

    add("Home", Path.home())
    add("Downloads", downloads_dir())
    add("isos/", state.ROOT / "isos")
    cache = (state.ROOT / "isos").resolve()
    for _, vm in config.sorted_vm_items(cfg if cfg is not None else config.load_config()):
        try:
            medium = iso.medium_path(vm)
        except (KeyError, TypeError):
            continue
        if medium.is_file() and medium.resolve().parent != cache:
            add(ui.pretty_path(medium.parent), medium.parent)
    return found


def browse(directory: str | None = None) -> dict[str, Any]:
    """One directory of the host: subdirectories and ISO files, hidden entries left out."""
    path = Path(directory).expanduser() if directory else downloads_dir()
    if not path.is_absolute():
        raise VMError("Give an absolute path")
    path = path.resolve()
    if not path.is_dir():
        raise VMError(f"Not a directory: {path}")
    entries: list[dict[str, Any]] = []
    try:
        children = sorted(path.iterdir(), key=lambda child: (not child.is_dir(), child.name.lower()))
    except OSError as exc:
        raise VMError(f"Cannot list {path}: {exc.strerror or exc}") from exc
    for child in children:
        if child.name.startswith("."):
            continue
        try:
            if child.is_dir():
                entries.append({"name": child.name, "kind": "dir"})
            elif child.is_file() and child.suffix.lower() in ISO_SUFFIXES:
                st = child.stat()
                entries.append({"name": child.name, "kind": "iso", "size": st.st_size, "mtime": int(st.st_mtime)})
        except OSError:
            continue
    return {"path": str(path), "parent": str(path.parent) if path.parent != path else None,
            "entries": entries[:BROWSE_LIMIT], "truncated": len(entries) > BROWSE_LIMIT,
            "places": places()}


# --- checks ---------------------------------------------------------------------------------

def is_iso9660(path: Path) -> bool:
    """The primary volume descriptor's "CD001" at sector 16 (Windows media carry it too, as the
    ISO 9660 bridge in front of their UDF tree)."""
    try:
        with path.open("rb") as handle:
            handle.seek(16 * 2048 + 1)
            return handle.read(5) == b"CD001"
    except OSError:
        return False


def wim_images(stream: IO[bytes]) -> list[dict[str, Any]]:
    """The images of a WIM/ESD read front to back from a stream (a pipe from 7z): the header
    says where the XML is, everything before it is skipped without being kept. One entry per
    image: ``name`` (what ``windows_config.edition`` matches) and its ``languages``."""
    header = stream.read(WIM_HEADER_SIZE)
    if len(header) < WIM_HEADER_SIZE or not header.startswith(WIM_MAGIC):
        raise VMError("not a WIM image (no MSWIM header)")
    size = int.from_bytes(header[WIM_XML_RESHDR:WIM_XML_RESHDR + 7], "little")
    (offset,) = struct.unpack_from("<Q", header, WIM_XML_RESHDR + 8)
    if not 0 < size <= WIM_XML_MAX or offset < WIM_HEADER_SIZE:
        raise VMError("the WIM header names no usable XML data")
    remaining = offset - WIM_HEADER_SIZE
    while remaining:
        chunk = stream.read(min(remaining, 4 * 1024 * 1024))
        if not chunk:
            raise VMError("the WIM image ends before its XML data")
        remaining -= len(chunk)
    data = stream.read(size)
    if len(data) < size:
        raise VMError("the WIM image ends inside its XML data")
    try:
        root = ElementTree.fromstring(data.decode("utf-16"))
    except (UnicodeDecodeError, ElementTree.ParseError) as exc:
        raise VMError(f"unreadable WIM XML: {exc}") from exc
    return [{"name": str(image.findtext("NAME") or image.findtext("DISPLAYNAME") or "").strip(),
             "languages": [str(lang.text or "").strip() for lang in image.findall("WINDOWS/LANGUAGES/LANGUAGE")]}
            for image in root.findall("IMAGE")]


def _read_windows_images(iso_path: Path) -> list[dict[str, Any]]:
    seven = windows.seven_zip_command()
    try:
        listing = runtime.run_output([seven, "l", "-ba", str(iso_path), *WINDOWS_IMAGES])
    except Exception as exc:  # noqa: BLE001 - 7z says "no files" with a non-zero exit too
        raise VMError(f"7z cannot list {iso_path.name}: {exc}") from exc
    member = next((name for name in WINDOWS_IMAGES if name in listing.replace("\\", "/")), None)
    if member is None:
        raise VMError("no sources/install.wim or sources/install.esd in this ISO: not a Windows setup medium")
    ui.print_note(f"Reading the image list of {member} (streams the whole file once, cached afterwards)")
    process = subprocess.Popen([seven, "e", "-so", str(iso_path), member], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        assert process.stdout is not None
        return wim_images(process.stdout)
    finally:
        process.kill()
        process.wait()


def _cache_file(iso_path: Path) -> Path:
    digest = hashlib.sha256(str(iso_path.resolve()).encode()).hexdigest()[:16]
    return state.ROOT / "isos" / ".wiminfo" / f"{digest}.json"


def windows_images(iso_path: Path) -> list[dict[str, Any]]:
    """The images inside the ISO's install.wim (or install.esd), streamed through 7z: the files
    live in UDF only, and extracting an 8 GB WIM just to read its XML would fill /tmp. Cached
    under isos/.wiminfo/ per medium (path, size, mtime)."""
    st = iso_path.stat()
    stamp = f"{iso_path.resolve()}|{st.st_size}|{st.st_mtime_ns}"
    cache = _cache_file(iso_path)
    try:
        cached = json.loads(cache.read_text())
        if cached.get("stamp") == stamp and isinstance(cached.get("images"), list):
            return list(cached["images"])
    except (OSError, ValueError):
        pass
    images = _read_windows_images(iso_path)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"stamp": stamp, "images": images}, indent=1))
    except OSError:
        pass
    return images


def windows_mismatch(win: dict[str, Any], images: list[dict[str, Any]]) -> tuple[list[str], str | None]:
    """What the answer file asks that the medium cannot give: problems (an edition it does not
    hold, a language it does not have and cannot be inferred) and the language to use instead
    when the medium has exactly one (Setup silently drops a foreign UILanguage and waits on its
    first page: an Italian ISO with the catalog's en-US, 2026-10-02)."""
    problems: list[str] = []
    names = [image["name"] for image in images]
    edition = str(win.get("edition") or "")
    if win.get("image_index") is None and edition and edition not in names:
        problems.append(f"no image named '{edition}' (windows_config.edition) in this ISO; it has: " + ", ".join(names))
    languages = sorted({lang for image in images for lang in image.get("languages", []) if lang})
    wanted = str(win.get("language") or "en-US")
    use: str | None = None
    if languages and wanted not in languages:
        if len(languages) == 1:
            use = languages[0]
        else:
            problems.append(f"windows_config.language is {wanted}, the ISO has " + ", ".join(languages))
    return problems, use


def reconcile_windows_medium(vm_name: str, vm: dict[str, Any], iso_path: Path, dry_run: bool = False) -> dict[str, Any]:
    """Before Setup boots: refuse a medium without the edition, follow the medium's language.
    An unreadable image list (no 7z) only warns: the flow then behaves as before."""
    win = vm.get("windows_config")
    if dry_run or not isinstance(win, dict) or not iso_path.is_file():
        return vm
    try:
        images = windows_images(iso_path)
    except VMError as exc:
        ui.print_status("warn", f"Edition and language of {iso_path.name} not checked: {exc}", ok=False)
        return vm
    problems, use = windows_mismatch(win, images)
    if problems:
        raise VMError(f"{iso_path.name} does not match {vm_name}: " + "; ".join(problems)
                      + f". Set windows_config in vms/profiles/local.json, or give it another ISO: vmctl iso set {vm_name} <path>")
    if use is not None:
        ui.print_note(f"{iso_path.name} is a {use} medium: Setup runs in {use} (windows_config.language says "
                      f"{win.get('language') or 'en-US'}; set it in local.json to silence this note)")
        return {**vm, "windows_config": {**win, "language": use}}
    return vm


def check(vm_name: str, path: str | Path, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Is *path* a medium this profile can use? ``problems`` refuse it, ``notes`` only inform."""
    cfg = cfg if cfg is not None else config.load_config()
    name = config.canonical_vm_name(vm_name, warn=False)
    vm = config.get_vm(cfg, name)
    source = Path(path).expanduser()
    if not source.is_absolute():
        source = Path.cwd() / source
    result: dict[str, Any] = {"vm": name, "path": str(source), "problems": [], "notes": [], "editions": None}
    problems: list[str] = result["problems"]
    notes: list[str] = result["notes"]
    if vm.get("disk_image"):
        problems.append(f"{name} starts from a built disk image, not an ISO (disk_image.path)")
        return result
    if not source.is_file():
        problems.append(f"no such file: {source}")
        return result
    result["size"] = source.stat().st_size
    problems += iso.validate_iso_file(source, vm)
    if not problems and not is_iso9660(source):
        problems.append("no ISO 9660 volume descriptor: not a CD/DVD image")
    win = vm.get("windows_config")
    if isinstance(win, dict) and not problems:
        edition = str(win.get("edition") or "")
        result["edition"] = edition
        try:
            images = windows_images(source)
        except VMError as exc:
            if "not a Windows setup medium" in str(exc):
                problems.append(str(exc))
            else:
                notes.append(f"Editions not checked: {exc}")
        else:
            result["editions"] = [image["name"] for image in images]
            result["languages"] = sorted({lang for image in images for lang in image.get("languages", []) if lang})
            found, use = windows_mismatch(win, images)
            problems += found
            if win.get("image_index") is not None:
                notes.append(f"windows_config.image_index {win['image_index']} picks the image by number")
            if use is not None:
                notes.append(f"a {use} medium: Setup will run in {use} (windows_config.language is {win.get('language') or 'en-US'})")
    current = iso.medium_path(vm)
    result["current"] = str(current)
    shared = [other for other, profile in config.sorted_vm_items(cfg)
              if other != name and not profile.get("disk_image") and iso.medium_path(profile) == current]
    if shared:
        result["shared_with"] = shared
        notes.append(f"{', '.join(shared)} use the same medium ({ui.pretty_path(current)})")
    return result


# --- setting --------------------------------------------------------------------------------

def tracked_medium(name: str, vm: dict[str, Any]) -> Path:
    """Where the catalog expects the medium (isos/...), whatever local.json says today."""
    tracked = config.load_tracked().get(name)
    return runtime.resolve_path(str((tracked or vm)["iso"]))


def _local_key(document: dict[str, Any], name: str) -> str:
    return next((key for key in document.get("vms", {}) if config.canonical_vm_name(key, warn=False) == name), name)


def _write_iso_override(name: str, value: str | None) -> None:
    """Set (or, with None, drop) ``vms.<name>.iso`` in local.json, validated before writing."""
    document = catalog.read_document()
    vms = document.setdefault("vms", {})
    key = _local_key(document, name)
    entry = dict(vms.get(key) or {})
    if value is None:
        if "iso" not in entry:
            return
        entry.pop("iso")
    else:
        entry["iso"] = value
    if entry:
        vms[key] = entry
    else:
        vms.pop(key, None)
    config.load_config(local_profiles=document)  # every profile must still resolve with it
    catalog.write_document(document)


def _copy_with_progress(source: Path, target: Path) -> None:
    partial = target.with_name(target.name + ".part")
    total = source.stat().st_size
    done = 0
    step = max(total // 20, 1)
    shown = 0
    try:
        with source.open("rb") as src, partial.open("wb") as dst:
            for chunk in iter(lambda: src.read(8 * 1024 * 1024), b""):
                dst.write(chunk)
                done += len(chunk)
                if done - shown >= step:
                    shown = done
                    ui.print_note(f"copied {done * 100 // max(total, 1)}% of {total / 1e9:.1f} GB")
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def set_iso(vm_name: str, path: str | Path, move: bool = False, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    cfg = config.load_config()
    name = config.canonical_vm_name(vm_name, warn=False)
    vm = config.get_vm(cfg, name)
    result = check(name, path, cfg)
    source = Path(result["path"]).resolve() if Path(result["path"]).exists() else Path(result["path"])
    ui.print_header(f"ISO for {name}")
    ui.print_kv("file", str(source))
    if result.get("editions"):
        ui.print_kv("editions", ", ".join(result["editions"]))
    for note in result["notes"]:
        ui.print_note(note)
    if result["problems"]:
        message = "; ".join(result["problems"])
        if not force or not source.is_file():
            raise VMError(f"{source.name} cannot be used for {name}: {message}" + ("" if not source.is_file() else " (--force to use it anyway)"))
        ui.print_status("warn", f"Used anyway (--force): {message}", ok=False)
    if move:
        target = tracked_medium(name, vm)
        result["mode"] = "move"
        result["target"] = str(target)
        ui.print_kv("move to", ui.pretty_path(target))
        if target.exists() and target.resolve() != source:
            raise VMError(f"{ui.pretty_path(target)} already exists: vmctl delete-iso {name} first, or use the file where it is (without --move)")
        if dry_run:
            return result
        if target.resolve() != source:
            runtime.ensure_parent(target)
            try:
                os.rename(source, target)
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise VMError(f"Cannot move {source} to {target}: {exc.strerror or exc}") from exc
                ui.print_note("Another file system: copying, then removing the original")
                _copy_with_progress(source, target)
                source.unlink()
        _write_iso_override(name, None)
        ui.print_status("ok", f"{name} uses {ui.pretty_path(target)}")
    else:
        result["mode"] = "link"
        result["target"] = str(source)
        ui.print_kv("local.json", f'vms.{name}.iso = "{source}"')
        if dry_run:
            return result
        if source == tracked_medium(name, vm).resolve():
            _write_iso_override(name, None)  # already where the catalog looks: nothing to override
        else:
            _write_iso_override(name, str(source))
        ui.print_status("ok", f"{name} uses {source} where it is (vms/profiles/local.json)")
    return result


def in_cache(path: Path) -> bool:
    """Whether *path* is a file vmctl owns: under the checkout's isos/. ``delete-iso`` removes
    only those; a medium set with ``vmctl iso set`` stays the user's file."""
    try:
        return path.resolve().is_relative_to((state.ROOT / "isos").resolve())
    except OSError:
        return False


