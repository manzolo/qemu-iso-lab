"""ISO download, discovery, validation, and extraction."""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

from typing import Any

from vmctl import state, ui, runtime
from vmctl.errors import VMError


def looks_like_html(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            prefix = fh.read(512).lstrip().lower()
    except OSError:
        return False
    return prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_iso_file(path: Path, vm: dict[str, Any] | None = None) -> list[str]:
    problems: list[str] = []
    if not path.is_file():
        return [f"not a regular file: {path}"]
    try:
        size = path.stat().st_size
    except OSError as exc:
        return [f"cannot stat file: {exc}"]
    if size == 0:
        problems.append("file is empty")
    if looks_like_html(path):
        problems.append("file looks like HTML, not an ISO")

    vm = vm or {}
    expected_size = vm.get("iso_size")
    if expected_size is not None:
        try:
            expected_size_int = int(expected_size)
        except (TypeError, ValueError):
            problems.append(f"invalid iso_size value: {expected_size}")
        else:
            if size != expected_size_int:
                problems.append(f"size is {size} bytes, expected {expected_size_int}")

    expected_sha256 = vm.get("iso_sha256")
    if expected_sha256:
        actual_sha256 = sha256_file(path)
        if actual_sha256.lower() != str(expected_sha256).lower():
            problems.append(f"sha256 is {actual_sha256}, expected {expected_sha256}")

    return problems


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": state.HTTP_USER_AGENT})
    with urllib.request.urlopen(request) as response:
        content_type = response.headers.get("Content-Type", "")
        if "text/html" not in content_type.lower() and "text/plain" not in content_type.lower():
            ui.print_status("warn", f"Unexpected discovery content type from {ui.pretty_url(url)}: {content_type}", ok=False)
        result: str = response.read().decode("utf-8", errors="replace")
        return result


def discover_iso_urls(vm: dict[str, Any]) -> list[str]:
    discovery = vm.get("iso_discovery")
    if not discovery:
        return []
    if not isinstance(discovery, dict):
        raise VMError("iso_discovery must be an object")

    index_url = discovery.get("index_url")
    pattern = discovery.get("pattern")
    if not index_url or not pattern:
        raise VMError("iso_discovery requires index_url and pattern")

    text = fetch_text(str(index_url))
    regex = re.compile(str(pattern))
    urls: list[str] = []
    for match in regex.finditer(text):
        href = match.groupdict().get("url") if match.groupdict() else None
        if href is None:
            href = match.group(1) if match.groups() else match.group(0)
        if not href:
            continue
        base_url = str(discovery.get("base_url") or index_url)
        candidate = urljoin(base_url, href)
        if candidate not in urls:
            urls.append(candidate)

    if discovery.get("sort", "desc") == "desc":
        urls.sort(reverse=True)
    elif discovery.get("sort") == "asc":
        urls.sort()
    limit = discovery.get("limit")
    if limit is not None:
        try:
            urls = urls[: int(limit)]
        except (TypeError, ValueError) as exc:
            raise VMError(f"Invalid iso_discovery limit: {limit}") from exc
    return urls


def iso_url_candidates(vm: dict[str, Any], allow_discovery: bool = True) -> list[str]:
    candidates: list[str] = []
    if allow_discovery:
        try:
            candidates.extend(discover_iso_urls(vm))
        except (OSError, VMError, re.error, urllib.error.URLError) as exc:
            ui.print_status("warn", f"ISO discovery failed, falling back to static sources: {exc}", ok=False)

    extra_urls = vm.get("iso_urls", [])
    if isinstance(extra_urls, str):
        extra_urls = [extra_urls]
    if not isinstance(extra_urls, list):
        raise VMError("iso_urls must be a list of URLs")
    candidates.extend(str(url) for url in extra_urls if url)

    iso_url = vm.get("iso_url")
    if iso_url:
        candidates.append(str(iso_url))

    deduped: list[str] = []
    for url in candidates:
        if url not in deduped:
            deduped.append(url)
    return deduped


def download_file(url: str, destination: Path, dry_run: bool = False, vm: dict[str, Any] | None = None) -> None:
    ui.print_header("Download ISO")
    ui.print_kv("source", ui.pretty_url(url))
    ui.print_kv("target", ui.pretty_path(destination))
    runtime.ensure_parent(destination)
    if dry_run:
        return

    partial = destination.with_name(destination.name + ".part")
    if partial.exists():
        partial.unlink()

    try:
        request = urllib.request.Request(url, headers={"User-Agent": state.HTTP_USER_AGENT})
        with urllib.request.urlopen(request) as response, partial.open("wb") as fh:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" in content_type.lower():
                raise VMError(f"Refusing HTML response for ISO download: {url}")
            expected_length = response.headers.get("Content-Length")
            shutil.copyfileobj(response, fh)
        if expected_length:
            actual_length = partial.stat().st_size
            try:
                expected_length_int = int(expected_length)
            except ValueError as exc:
                raise VMError(f"Invalid Content-Length for ISO download from '{url}': {expected_length}") from exc
            if actual_length != expected_length_int:
                raise VMError(f"Incomplete ISO download from '{url}': got {actual_length} bytes, expected {expected_length}")
    except VMError:
        if partial.exists():
            partial.unlink()
        raise
    except (OSError, urllib.error.URLError) as exc:
        if partial.exists():
            partial.unlink()
        raise VMError(f"Failed to download '{url}': {exc}") from exc

    problems = validate_iso_file(partial, vm)
    if problems:
        partial.unlink()
        raise VMError(f"Invalid ISO downloaded from '{url}': {'; '.join(problems)}")

    partial.replace(destination)


def ensure_iso(vm: dict[str, Any], dry_run: bool = False) -> Path:
    iso_path = runtime.resolve_path(vm["iso"])
    if iso_path.is_file():
        problems = validate_iso_file(iso_path, vm)
        if problems:
            ui.print_status("warn", f"Removing invalid cached ISO: {ui.pretty_path(iso_path)} ({'; '.join(problems)})", ok=False)
            if not dry_run:
                iso_path.unlink()
            else:
                return iso_path
        else:
            ui.print_status("ok", f"ISO ready: {ui.pretty_path(iso_path)}")
            return iso_path
    if iso_path.exists():
        raise VMError(f"ISO path exists but is not a regular file: {iso_path}")

    candidates = iso_url_candidates(vm, allow_discovery=not dry_run)
    if not candidates:
        raise VMError(f"ISO not found and no ISO download source configured: {iso_path}")

    failures: list[str] = []
    for url in candidates:
        try:
            download_file(url, iso_path, dry_run=dry_run, vm=vm)
            return iso_path
        except VMError as exc:
            failures.append(f"{ui.pretty_url(url)}: {exc}")
            ui.print_status("warn", f"ISO source failed: {ui.pretty_url(url)}", ok=False)
            if dry_run:
                break
    if failures:
        raise VMError("Unable to fetch ISO from configured sources:\n- " + "\n- ".join(failures))
    return iso_path


def installer_artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "installer"


def extract_iso_member(iso_path: Path, member_path: str, dest_path: Path, dry_run: bool = False) -> None:
    runtime.ensure_parent(dest_path)
    if dest_path.exists() and not dry_run:
        return

    if shutil.which("xorriso"):
        runtime.run(
            ["xorriso", "-osirrox", "on", "-indev", str(iso_path), "-extract", f"/{member_path}", str(dest_path)],
            dry_run=dry_run,
        )
        return

    if shutil.which("bsdtar"):
        cmd = ["bsdtar", "-xOf", str(iso_path), member_path]
        ui.print_command(cmd)
        if dry_run:
            return
        result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE)
        dest_path.write_bytes(result.stdout)
        return

    raise VMError("Missing ISO extraction tool: install xorriso or bsdtar")


def extract_installer_boot_artifacts(vm: dict[str, Any], iso_path: Path, dry_run: bool = False) -> tuple[Path, Path]:
    artifact_dir = installer_artifact_dir(vm)
    kernel_path = artifact_dir / "vmlinuz"
    initrd_path = artifact_dir / "initrd"
    extract_iso_member(iso_path, "casper/vmlinuz", kernel_path, dry_run=dry_run)
    extract_iso_member(iso_path, "casper/initrd", initrd_path, dry_run=dry_run)
    return kernel_path, initrd_path
