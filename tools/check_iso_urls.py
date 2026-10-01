#!/usr/bin/env python3
"""Are the catalog's ISO download links still alive? Nothing is downloaded.

    tools/check_iso_urls.py                # every tracked profile with a download source
    tools/check_iso_urls.py kali-live ...  # only these
    tools/check_iso_urls.py --json         # machine-readable
    tools/check_iso_urls.py --summary FILE # also a Markdown table (the CI writes $GITHUB_STEP_SUMMARY)

Vendors move and rename their media: on 2026-10-01 two rows of the matrix failed because Kali 2026.1
had left cdimage.kali.org (2026.2 is a torrent only) and KDE had renamed its neon "user" images, and
CentOS Stream composes age out of their directory. The weekly CI job (.github/workflows/iso-links.yml)
runs this, so a dead link shows up before a night matrix finds it.

Each URL gets one GET with "Range: bytes=0-0" (not every server answers HEAD), redirects followed.
A source is bad when it answers an error, an HTML page instead of a medium, a file under MIN_BYTES
(the Kali mirror handed out the .torrent under the ISO's name) or a size other than the profile's
iso_size. A profile is OK when at least one of its sources is good (discovery included), DEGRADED
when only an alternate is, BROKEN when none is; the exit status is 1 when any profile is BROKEN.
User-supplied media (iso_help, no URL) are listed as such and never fail.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vmctl import config, iso  # noqa: E402

MIN_BYTES = 8 * 1024 * 1024
TIMEOUT_SEC = 40
USER_AGENT = "qemu-iso-lab-link-check/1 (+https://github.com/manzolo/qemu-iso-lab)"


def classify(status: int | None, content_type: str, size: int | None, expected_size: int | None,
             error: str | None = None) -> tuple[bool, str]:
    """(good, reason) for one probed URL: pure, so it is tested without a network."""
    if error:
        return False, error
    if status is None or status >= 400:
        return False, f"HTTP {status}"
    if "html" in content_type.lower():
        return False, f"an HTML page ({content_type}), not a medium"
    if size is not None and size < MIN_BYTES:
        return False, f"only {size} bytes (a torrent or an error file under the medium's name?)"
    if expected_size is not None and size is not None and size != expected_size:
        return False, f"{size} bytes, the profile pins iso_size {expected_size}"
    return True, f"{size // 1048576} MiB" if size else "ok (size unknown)"


def probe(url: str) -> tuple[int | None, str, int | None, str | None]:
    request = urllib.request.Request(url, headers={"Range": "bytes=0-0", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
            status = response.status
            content_type = response.headers.get("Content-Type", "")
            total = response.headers.get("Content-Range", "")
            match = re.search(r"/(\d+)$", total)
            if match:
                size: int | None = int(match.group(1))
            else:
                length = response.headers.get("Content-Length")
                size = int(length) if length and length.isdigit() and status == 200 else None
            return status, content_type, size, None
    except urllib.error.HTTPError as exc:
        return exc.code, "", None, None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, "", None, f"unreachable: {getattr(exc, 'reason', exc)}"


def profile_sources(vm: dict[str, Any]) -> list[str]:
    # iso.py reports a failed discovery on stdout: it would corrupt --json, so it goes to stderr
    with contextlib.redirect_stdout(sys.stderr):
        try:
            return iso.iso_url_candidates(vm, allow_discovery=True)
        except Exception:  # a broken discovery index must not hide the static sources
            return iso.iso_url_candidates(vm, allow_discovery=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vms", nargs="*", help="profiles to check (default: every tracked one)")
    parser.add_argument("--json", action="store_true", help="print the results as JSON")
    parser.add_argument("--summary", type=Path, metavar="FILE", help="append a Markdown table to FILE")
    args = parser.parse_args(argv)
    catalog = config.load_tracked()
    names = args.vms or sorted(catalog)
    plan: dict[str, list[str]] = {}
    manual: list[str] = []
    for name in names:
        vm = catalog[name]
        if not vm.get("iso") and not vm.get("disk_image"):
            continue
        sources = profile_sources(vm)
        if sources:
            plan[name] = sources
        elif vm.get("iso_help") or vm.get("disk_image"):
            manual.append(name)
    urls = sorted({url for sources in plan.values() for url in sources})
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        probed = dict(zip(urls, pool.map(probe, urls)))
    rows: list[dict[str, Any]] = []
    for name, sources in plan.items():
        expected = catalog[name].get("iso_size")
        checks = []
        for url in sources:
            status, content_type, size, error = probed[url]
            good, reason = classify(status, content_type, size, int(expected) if expected else None, error)
            checks.append({"url": url, "good": good, "reason": reason})
        if checks[0]["good"]:
            verdict = "OK"
        elif any(c["good"] for c in checks):
            verdict = "DEGRADED"
        else:
            verdict = "BROKEN"
        rows.append({"vm": name, "verdict": verdict, "sources": checks})
    if args.json:
        print(json.dumps({"profiles": rows, "user_supplied": manual}, indent=2))
    else:
        for row in rows:
            first_bad = next((c for c in row["sources"] if not c["good"]), None)
            note = "" if row["verdict"] == "OK" else f"  {first_bad['url']}: {first_bad['reason']}" if first_bad else ""
            print(f"{row['verdict']:<9} {row['vm']:<34}{note}")
        if manual:
            print(f"user-supplied (iso_help, not checked): {', '.join(manual)}")
    broken = [r for r in rows if r["verdict"] == "BROKEN"]
    degraded = [r for r in rows if r["verdict"] == "DEGRADED"]
    print(f"{len(rows)} profiles, {len(urls)} URLs: {len(broken)} broken, {len(degraded)} degraded", file=sys.stderr)
    if args.summary is not None:
        lines = ["## ISO download links", "", f"{len(rows)} profiles, {len(urls)} URLs: "
                 f"**{len(broken)} broken**, {len(degraded)} degraded.", ""]
        if broken or degraded:
            lines += ["| Profile | Verdict | URL | Why |", "|---|---|---|---|"]
            for row in broken + degraded:
                for check in row["sources"]:
                    if not check["good"]:
                        lines.append(f"| `{row['vm']}` | {row['verdict']} | {check['url']} | {check['reason']} |")
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
