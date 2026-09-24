"""Textual menu/form adapter for the shared vmtui shell workflows.

Only the selected value goes to stdout (often a shell command substitution).
Textual renders to stderr; VM execution and terminal consoles stay in vmtui.
"""
from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
from time import monotonic
from typing import Any, Mapping

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.widgets import Button, Footer, Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option


def plain(value: str) -> str:
    return re.sub(r"\\Z[0-9bn]", "", Text.from_ansi(value.replace(r"\n", "\n")).plain)


def copy_native(text: str) -> bool:
    """Use the desktop clipboard when available; callers also send OSC 52."""
    candidates = []
    if os.environ.get("WAYLAND_DISPLAY"):
        candidates.append(["wl-copy", "--type", "text/plain"])
    if os.environ.get("DISPLAY"):
        candidates.extend([["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]])
    for command in candidates:
        if not shutil.which(command[0]):
            continue
        try:
            result = subprocess.run(command, input=text, text=True, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=3, check=False)
            if result.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            continue
    return False


class CommandWidget(App[int]):
    """Keep desktop/viewer output in the dashboard while vmctl owns the session."""

    CSS_PATH = "tui_textual.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("escape", "back", "Back", priority=True),
        Binding("enter", "back", "Back", show=False),
        Binding("ctrl+q", "back", show=False, priority=True),
        Binding("ctrl+c", "copy_selection", "Copy selection", priority=True),
        Binding("f2", "copy_command", "Copy command"),
        Binding("f3", "copy_qemu", "Copy QEMU"),
        Binding("f6", "interrupt", "Interrupt session", priority=True),
        Binding("left", "previous_button", show=False),
        Binding("right", "next_button", show=False),
    ]

    def __init__(self, name: str, command: list[str], *, desktop: bool = False) -> None:
        super().__init__()
        self.vm_name = name
        self.command = [*command, "--headless", "--background"] if desktop else command
        self.followup = [command[0], "attach", name, "--wait", "10"] if desktop else None
        self.qemu_command = ""
        self.detached_started = False
        self.process: asyncio.subprocess.Process | None = None
        self.code: int | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield Static("QEMU [bold]ISO Lab[/bold]", id="brand")
        with Vertical(id="workflow"):
            title = "Open display" if self.command[1] == "attach" else "Start VM"
            yield Static(f"{title} · {self.vm_name}", classes="dialog-title", markup=False)
            yield Static("Starting…", id="command-status", markup=False)
            yield TextArea(read_only=True, soft_wrap=True, show_line_numbers=False, id="command-output")
            with Horizontal(id="workflow-buttons"):
                yield Button("Back · Esc", id="command-back", disabled=True)
                yield Button("Copy command", id="copy-command")
                yield Button("Copy QEMU", id="copy-qemu", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.run_command()

    @work
    async def run_command(self) -> None:
        started = monotonic()
        try:
            code = await self.execute(self.command)
            if code == 0 and self.followup:
                self.detached_started = True
                code = await self.execute(self.followup)
            self.code = code
        except OSError as exc:
            self.append_output(str(exc))
            self.code = 1
        status = self.query_one("#command-status", Static)
        elapsed = int(monotonic() - started)
        if self.code == 0:
            status.update(f"Session completed successfully ({elapsed}s).")
        else:
            status.update(f"Session ended with exit code {self.code} ({elapsed}s). See output below.")
            status.add_class("error")
        if self.detached_started:
            self.append_output("The VM was started in the background. Closing the viewer does not stop it.\n"
                               "Use Open display to reconnect, or Stop VM to shut it down.")
        button = self.query_one("#command-back", Button)
        button.disabled = False
        button.focus()
        self.refresh_bindings()

    async def execute(self, command: list[str]) -> int:
        self.append_output(f"$ {shlex.join(command)}")
        self.process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"}, start_new_session=True,
        )
        self.query_one("#command-status", Static).update(
            "Viewer session · closing the viewer leaves the VM running."
            if command[1] == "attach" else "Starting VM…"
        )
        self.refresh_bindings()
        assert self.process.stdout is not None
        while line := await self.process.stdout.readline():
            self.append_output(line.decode("utf-8", errors="replace").rstrip("\r\n"))
        return await self.process.wait()

    def append_output(self, text: str) -> None:
        text = Text.from_ansi(text).plain
        output = self.query_one(TextArea)
        output.insert(text + "\n", output.document.end)
        if output.document.line_count > 2200:
            output.delete((0, 0), (output.document.line_count - 2000, 0))
        if not output.has_focus:
            output.scroll_end(animate=False)
        if text.startswith("$ "):
            try:
                executable = shlex.split(text[2:])[0]
            except (ValueError, IndexError):
                return
            if os.path.basename(executable).startswith("qemu-system-"):
                self.qemu_command = text[2:]
                self.query_one("#copy-qemu", Button).disabled = False
                self.refresh_bindings()

    @work
    async def copy_text(self, text: str) -> None:
        self.copy_to_clipboard(text)
        native = await asyncio.to_thread(copy_native, text)
        self.notify("Copied to clipboard." if native else "Copy sent to terminal clipboard (OSC 52).")

    @on(Button.Pressed, "#copy-command")
    def action_copy_command(self) -> None:
        self.copy_text(shlex.join(self.command))

    @on(Button.Pressed, "#copy-qemu")
    def action_copy_qemu(self) -> None:
        if self.qemu_command:
            self.copy_text(self.qemu_command)

    def action_copy_selection(self) -> None:
        selected = self.query_one(TextArea).selected_text or self.screen.get_selected_text()
        if selected:
            self.copy_text(selected)

    def move_button(self, offset: int) -> None:
        buttons = [button for button in self.query(Button) if not button.disabled]
        if isinstance(self.focused, Button) and self.focused in buttons:
            buttons[(buttons.index(self.focused) + offset) % len(buttons)].focus()

    def action_previous_button(self) -> None:
        self.move_button(-1)

    def action_next_button(self) -> None:
        self.move_button(1)

    @on(Button.Pressed, "#command-back")
    async def action_back(self) -> None:
        if self.code is not None:
            self.exit(self.code if self.code >= 0 else 128 - self.code)
        else:
            self.notify("Close the viewer window, or use F6 to interrupt the session.")

    def action_interrupt(self) -> None:
        if self.process is not None and self.process.returncode is None:
            try:
                os.killpg(self.process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass

    def check_action(self, action: str, parameters: tuple[Any, ...]) -> bool | None:
        if action == "interrupt":
            return self.process is not None and self.process.returncode is None
        if action == "copy_qemu":
            return bool(self.qemu_command)
        return True


class WorkflowWidget(App[str | None]):
    CSS_PATH = "tui_textual.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("escape", "cancel", "Back", priority=True),
        Binding("left", "navigate_left", "Left", show=False),
        Binding("right", "navigate_right", "Right", show=False),
        Binding("slash", "search", "Search"),
        Binding("f5", "refresh_menu", "Refresh"),
        Binding("up", "previous_control", show=False),
        Binding("down", "next_control", show=False),
    ]

    def __init__(self, kind: str, title: str, prompt: str, values: list[str],
                 env: Mapping[str, str] | None = None) -> None:
        super().__init__()
        self.kind = kind
        self.title = title
        self.prompt = plain(prompt)
        self.values = values
        self.settings = dict(os.environ if env is None else env)
        self.items = list(zip(values[::2], values[1::2])) if kind == "menu" else []

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield Static("QEMU [bold]ISO Lab[/bold]", id="brand")
        with Vertical(id="workflow"):
            yield Static(self.title, classes="dialog-title", markup=False)
            with VerticalScroll(id="workflow-copy"):
                yield Static(self.prompt, markup=False)
            if self.kind == "menu":
                yield Input(placeholder="Filter actions…  /", id="workflow-search")
                yield OptionList(id="workflow-options", markup=False)
                yield Static("", id="workflow-device", markup=False)
            elif self.kind == "input":
                yield Input(value=self.values[0] if self.values else "", id="workflow-input")
            with Horizontal(id="workflow-buttons"):
                yield Button("Cancel" if self.kind in {"confirm", "input"} else "Back",
                             id="workflow-cancel")
                if self.kind != "menu":
                    destructive = self.settings.get("CONFIRM_DESTRUCTIVE") == "1"
                    label = "Erase disk" if self.kind == "confirm" and destructive else "Continue"
                    yield Button(label, id="workflow-accept",
                                 variant="error" if destructive else "primary")
        yield Footer()

    def on_mount(self) -> None:
        self.set_class(self.kind == "menu", "workflow-menu")
        if self.kind == "menu":
            self.filter_options()
            self.query_one(OptionList).focus()
        elif self.kind == "input":
            self.query_one(Input).focus()
        else:
            # All confirmations default to cancel, including destructive ones.
            self.query_one("#workflow-cancel" if self.kind == "confirm"
                           else "#workflow-accept", Button).focus()

    def on_resize(self, event: Resize) -> None:
        self.set_class(event.size.height < 32, "workflow-compact")

    @on(Input.Changed, "#workflow-search")
    def filter_options(self) -> None:
        query = self.query_one("#workflow-search", Input).value.casefold().split()
        options = self.query_one(OptionList)
        options.clear_options()
        default = self.settings.get("MENU_DEFAULT_ITEM")
        selected = None
        first = None
        for tag, description in self.items:
            section = tag.startswith("__sep_")
            if query and (section or not all(word in f"{tag} {plain(description)}".casefold()
                                            for word in query)):
                continue
            copy = plain(description).split("\t", 1)[0]
            if not section and self.settings.get("MENU_NO_TAGS") != "1":
                copy = f"{tag}  ·  {copy}"
            options.add_option(Option(Text(copy), id=tag, disabled=section))
            index = options.option_count - 1
            if not section and first is None:
                first = index
            if tag == default:
                selected = index
        options.highlighted = selected if selected is not None else first
        self.query_one("#workflow-device").display = False

    @on(OptionList.OptionHighlighted)
    def show_device(self, event: OptionList.OptionHighlighted) -> None:
        description = next((desc for tag, desc in self.items if tag == event.option.id), "")
        details = plain(description).partition("\t")[2]
        self.query_one("#workflow-device", Static).update(details)
        self.query_one("#workflow-device").display = bool(details)

    @on(OptionList.OptionSelected)
    def select_option(self, event: OptionList.OptionSelected) -> None:
        self.exit(event.option.id)

    @on(Input.Submitted)
    def submit_input(self) -> None:
        if self.kind == "menu":
            self.query_one(OptionList).focus()
        else:
            self.accept()

    @on(Button.Pressed, "#workflow-accept")
    def accept(self) -> None:
        self.exit(self.query_one(Input).value if self.kind == "input" else "Yes")

    @on(Button.Pressed, "#workflow-cancel")
    def action_cancel(self) -> None:
        self.exit(None)

    def move_button(self, offset: int) -> None:
        buttons = list(self.query("#workflow-buttons Button"))
        if self.focused in buttons:
            index = buttons.index(self.focused)
            buttons[max(0, min(index + offset, len(buttons) - 1))].focus()

    def action_navigate_left(self) -> None:
        if isinstance(self.focused, Button):
            if self.kind == "menu":
                self.query_one(OptionList).focus()
            else:
                self.move_button(-1)
        elif isinstance(self.focused, OptionList):
            self.action_cancel()

    def action_navigate_right(self) -> None:
        if isinstance(self.focused, Button):
            self.move_button(1)
        elif isinstance(self.focused, OptionList):
            self.focused.action_select()

    def action_search(self) -> None:
        if self.kind == "menu":
            self.query_one(Input).focus()

    def action_refresh_menu(self) -> None:
        if self.kind == "menu" and self.settings.get("MENU_REFRESH") == "1":
            options = self.query_one(OptionList)
            tag = options.get_option_at_index(options.highlighted).id if options.highlighted is not None else ""
            self.exit(f"__refresh\t{tag}")

    def action_previous_control(self) -> None:
        self.screen.focus_previous()

    def action_next_control(self) -> None:
        self.screen.focus_next()

    def check_action(self, action: str, parameters: tuple[Any, ...]) -> bool | None:
        if action == "search":
            return self.kind == "menu"
        if action == "refresh_menu":
            return self.kind == "menu" and self.settings.get("MENU_REFRESH") == "1"
        return True


def main(argv: list[str]) -> int:
    kind, title, prompt, *values = argv
    if kind in {"command", "desktop"}:
        return CommandWidget(title, [prompt, *values], desktop=kind == "desktop").run() or 0
    if kind not in {"menu", "confirm", "input", "message"}:
        raise ValueError(f"Unknown widget: {kind}")
    # Shell menus capture stdout, but Textual uses stdout's file descriptor for
    # terminal geometry. Render with both output descriptors on the terminal,
    # then restore the capture pipe before returning the selected value.
    sys.stdout.flush()
    result_fd = os.dup(sys.stdout.fileno())
    try:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        result = WorkflowWidget(kind, title, prompt, values).run()
    finally:
        sys.stdout.flush()
        os.dup2(result_fd, sys.stdout.fileno())
        os.close(result_fd)
    if result is None:
        return 0 if kind == "message" else 1
    if kind in {"menu", "input"}:
        print(result)
    return 0
