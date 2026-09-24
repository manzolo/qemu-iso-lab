"""Textual dashboard behavior; UI tests require the optional Textual extra."""
import asyncio
from contextlib import nullcontext
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from vmctl.tui_bridge import ClassicBridge, primary_action, quick_actions, status_label, visible_rows

HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
if HAS_TEXTUAL:
    from textual.widgets import Button, DataTable, Input, OptionList, Static, TextArea
    from vmctl.tui_textual import Dashboard, DetailsScreen, HelpScreen, VMDetails
    from vmctl.tui_widgets import CommandWidget, WorkflowWidget


def profile(name, **changes):
    row = dict(name=name, label=f"{name} Linux", family="linux", running=False,
               installed=False, prepared=False, install_label="no disk", job_status="",
               memory_mb=2048, cpus=2, firmware="EFI", disk_host="", disk_capacity="",
               ssh_port="", iso_ready=True, install_detail="no disk image")
    row.update(changes)
    return row


class DashboardDataTests(unittest.TestCase):
    def test_printed_commands_roundtrip_shell_arguments(self):
        import io
        import shlex
        from contextlib import redirect_stdout
        from vmctl import ui
        command = ["qemu-system-x86_64", "-drive", "file=/path with spaces/disk.qcow2", "$(literal)"]
        output = io.StringIO()
        with patch.object(ui, "USE_COLOR", False), redirect_stdout(output):
            ui.print_command(command)
        self.assertEqual(shlex.split(output.getvalue().strip().removeprefix("$ ")), command)

    def test_filters_combine_query_and_state_with_natural_order(self):
        rows = [profile("vm10"), profile("vm2"),
                profile("alpine", label="Alpine Server", prepared=True),
                profile("ubuntu", running=True, prepared=True, installed=True)]
        self.assertEqual([r["name"] for r in visible_rows(rows, "", "all")],
                         ["ubuntu", "alpine", "vm2", "vm10"])
        self.assertEqual([r["name"] for r in visible_rows(rows, "ALP server", "disk")], ["alpine"])
        self.assertEqual(visible_rows(rows, "alpine", "running"), [])

    def test_iso_availability_never_implies_an_installed_vm(self):
        for ready in (True, False):
            self.assertEqual(status_label(profile("vm", iso_ready=ready))[0], "No disk")
        self.assertEqual(status_label(profile("vm", install_label="unverified"))[0], "Unverified")
        self.assertEqual(status_label(profile("vm", running=True, job_status="running"))[0], "Installing")

    def test_contextual_action_uses_the_existing_shortcut(self):
        for flags, expected in [({}, "alt-u"), ({"installed": True}, "alt-d"),
                                ({"running": True}, "alt-a"),
                                ({"running": True, "job_status": "running"}, "alt-l")]:
            self.assertEqual(primary_action(profile("vm", **flags))[1], expected)

    def test_quick_actions_follow_installation_and_runtime_state(self):
        self.assertEqual(primary_action(profile("vm")), ("Boot ISO…", "alt-u"))
        self.assertEqual(primary_action(profile("vm", has_preseed=True)), ("Unattended install…", "alt-u"))
        self.assertEqual([action for _, action in quick_actions(profile("vm", installed=True))],
                         ["alt-d", "alt-h", "Video Profile", "menu"])
        self.assertEqual([action for _, action in quick_actions(profile("vm", running=True, has_ssh=True))],
                         ["alt-a", "alt-s", "alt-x", "menu"])
        self.assertNotIn("alt-s", [action for _, action in quick_actions(profile("vm", running=True))])
        self.assertEqual([action for _, action in quick_actions(profile("vm", job_status="running"))],
                         ["alt-l", "alt-a", "menu"])

    def test_bridge_passes_names_as_arguments_and_restores_interrupt_handler(self):
        bridge = ClassicBridge()
        with patch("vmctl.tui_bridge.subprocess.run", return_value=Mock(returncode=0)) as run, \
                patch("vmctl.tui_bridge.signal.signal", return_value="previous") as signal:
            bridge.run("name with spaces; $(false)", "alt-u")
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["name with spaces; $(false)", "alt-u"])
        self.assertNotIn(command[-2], command[2])
        self.assertEqual(run.call_args.kwargs["env"]["VMTUI_UI"], "textual")
        self.assertEqual(signal.call_args.args[1], "previous")
        with self.assertRaises(ValueError):
            bridge.run("vm", "flash")

    def test_snapshot_reports_backend_errors(self):
        with patch("vmctl.tui_bridge.subprocess.run", return_value=subprocess.CompletedProcess(
                [], 1, "", "error: missing command: dialog")):
            with self.assertRaisesRegex(RuntimeError, "missing command: dialog"):
                ClassicBridge().snapshot()

    @unittest.skipUnless(HAS_TEXTUAL and sys.platform == "linux", "requires Textual and a Linux PTY")
    def test_widget_uses_terminal_size_when_stdout_is_captured(self):
        import fcntl
        import pty
        import struct
        import termios
        master, terminal = pty.openpty()
        try:
            fcntl.ioctl(terminal, termios.TIOCSWINSZ, struct.pack("HHHH", 44, 120, 0, 0))
            with tempfile.TemporaryDirectory() as tmp:
                size_path = Path(tmp) / "size.txt"
                script = '''
import sys
from pathlib import Path
from vmctl import tui_widgets
class Probe(tui_widgets.WorkflowWidget):
    CSS_PATH = Path(tui_widgets.__file__).with_name("tui_textual.tcss")
    def on_mount(self):
        super().on_mount()
        self.call_after_refresh(self.finish)
    def finish(self):
        Path(sys.argv[1]).write_text(f"{self.size.width}x{self.size.height}")
        self.exit("chosen; $(literal)")
tui_widgets.WorkflowWidget = Probe
raise SystemExit(tui_widgets.main(["menu", "Test", "Choose", "chosen", "Chosen"]))
'''
                env = {key: value for key, value in os.environ.items() if key not in {"COLUMNS", "LINES"}}
                # Drain the PTY while communicating: a full render can exceed
                # its buffer even though the selected value is on a separate pipe.
                import threading
                terminal_output = []
                def drain():
                    try:
                        while chunk := os.read(master, 65536):
                            terminal_output.append(chunk)
                    except OSError:
                        pass
                reader = threading.Thread(target=drain, daemon=True)
                reader.start()
                result = subprocess.run([sys.executable, "-c", script, str(size_path)],
                                        stdin=terminal, stderr=terminal, stdout=subprocess.PIPE,
                                        text=True, env=env, timeout=15)
                self.assertEqual(result.returncode, 0, b"".join(terminal_output).decode(errors="replace"))
                self.assertEqual(result.stdout, "chosen; $(literal)\n")
                self.assertEqual(size_path.read_text(), "120x44")
        finally:
            os.close(terminal)
            if "reader" in locals():
                reader.join(timeout=2)
            os.close(master)


