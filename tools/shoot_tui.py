#!/usr/bin/env python3
"""Screenshot the Textual dashboard (vmtui) as SVG, headless, from the lab's live state.

    .venv-tui/bin/python tools/shoot_tui.py --out docs/screenshots/vmtui-dashboard.svg
    .venv-tui/bin/python tools/shoot_tui.py --out labs.svg --filter labs --size 132x42
    .venv-tui/bin/python tools/shoot_tui.py --out details.svg --select debian-server --enter

Runs the real dashboard against the real snapshot (no VM is started or touched), waits for the
first refresh, applies the filter / selection asked for and saves Textual's SVG rendering. Turn
the SVG into a PNG with any browser (a headless Chromium screenshot keeps the fonts crisp).
Needs Textual (.venv-tui after `make install textual`).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def shoot(out: Path, size: tuple[int, int], filter_id: str, select: str | None, enter: bool, search: str) -> None:
    from textual.widgets import Input

    from vmctl.tui_textual import Dashboard

    app = Dashboard(auto_refresh=False)
    async with app.run_test(size=size) as pilot:
        for _ in range(600):
            if app.loaded and not app.refreshing:
                break
            await asyncio.sleep(0.05)
        if not app.loaded:
            raise SystemExit("the dashboard did not load its snapshot")
        if search:
            app.query_one("#search", Input).value = search
            await pilot.pause()
        if filter_id and filter_id != app.mode:
            app.set_mode(filter_id)
            await pilot.pause()
        if select:
            app.selected = select
            app.rebuild_table()
            await pilot.pause()
        if enter:
            await pilot.press("enter")
            await pilot.pause()
        await pilot.pause(0.3)
        app.save_screenshot(filename=out.name, path=str(out.parent))
    print(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True, help="the SVG to write")
    parser.add_argument("--size", default="120x40", help="columns x rows (default 120x40)")
    parser.add_argument("--filter", default="", choices=["", "mine", "all", "disk", "running", "labs"], help="filter button to activate")
    parser.add_argument("--select", help="profile to select in the table")
    parser.add_argument("--search", default="", help="text in the search field")
    parser.add_argument("--enter", action="store_true", help="press Enter on the selection (opens details on narrow terminals)")
    args = parser.parse_args(argv)
    cols, rows = (int(part) for part in args.size.lower().split("x"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(shoot(args.out, (cols, rows), args.filter, args.select, args.enter, args.search))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
