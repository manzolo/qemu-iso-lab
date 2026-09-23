"""Preview behavior; UI tests require the optional Textual extra."""
import asyncio
from contextlib import nullcontext
import importlib.util
import subprocess
import unittest
from unittest.mock import Mock, patch

from vmctl.tui_bridge import ClassicBridge, primary_action, status_label, visible_rows

HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
if HAS_TEXTUAL:
    from textual.widgets import Button, DataTable, Input, Static
    from vmctl.tui_preview import Dashboard, DetailsScreen, HelpScreen, VMDetails


def profile(name, **changes):
    row = dict(name=name, label=f"{name} Linux", family="linux", running=False,
               installed=False, prepared=False, install_label="no disk", job_status="",
               memory_mb=2048, cpus=2, firmware="EFI", disk_host="", disk_capacity="",
               ssh_port="", iso_ready=True, install_detail="no disk image")
    row.update(changes)
    return row


class PreviewDataTests(unittest.TestCase):
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

    def test_bridge_passes_names_as_arguments_and_restores_interrupt_handler(self):
        bridge = ClassicBridge()
        with patch("vmctl.tui_bridge.subprocess.run", return_value=Mock(returncode=0)) as run, \
                patch("vmctl.tui_bridge.signal.signal", return_value="previous") as signal:
            bridge.run("name with spaces; $(false)", "alt-u")
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["name with spaces; $(false)", "alt-u"])
        self.assertNotIn(command[-2], command[2])
        self.assertEqual(signal.call_args.args[1], "previous")
        with self.assertRaises(ValueError):
            bridge.run("vm", "flash")

    def test_snapshot_reports_backend_errors(self):
        with patch("vmctl.tui_bridge.subprocess.run", return_value=subprocess.CompletedProcess(
                [], 1, "", "error: missing command: dialog")):
            with self.assertRaisesRegex(RuntimeError, "missing command: dialog"):
                ClassicBridge().snapshot()


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
            await pilot.press("enter")
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