@unittest.skipUnless(HAS_TEXTUAL, "optional Textual dependency is not installed")
class DashboardTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self):
        bridge = Mock(spec=ClassicBridge)
        bridge.snapshot.return_value = [profile("alpine"), profile("debian", installed=True,
            prepared=True, install_label="verified"), profile("ubuntu", running=True, prepared=True)]
        bridge.run.return_value = 0
        return Dashboard(bridge, auto_refresh=False)

    async def ready(self, app, pilot):
        for _ in range(100):
            if not app.refreshing:
                break
            await asyncio.sleep(.01)
        self.assertFalse(app.refreshing)
        await pilot.pause()

    async def test_search_filters_empty_state_and_selection_on_refresh(self):
        app = self.make_app()
        async with app.run_test(size=(120, 44)) as pilot:
            await self.ready(app, pilot)
            self.assertEqual(app.selected, "ubuntu")
            await pilot.press("down")
            self.assertEqual(app.selected, "debian")
            app.action_refresh()
            await self.ready(app, pilot)
            self.assertEqual(app.selected, "debian")
            await pilot.click("#running")
            self.assertEqual([row["name"] for row in app.filtered], ["ubuntu"])
            await pilot.press("slash")
            await pilot.press("z", "z", "z")
            self.assertEqual(app.query_one(DataTable).row_count, 0)
            self.assertTrue(app.query_one("#empty").display)
            self.assertTrue(app.query_one("#primary", Button).disabled)
            await pilot.press("escape")
            self.assertEqual(app.selected, "ubuntu")
            app.bridge.run.assert_not_called()

    async def test_narrow_layout_details_and_help_return_to_selection(self):
        app = self.make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            await self.ready(app, pilot)
            self.assertFalse(app.query_one("#details").display)
            await pilot.press("right")
            self.assertIsInstance(app.screen, DetailsScreen)
            self.assertEqual(app.screen.query_one(VMDetails).row["name"], "ubuntu")
            app.action_refresh()
            await self.ready(app, pilot)
            self.assertIsInstance(app.screen, DetailsScreen)
            await pilot.press("escape")
            await pilot.press("f1")
            self.assertIsInstance(app.screen, HelpScreen)
            await pilot.press("escape")
            await pilot.resize_terminal(120, 44)
            self.assertTrue(app.query_one("#details").display)
            self.assertEqual(app.selected, "ubuntu")

    async def test_enter_runs_selected_profile_default_action(self):
        for size in ((120, 44), (80, 30)):
            with self.subTest(size=size):
                app = self.make_app()
                async with app.run_test(size=size) as pilot:
                    await self.ready(app, pilot)
                    with patch.object(app, "suspend", return_value=nullcontext()):
                        for name, action in (("ubuntu", "alt-a"), ("debian", "alt-d"),
                                             ("alpine", "alt-u")):
                            app.bridge.run.reset_mock()
                            await pilot.press("enter")
                            await self.ready(app, pilot)
                            app.bridge.run.assert_called_once_with(name, action)
                            self.assertIsInstance(app.focused, DataTable)
                            await pilot.press("down")

    async def test_action_returns_to_same_filtered_vm(self):
        app = self.make_app()
        async with app.run_test(size=(120, 44)) as pilot:
            await self.ready(app, pilot)
            app.query_one(Input).value = "alpine"
            await pilot.pause()
            with patch.object(app, "suspend", return_value=nullcontext()):
                await pilot.click("#primary")
            await self.ready(app, pilot)
            app.bridge.run.assert_called_once_with("alpine", "alt-u")
            self.assertEqual(app.selected, "alpine")
            self.assertEqual(app.query_one(Input).value, "alpine")

    async def test_arrows_navigate_actions_without_changing_profile_or_search(self):
        app = self.make_app()
        async with app.run_test(size=(120, 44)) as pilot:
            await self.ready(app, pilot)
            app.query_one(Input).value = "ubuntu"
            await pilot.pause()
            app.query_one(DataTable).focus()
            await pilot.press("right")
            self.assertEqual(app.focused.id, "primary")
            await pilot.press("down")
            self.assertEqual(str(app.focused.label), "Stop VM")
            await pilot.press("down")
            self.assertEqual(app.focused.id, "menu")
            await pilot.press("up", "left")
            self.assertIsInstance(app.focused, DataTable)
            self.assertEqual(app.selected, "ubuntu")
            self.assertEqual(app.query_one(Input).value, "ubuntu")
            await pilot.press("right", "escape")
            self.assertIsInstance(app.focused, DataTable)
            self.assertEqual(app.query_one(Input).value, "ubuntu")
            app.bridge.run.assert_not_called()

    async def test_right_opens_compact_actions_and_left_returns(self):
        app = self.make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            await self.ready(app, pilot)
            await pilot.press("right")
            self.assertIsInstance(app.screen, DetailsScreen)
            self.assertEqual(app.focused.id, "primary")
            await pilot.press("left")
            self.assertIsInstance(app.focused, DataTable)

    async def test_all_actions_uses_textual_workflows_and_preserves_selection(self):
        app = self.make_app()
        async with app.run_test(size=(120, 44)) as pilot:
            await self.ready(app, pilot)
            await pilot.press("right", "up")
            self.assertEqual(app.focused.id, "menu")
            with patch.object(app, "suspend", return_value=nullcontext()):
                await pilot.press("enter")
            await self.ready(app, pilot)
            app.bridge.run.assert_called_once_with("ubuntu", "menu")
            self.assertEqual(app.selected, "ubuntu")

    async def test_refresh_failure_retains_rows_and_can_recover(self):
        app = self.make_app()
        async with app.run_test(size=(120, 44)) as pilot:
            await self.ready(app, pilot)
            app.bridge.snapshot.side_effect = RuntimeError("profile read failed")
            app.action_refresh()
            await self.ready(app, pilot)
            self.assertEqual(app.query_one(DataTable).row_count, 3)
            self.assertIn("profile read failed", str(app.query_one("#notice", Static).content))
            app.bridge.snapshot.side_effect = None
            app.action_refresh()
            await self.ready(app, pilot)
            self.assertFalse(app.query_one("#notice").has_class("error"))


