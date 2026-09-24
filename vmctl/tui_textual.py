"""Textual dashboard using shared, guarded VM workflows."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Key, Resize
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Static

from vmctl.tui_bridge import ClassicBridge, Facts, primary_action, quick_actions, resources, status_label, visible_rows


class ProfileTable(DataTable[Text | str]):
    BINDINGS = [Binding("enter", "select_cursor", "Default action"),
                Binding("home", "first_profile", "First profile", show=False),
                Binding("end", "last_profile", "Last profile", show=False),
                Binding("right", "app.details", "Actions", show=False, priority=True)]

    def action_first_profile(self) -> None:
        if self.row_count:
            self.move_cursor(row=0, animate=False)

    def action_last_profile(self) -> None:
        if self.row_count:
            self.move_cursor(row=self.row_count - 1, animate=False)


class VMDetails(Vertical):
    BINDINGS = [Binding("up", "previous_action", show=False),
                Binding("down", "next_action", show=False),
                Binding("left", "profiles", "Profiles")]

    class Requested(Message):
        def __init__(self, name: str, action: str) -> None:
            super().__init__()
            self.name = name
            self.action = action

    def __init__(self, row: Facts | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.row = row
        self.border_title = "PROFILE"

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="detail-copy"):
            yield Static(id="vm-name", markup=False)
            yield Static(id="vm-description", markup=False)
            yield Static(id="vm-status")
            with Horizontal(id="vm-resources"):
                yield Static(id="vm-memory", classes="resource-card")
                yield Static(id="vm-cpus", classes="resource-card")
            yield Static(id="vm-facts", markup=False)
            yield Static(id="vm-install", markup=False)
        yield Static("↑/↓ Choose · ← Profiles", classes="action-hint", markup=False)
        yield Button("Boot ISO…", id="primary", disabled=True,
                     tooltip="Default action when the profile list has focus")
        yield Button("", id="quick-1", disabled=True)
        yield Button("", id="quick-2", disabled=True)
        yield Button("All actions…", id="menu", disabled=True)

    def on_mount(self) -> None:
        self.show_row(self.row)

    def show_row(self, row: Facts | None) -> None:
        self.row = row
        self.query_one("#vm-name", Static).update(row["name"] if row else "No profile selected")
        self.query_one("#vm-description", Static).update(row["label"] if row else "Change the search or filter to see profiles.")
        for button in self.query(Button):
            button.disabled = row is None
        self.query_one("#vm-resources").display = row is not None
        self.query_one("#vm-install").display = bool(row and row["installed"] and row["install_detail"])
        hint = self.query_one(".action-hint", Static)
        hint.display = row is not None
        if row is None:
            for selector in ("#vm-status", "#vm-facts", "#vm-install"):
                self.query_one(selector, Static).update("")
            return
        label, color = status_label(row)
        status = self.query_one("#vm-status", Static)
        status.update(Text(f"●  {label}", style=f"bold {color}"))
        status.styles.border_left = ("thick", color)
        memory = resources(row).split(" / ")[0]
        self.query_one("#vm-memory", Static).update(Text.assemble(
            ("RAM\n", "#a6b4c8"), (memory, "bold #dce5ef")))
        self.query_one("#vm-cpus", Static).update(Text.assemble(
            ("CPU\n", "#a6b4c8"), (f"{row['cpus']} vCPU", "bold #dce5ef")))
        firmware = "UEFI" if row["firmware"] == "EFI" else row["firmware"]
        ssh = f"localhost:{row['ssh_port']}" if row["ssh_port"] else "Not configured"
        facts = Text()
        facts.append("STORAGE\n", style="bold #84c9e7")
        if row["prepared"]:
            facts.append("Capacity   ", style="#a6b4c8")
            facts.append(f"{row['disk_capacity'] or '?'}\n")
            facts.append("On host    ", style="#a6b4c8")
            facts.append(row["disk_host"] or "?")
        else:
            facts.append("No disk created", style="#a6b4c8")
        facts.append("\n\nBOOT\n", style="bold #84c9e7")
        facts.append(f"{firmware} · ")
        facts.append("ISO available" if row["iso_ready"] else "ISO not cached",
                     style="#86d5ab" if row["iso_ready"] else "#a6b4c8")
        facts.append("\n\nSSH\n", style="bold #84c9e7")
        facts.append(ssh)
        self.query_one("#vm-facts", Static).update(facts)
        self.query_one("#vm-install", Static).update(row["install_detail"] if row["installed"] else "")
        actions = quick_actions(row)[:-1]
        hint.update(Text.assemble(
            (f"Enter → {actions[0][0]}", "bold #84c9e7"),
            "\n↑/↓ Choose · ← Profiles",
        ))
        for index, selector in enumerate(("#primary", "#quick-1", "#quick-2")):
            button = self.query_one(selector, Button)
            button.display = index < len(actions)
            if button.display:
                button.label = actions[index][0]
        if isinstance(self.app.focused, Button) and not self.app.focused.display:
            self.query_one("#primary", Button).focus()

    def move_action(self, offset: int) -> None:
        buttons = [button for button in self.query(Button) if button.display and not button.disabled]
        if buttons:
            focused = self.app.focused
            index = buttons.index(focused) if isinstance(focused, Button) and focused in buttons else 0
            buttons[(index + offset) % len(buttons)].focus()

    def action_previous_action(self) -> None:
        self.move_action(-1)

    def action_next_action(self) -> None:
        self.move_action(1)

    def action_profiles(self) -> None:
        if isinstance(self.app.screen, DetailsScreen):
            self.app.pop_screen()
        self.app.query_one(ProfileTable).focus()

    @on(Button.Pressed)
    def request_action(self, event: Button.Pressed) -> None:
        event.stop()
        if self.row is not None:
            index = {"primary": 0, "quick-1": 1, "quick-2": 2, "menu": -1}[event.button.id or "menu"]
            action = quick_actions(self.row)[index][1]
            self.post_message(self.Requested(self.row["name"], action))


class DetailsScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Back")]

    def __init__(self, row: Facts) -> None:
        super().__init__()
        self.row = row

    def compose(self) -> ComposeResult:
        with Vertical(id="detail-dialog"):
            yield VMDetails(self.row)
            yield Button("Back · Esc", id="close-details")

    def on_mount(self) -> None:
        self.query_one("#primary", Button).focus()

    @on(Button.Pressed, "#close-details")
    def close_details(self) -> None:
        self.dismiss()


class HelpScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Back"), ("f1", "dismiss", "Back")]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Static("Dashboard", classes="dialog-title")
            yield Static(
                "↑ / ↓      Select a profile\n"
                "Home / End First / last profile\n"
                "Enter      Run the default profile action\n"
                "→          Move to profile actions\n"
                "↑ / ↓      Move between actions\n"
                "←          Return to the profile list\n"
                "/ or F3    Search name, description or family\n"
                "↓          Move from search to results\n"
                "Tab        Move between controls\n"
                "F5         Refresh (also automatic every 15s)\n"
                "F8         Open the classic TUI\n"
                "Esc        Clear search / return to list / quit\n\n"
                "Boot verified: a disk boot or post-install check passed.\n"
                "Installed: installation completed; boot not verified.\n"
                "Unverified: data exists without a matching install record.\n"
                "Incomplete: an installation did not finish.\n\n"
                "ISO availability is separate from installation state.\n"
                "Disk sizes describe host allocation and virtual capacity.\n\n"
                "Menus and confirmations use this same frontend.\n"
                "Close them to return here with your search preserved.",
                markup=False,
            )
            yield Button("Back · Esc", id="close-help")

    @on(Button.Pressed, "#close-help")
    def close_help(self) -> None:
        self.dismiss()


class Dashboard(App[None]):
    TITLE = "QEMU ISO Lab"
    CSS_PATH = "tui_textual.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("slash", "search", "Search"),
        Binding("f3", "search", "Search", show=False),
        Binding("enter", "primary", "Default action"),
        Binding("f5", "refresh", "Refresh"),
        Binding("f8", "classic", "Classic UI"),
        Binding("f1", "help", "Help"),
        Binding("escape", "escape", "Back / Quit", priority=True),
    ]

    def __init__(self, bridge: ClassicBridge | None = None, *, auto_refresh: bool = True) -> None:
        super().__init__()
        self.bridge = bridge or ClassicBridge()
        self.poll_enabled = auto_refresh
        self.rows: list[Facts] = []
        self.filtered: list[Facts] = []
        self.mode = "all"
        self.selected: str | None = None
        self.refreshing = False
        self.interactive = False
        self.loaded = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield Static("QEMU [bold]ISO Lab[/bold]", id="brand")
            yield Static("Loading profiles…", id="summary", markup=False)
        with Horizontal(id="toolbar"):
            yield Input(placeholder="Search profiles, distributions…  / or F3", id="search")
            with Horizontal(id="filters"):
                yield Button("All", id="all", classes="filter active")
                yield Button("With disk", id="disk", classes="filter")
                yield Button("Running", id="running", classes="filter")
        yield Static("Reading VM state…", id="notice", markup=False)
        with Horizontal(id="workspace"):
            with Vertical(id="catalog"):
                yield Static("PROFILES", id="list-title", classes="eyebrow")
                yield ProfileTable(id="profiles", cursor_type="row", zebra_stripes=True)
                yield Static("No matching profiles. Try another search or filter.", id="empty", markup=False)
            yield VMDetails(id="details")
        with Vertical(id="activity"):
            yield Static("ACTIVITY", classes="eyebrow")
            yield Static("Reading installation status…", id="activity-text", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).focus()
        self.set_class(self.size.width < 100, "compact")
        self.action_refresh()
        if self.poll_enabled:
            self.set_interval(15, lambda: self.action_refresh(quiet=True))

    def on_resize(self, event: Resize) -> None:
        self.set_class(event.size.width < 100, "compact")
        if self.loaded:
            self.rebuild_table()

    def action_refresh(self, *, quiet: bool = False) -> None:
        if not self.refreshing and not self.interactive:
            self.refreshing = True
            if not quiet:
                self.query_one("#notice", Static).update("Refreshing VM state…")
            self.read_snapshot()

    @work
    async def read_snapshot(self) -> None:
        try:
            rows = await asyncio.to_thread(self.bridge.snapshot)
        except Exception as exc:
            self.query_one("#notice", Static).update(f"Refresh failed — {exc}. F5 to retry.")
            self.query_one("#notice").add_class("error")
            if not self.loaded:
                self.query_one("#summary", Static).update("VM state unavailable")
                self.query_one("#activity-text", Static).update("Unable to read installation status.")
        else:
            self.rows = rows
            self.loaded = True
            self.query_one("#notice").remove_class("error")
            self.query_one("#notice", Static).update(f"Updated {datetime.now():%H:%M:%S}  ·  Select a profile to explore")
            self.query_one("#summary", Static).update(
                f"{len(rows)} profiles  ·  {sum(bool(r['installed']) for r in rows)} with data"
                f"  ·  {sum(bool(r['running']) for r in rows)} running"
            )
            jobs = [f"{r['name']}  ·  {r['job_status']}" for r in rows if r["job_status"]]
            jobs.sort(key=lambda line: "· running" not in line)
            self.query_one("#activity-text", Static).update("\n".join(jobs) or "No installation jobs. Select a profile to get started.")
            self.rebuild_table()
            if isinstance(self.screen, DetailsScreen):
                opened = self.screen.row["name"]
                details = self.screen.query_one(VMDetails)
                row = next((r for r in rows if r["name"] == opened), None)
                if details.row != row:
                    details.show_row(row)
        finally:
            self.refreshing = False

    def rebuild_table(self) -> None:
        query = self.query_one("#search", Input).value
        self.filtered = visible_rows(self.rows, query, self.mode)
        names = [row["name"] for row in self.filtered]
        selected = self.selected if self.selected in names else (names[0] if names else None)
        table = self.query_one(DataTable)
        available = self.size.width - (6 if self.has_class("compact") else 46)
        widths = [max(12, min(36, available - 32)), 16, 9]
        same_layout = (
            [row.key.value for row in table.ordered_rows] == names
            and [column.width for column in table.columns.values()] == widths
        )
        with self.batch_update(), table.prevent(DataTable.RowHighlighted):
            if not same_layout:
                table.clear(columns=True)
                for title, width in zip(("PROFILE", "STATE", "RAM / CPU"), widths):
                    table.add_column(title, width=width)
            for row in self.filtered:
                label, tone = status_label(row)
                cells = (Text(row["name"]), Text(label, style=tone), resources(row))
                if same_layout:
                    for column, value in zip(table.columns, cells):
                        if table.get_cell(row["name"], column) != value:
                            table.update_cell(row["name"], column, value)
                else:
                    table.add_row(*cells, key=row["name"])
            if selected and not same_layout:
                table.move_cursor(row=names.index(selected), animate=False)
                self.call_after_refresh(table.move_cursor, row=names.index(selected), animate=False)
        self.selected = selected
        self.query_one("#empty").display = not self.filtered
        self.query_one("#list-title", Static).update(f"PROFILES  {len(self.filtered)} / {len(self.rows)}")
        self.update_details()

    def selected_row(self) -> Facts | None:
        return next((row for row in self.rows if row["name"] == self.selected), None)

    def update_details(self) -> None:
        details = self.query_one("#details", VMDetails)
        row = self.selected_row()
        if details.row != row:
            details.show_row(row)

    @on(DataTable.RowHighlighted)
    def highlight_row(self, event: DataTable.RowHighlighted) -> None:
        self.selected = str(event.row_key.value)
        self.update_details()

    @on(DataTable.RowSelected)
    def open_selected(self) -> None:
        self.action_primary()

    @on(Input.Changed, "#search")
    def search_changed(self) -> None:
        if self.loaded:
            self.rebuild_table()

    @on(Input.Submitted, "#search")
    def search_submitted(self) -> None:
        self.query_one(DataTable).focus()

    def on_key(self, event: Key) -> None:
        if event.key == "down" and isinstance(self.focused, Input) and self.focused.id == "search":
            event.stop()
            self.search_submitted()

    @on(Button.Pressed, ".filter")
    def filter_changed(self, event: Button.Pressed) -> None:
        self.mode = event.button.id or "all"
        for button in self.query(".filter"):
            button.set_class(button is event.button, "active")
        self.rebuild_table()
        self.query_one(DataTable).focus()

    def action_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_primary(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        row = self.selected_row()
        if row is not None:
            self.run_classic(row["name"], primary_action(row)[1])

    def action_details(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        row = self.selected_row()
        if row is not None:
            if self.has_class("compact"):
                self.push_screen(DetailsScreen(row))
            else:
                self.query_one("#primary", Button).focus()

    def action_help(self) -> None:
        if not isinstance(self.screen, ModalScreen):
            self.push_screen(HelpScreen())

    def action_escape(self) -> None:
        if isinstance(self.screen, ModalScreen):
            self.pop_screen()
        elif isinstance(self.focused, Button) and self.focused.parent is self.query_one("#details"):
            self.query_one(DataTable).focus()
        elif self.query_one("#search", Input).value:
            self.query_one("#search", Input).value = ""
            self.query_one(DataTable).focus()
        elif self.focused is not self.query_one(DataTable):
            self.query_one(DataTable).focus()
        else:
            self.exit()

    def action_classic(self) -> None:
        self.run_classic("", "classic")

    @on(VMDetails.Requested)
    def run_requested(self, event: VMDetails.Requested) -> None:
        if isinstance(self.screen, DetailsScreen):
            self.pop_screen()
        self.run_classic(event.name, event.action)

    def run_classic(self, name: str, action: str) -> None:
        self.interactive = True
        try:
            with self.suspend():
                code = self.bridge.run(name, action)
            if code:
                self.notify(f"Action exited with status {code}", severity="warning")
        except Exception as exc:
            self.notify(str(exc), title="Unable to open action", severity="error")
        finally:
            self.interactive = False
            self.action_refresh()