@unittest.skipUnless(HAS_TEXTUAL, "optional Textual dependency is not installed")
class CommandWidgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_desktop_starts_detached_then_attaches_only_after_success(self):
        for results in ([0, 0], [7], [0, 9]):
            app = CommandWidget("test-vm", ["vmctl", "start", "test-vm", "--video", "std"], desktop=True)
            with patch.object(app, "execute", new_callable=AsyncMock, side_effect=results) as execute:
                async with app.run_test(size=(100, 35)) as pilot:
                    await self.wait_finished(app, pilot)
                    self.assertEqual(execute.call_args_list[0].args[0],
                                     ["vmctl", "start", "test-vm", "--video", "std", "--headless", "--background"])
                    self.assertEqual(execute.call_count, len(results))
                    if len(results) == 2:
                        self.assertEqual(execute.call_args_list[1].args[0],
                                         ["vmctl", "attach", "test-vm", "--wait", "10"])
                        self.assertTrue(app.detached_started)
                        self.assertIn("does not stop", app.query_one(TextArea).text)
                    self.assertEqual(app.code, results[-1])
                    await pilot.press("escape")

    async def test_copy_buttons_preserve_unwrapped_commands_and_selection(self):
        import shlex
        command = [sys.executable, "-c", "print('finished')"]
        qemu = shlex.join(["qemu-system-x86_64", "-drive", "file=/path with spaces/" + "disk" * 50])
        app = CommandWidget("test-vm", command)
        with patch("vmctl.tui_widgets.copy_native", return_value=True) as native:
            async with app.run_test(size=(80, 24)) as pilot:
                await self.wait_finished(app, pilot)
                app.append_output("$ " + qemu)
                await pilot.press("f2")
                await app.workers.wait_for_complete()
                self.assertEqual(app.clipboard, shlex.join(command))
                await pilot.press("f3")
                await app.workers.wait_for_complete()
                self.assertEqual(app.clipboard, qemu)
                self.assertNotIn("\n", app.clipboard)
                output = app.query_one(TextArea)
                output.focus()
                output.select_all()
                await pilot.press("ctrl+c")
                await app.workers.wait_for_complete()
                self.assertEqual(app.clipboard, output.text)
                native.assert_called_with(output.text)
                await pilot.press("escape")

    def test_native_clipboard_uses_stdin_and_falls_back(self):
        from vmctl.tui_widgets import copy_native
        text = "qemu-system-x86_64 '$(literal)'"
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}), \
                patch("vmctl.tui_widgets.shutil.which", side_effect=lambda name: name), \
                patch("vmctl.tui_widgets.subprocess.run", side_effect=[Mock(returncode=1), Mock(returncode=0)]) as run:
            self.assertTrue(copy_native(text))
            self.assertEqual(run.call_args.args[0], ["xclip", "-selection", "clipboard"])
            self.assertEqual(run.call_args.kwargs["input"], text)
            self.assertNotIn("shell", run.call_args.kwargs)

    async def wait_finished(self, app, pilot):
        for _ in range(100):
            if app.code is not None:
                break
            await asyncio.sleep(.02)
        self.assertIsNotNone(app.code)
        await pilot.pause()

    async def test_command_output_success_and_return_use_dashboard(self):
        app = CommandWidget("test-vm", [sys.executable, "-c", "print('viewer finished')"])
        async with app.run_test(size=(100, 35)) as pilot:
            await self.wait_finished(app, pilot)
            output = app.query_one(TextArea).text
            self.assertIn("viewer finished", output)
            self.assertIn("successfully", str(app.query_one("#command-status", Static).content))
            self.assertEqual(app.focused.id, "command-back")
            await pilot.press("enter")
        self.assertEqual(app.return_value, 0)

    async def test_command_failure_is_visible_and_keeps_exit_code(self):
        app = CommandWidget("test-vm", [sys.executable, "-c",
                            "import sys; print('display unavailable', file=sys.stderr); sys.exit(7)"])
        async with app.run_test(size=(80, 24)) as pilot:
            await self.wait_finished(app, pilot)
            output = app.query_one(TextArea).text
            self.assertIn("display unavailable", output)
            self.assertTrue(app.query_one("#command-status").has_class("error"))
            await pilot.press("escape")
        self.assertEqual(app.return_value, 7)

    async def test_launch_failure_returns_to_dashboard(self):
        app = CommandWidget("test-vm", ["/nonexistent/vmctl", "start", "test-vm"])
        async with app.run_test(size=(80, 24)) as pilot:
            await self.wait_finished(app, pilot)
            self.assertEqual(app.code, 1)
            await pilot.press("enter")
        self.assertEqual(app.return_value, 1)

    async def test_live_session_cannot_be_closed_accidentally_and_interrupts_explicitly(self):
        app = CommandWidget("test-vm", [sys.executable, "-c",
                            "import time; print('session ready', flush=True); time.sleep(30)"])
        async with app.run_test(size=(100, 35)) as pilot:
            for _ in range(100):
                output = app.query_one(TextArea).text
                if "session ready" in app.query_one(TextArea).text.splitlines():
                    break
                await asyncio.sleep(.02)
            self.assertIn("session ready", output)
            self.assertTrue(app.query_one("#command-back", Button).disabled)
            await pilot.press("escape", "enter", "ctrl+q")
            self.assertTrue(app.is_running)
            self.assertIsNone(app.code)
            await pilot.press("ctrl+c")
            self.assertIsNone(app.code)
            await pilot.press("f6")
            await self.wait_finished(app, pilot)
            self.assertNotEqual(app.code, 0)
            await pilot.press("escape")


@unittest.skipUnless(HAS_TEXTUAL, "optional Textual dependency is not installed")
class WorkflowWidgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_menu_reserves_visible_space_for_multiple_actions(self):
        for size in [(80, 24), (115, 53)]:
            values = [value for i in range(30) for value in (f"action-{i}", f"Action {i}")]
            app = WorkflowWidget("menu", "Actions", "VM status\nSuggested action", values, {})
            async with app.run_test(size=size) as pilot:
                await pilot.pause()
                self.assertGreaterEqual(app.query_one(OptionList).size.height, 6)
                await pilot.press("escape")

    async def test_menu_skips_headings_filters_and_returns_exact_tag(self):
        app = WorkflowWidget("menu", "Actions", "Choose", [
            "__sep_RUN", "RUN", "Boot Desktop", "Start with display",
            "Video Profile", "Choose display settings"], {"MENU_NO_TAGS": "1"})
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            self.assertEqual(app.query_one(OptionList).highlighted, 1)
            await pilot.press("slash", "v", "i", "d", "e", "o", "enter", "right")
        self.assertEqual(app.return_value, "Video Profile")

    async def test_menu_default_refresh_and_cancel(self):
        for key, expected in [("f5", "__refresh\tvideo"), ("left", None), ("escape", None)]:
            app = WorkflowWidget("menu", "Actions", "Choose", ["boot", "Boot", "video", "Video"],
                                 {"MENU_DEFAULT_ITEM": "video", "MENU_REFRESH": "1"})
            async with app.run_test(size=(80, 30)) as pilot:
                await pilot.pause()
                self.assertEqual(app.query_one(OptionList).highlighted, 1)
                await pilot.press(key)
            self.assertEqual(app.return_value, expected)

    async def test_destructive_confirmation_defaults_to_cancel_and_requires_selection(self):
        for keys, expected in [(('enter',), None), (('escape',), None), (('right', 'enter'), "Yes")]:
            app = WorkflowWidget("confirm", "DANGER", "Erase /dev/example?", [],
                                 {"CONFIRM_DESTRUCTIVE": "1"})
            async with app.run_test(size=(80, 24)) as pilot:
                self.assertEqual(app.focused.id, "workflow-cancel")
                self.assertEqual(str(app.query_one("#workflow-accept", Button).label), "Erase disk")
                await pilot.press(*keys)
            self.assertEqual(app.return_value, expected)

    async def test_confirmation_horizontal_arrows_move_between_buttons_without_dismissing(self):
        app = WorkflowWidget("confirm", "Bootstrap", "Start installation?", [], {})
        async with app.run_test(size=(115, 53)) as pilot:
            await pilot.press("left")
            self.assertEqual(app.focused.id, "workflow-cancel")
            self.assertTrue(app.is_running)
            await pilot.press("right")
            self.assertEqual(app.focused.id, "workflow-accept")
            await pilot.press("right")
            self.assertEqual(app.focused.id, "workflow-accept")
            await pilot.press("left")
            self.assertEqual(app.focused.id, "workflow-cancel")
            self.assertTrue(app.is_running)
            await pilot.press("escape")
        self.assertIsNone(app.return_value)

    async def test_input_arrows_edit_value_and_escape_cancels(self):
        app = WorkflowWidget("input", "Device", "Type the device path", ["/dev/example"], {})
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("end", "left", "right", "enter")
        self.assertEqual(app.return_value, "/dev/example")
        app = WorkflowWidget("input", "Device", "Type the device path", ["/dev/example"], {})
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("escape")
        self.assertIsNone(app.return_value)

    async def test_device_details_follow_selection(self):
        app = WorkflowWidget("menu", "Devices", "Choose", [
            "/dev/a", "Disk A\tSerial: A123\nSize: 20G",
            "/dev/b", "Disk B\tSerial: B456\nSize: 40G"], {"MENU_NO_TAGS": "1"})
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.press("down")
            self.assertIn("B456", str(app.query_one("#workflow-device", Static).content))
            await pilot.press("enter")
        self.assertEqual(app.return_value, "/dev/b")
