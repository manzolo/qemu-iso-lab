"""vmctl web: the command catalog, request validation, the token/Host guard and jobs."""

import http.client
import json
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.state  # noqa: E402
from vmctl import webui  # noqa: E402
from vmctl.errors import VMError  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class CatalogTests(unittest.TestCase):
    def test_catalog_distinguishes_browser_and_terminal_commands(self):
        catalog = {entry["name"]: entry for entry in webui.command_catalog()}
        for excluded in ("shell", "console", "import-device", "web"):
            self.assertNotIn(excluded, catalog)
        self.assertIn("bootstrap-haiku", catalog)
        group = {arg["dest"]: arg for arg in catalog["group"]["args"]}
        self.assertIn("cluster", group["action"]["choices"])
        start = {arg["dest"]: arg for arg in catalog["start"]["args"]}
        self.assertEqual((start["headless"]["kind"], start["headless"]["flag"]), ("flag", "--headless"))
        self.assertTrue(catalog["clean"]["destructive"])
        self.assertIn("restore", catalog["checkpoint"]["destructive_actions"])
        self.assertTrue(catalog["flash"]["terminal_only"])
        self.assertFalse(catalog["start"]["terminal_only"])
        self.assertIn(["--expand", "--no-expand"], catalog["flash"]["exclusive_groups"])
        flash = {arg["flag"]: arg for arg in catalog["flash"]["args"]}
        self.assertTrue(flash["--device"]["required"])
        self.assertTrue(flash["--confirm-device"]["required"])
        for command in catalog.values():
            self.assertFalse(any(arg["help"] == "==SUPPRESS==" for arg in command["args"]))

    def test_flash_is_never_executed_by_the_web_even_when_confirmed(self):
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed), self.assertRaisesRegex(VMError, "cannot be run"):
                webui.prepare_command(["flash", "testvm", "--device", "/dev/test",
                                       "--confirm-device", "/dev/test"], confirmed=confirmed)

    def test_destructive_detection(self):
        self.assertTrue(webui.is_destructive(["clean", "vm"]))
        self.assertTrue(webui.is_destructive(["group", "clean", "netlab"]))
        self.assertTrue(webui.is_destructive(["check-vms", "a", "--clean-first"]))
        self.assertFalse(webui.is_destructive(["group", "up", "netlab"]))
        self.assertFalse(webui.is_destructive(["start", "vm"]))


class WebSocketTests(unittest.TestCase):
    """The console bridge speaks just enough RFC 6455 for noVNC."""

    def test_accept_key_is_the_rfc_example(self):
        self.assertEqual(webui.ws_accept("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_frames_round_trip_at_every_length_encoding(self):
        import io
        import os

        for size in (0, 5, 125, 126, 65535, 65536, 70000):
            with self.subTest(size=size):
                payload = os.urandom(size)
                mask = b"\x01\x02\x03\x04"
                frame = bytearray(webui.ws_frame(0x2, payload))
                frame[1] |= 0x80  # a client frame is masked: insert the key after the length
                header_end = 2 + (2 if size >= 126 else 0) + (6 if size >= 65536 else 0)
                masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                client = bytes(frame[:header_end]) + mask + masked
                self.assertEqual(webui.ws_read_frame(io.BytesIO(client)), (0x2, payload))
        self.assertIsNone(webui.ws_read_frame(io.BytesIO(b"")))


class RequestTests(BaseVmctlTestCase):
    def test_host_ssh_terminal_uses_fixed_cli_arguments(self):
        from unittest import mock

        with mock.patch("vmctl.ssh.ssh_target", return_value=("127.0.0.1", 2222, "user")), \
             mock.patch.dict("os.environ", {"DISPLAY": ":fixture"}), \
             mock.patch.object(webui.shutil, "which", return_value="/usr/bin/xterm"), \
             mock.patch.object(webui.subprocess, "Popen") as popen:
            webui.open_ssh_terminal(self.vm_name)
        argv = popen.call_args.args[0]
        self.assertEqual(argv, ["/usr/bin/xterm", "-e", str(self.root / "bin/vmctl"), "shell", self.vm_name])
        self.assertNotIn("shell", popen.call_args.kwargs)

    def test_host_ssh_rejects_missing_config_or_desktop(self):
        from unittest import mock

        with self.assertRaisesRegex(VMError, "SSH provisioning"):
            webui.open_ssh_terminal(self.vm_name)
        with mock.patch("vmctl.ssh.ssh_target"), mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(VMError, "No desktop session"):
                webui.open_ssh_terminal(self.vm_name)

    def test_snapshot_reports_the_actual_job_command(self):
        from unittest import mock

        directory = vmctl.state.ROOT / "artifacts/testvm/runtime/tui-job"
        directory.mkdir(parents=True)
        for command in ("start", "stop", "bootstrap-archinstall"):
            with self.subTest(command=command):
                (directory / "output.log").write_text(f"$ '/project with spaces/bin/vmctl' {command} testvm\n")
                with mock.patch("vmctl.tui_bridge.ClassicBridge") as bridge:
                    bridge.return_value.snapshot.return_value = [{"name": "testvm", "job_status": "running"}]
                    bridge.return_value.labs.return_value = []
                    snapshot = webui.Snapshot().get()
                self.assertEqual(snapshot["vms"][0]["job_command"], command)

    def test_missing_or_malformed_job_command_is_unknown(self):
        from vmctl import tui_jobs

        directory = vmctl.state.ROOT / "artifacts/testvm/runtime/tui-job"
        self.assertEqual(tui_jobs.command(directory), [])
        directory.mkdir(parents=True)
        (directory / "output.log").write_text("$ 'unterminated\n")
        self.assertEqual(tui_jobs.command(directory), [])

    def test_a_request_becomes_a_vmctl_command_and_names_its_vm(self):
        self.write_config_dir()
        command, vm = webui.prepare_command(["start", self.vm_name, "--headless", "--background"], confirmed=False)
        self.assertEqual(command, [str(self.root / "bin/vmctl"), "start", self.vm_name, "--headless", "--background"])
        self.assertEqual(vm, self.vm_name)
        _, vm = webui.prepare_command(["status"], confirmed=False)
        self.assertIsNone(vm)

    def test_refusals_explain_themselves(self):
        self.write_config_dir()
        for args, message in ((["clean", self.vm_name], "confirm it first"), (["flash", "x"], "cannot be run"),
                              (["start", self.vm_name, "--bogus"], "Unknown arguments"), ([], "Empty"),
                              (["checkpoint"], "required")):
            with self.subTest(args=args), self.assertRaisesRegex(VMError, message):
                webui.prepare_command(args, confirmed=False)

    def test_a_confirmed_request_answers_the_cli_question_itself(self):
        # Jobs have no terminal and a pipe never counts as yes: the browser's confirmation is passed on.
        self.write_config_dir()
        command, _ = webui.prepare_command(["checkpoint", "restore", self.vm_name, "clean"], confirmed=True)
        self.assertEqual(command[-1], "--yes")
        command, _ = webui.prepare_command(["clean", self.vm_name], confirmed=True)
        self.assertNotIn("--yes", command)  # clean has no question to answer

    def test_an_accelerated_display_is_read_over_vnc(self):
        # virtio-vga-gl + egl-headless (arch-noctalia) answers "no surface" to screendump.
        from unittest import mock
        from vmctl import qemu, report

        self.write_config_dir()
        vm = self.vmctl.get_vm(self.vmctl.load_config(), self.vm_name)
        for path in (qemu.qmp_socket_path(vm), qemu.vnc_socket_path(vm)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
        with mock.patch.object(qemu, "qmp_command", return_value=False), \
             mock.patch.object(report, "capture_via_vnc", return_value=b"PNG") as vnc:
            self.assertEqual(webui.screenshot_png(self.vm_name), b"PNG")
        vnc.assert_called_once()

    def test_a_web_job_runs_detached_and_its_log_is_readable(self):
        command = [sys.executable, "-c", "print('hello from a job')"]
        job = webui.start_job(command, None)
        self.assertTrue(job.startswith("web:"))
        for _ in range(100):
            log = webui.read_log(job, 0)
            if log["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(log["status"], "completed")
        self.assertIn("hello from a job", log["text"])
        self.assertEqual([j["id"] for j in webui.list_jobs()], [job])
        with self.assertRaises(VMError):
            webui.job_directory("web:../etc")


class ServerTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.server = webui.make_server(0, "secret-token")
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path, token="secret-token", host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers["X-Vmctl-Token"] = token
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        result = response.status, response.read()
        conn.close()
        return result

    def test_disconnected_browser_does_not_trigger_another_error_response(self):
        from unittest import mock

        handler = object.__new__(webui.Handler)
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler.wfile = mock.Mock()
        handler.wfile.write.side_effect = BrokenPipeError()
        handler._json({"example": True})
        self.assertTrue(handler.close_connection)

    def test_new_mutations_require_authentication(self):
        from unittest import mock

        for endpoint in ("ssh-terminal", "override"):
            with self.subTest(endpoint=endpoint), mock.patch.object(webui, "open_ssh_terminal") as terminal, \
                 mock.patch.object(webui.profile_overrides, "save_override") as save:
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
                conn.request("POST", f"/api/vm/testvm/{endpoint}", body="{}")
                response = conn.getresponse()
                self.assertEqual(response.status, 401)
                response.read()
                conn.close()
                terminal.assert_not_called()
                save.assert_not_called()

    def test_override_endpoint_saves_and_invalidates_cached_state(self):
        status, body = self.get('/api/vm/testvm/override')
        self.assertEqual(status, 200)
        revision = json.loads(body)['revision']
        self.server.RequestHandlerClass.snapshot.value = {'stale': True}
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request('POST', '/api/vm/testvm/override',
                     body=json.dumps({'revision': revision, 'override': {'memory_mb': 4096}}),
                     headers={'X-Vmctl-Token': 'secret-token'})
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())['effective']['memory_mb'], 4096)
        conn.close()
        self.assertIsNone(self.server.RequestHandlerClass.snapshot.value)

    def test_ssh_websocket_uses_profile_command_and_disables_local_escapes(self):
        from unittest import mock

        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        with mock.patch('vmctl.ssh.ssh_shell_cmd', return_value=['ssh', '-p', '2222', 'user@127.0.0.1']), \
             mock.patch('vmctl.web_terminal.bridge') as bridge:
            conn.request('GET', '/api/vm/testvm/ssh', headers={
                'X-Vmctl-Token': 'secret-token', 'Upgrade': 'websocket', 'Connection': 'Upgrade',
                'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ=='})
            response = conn.getresponse()
            self.assertEqual(response.status, 101)
            self.assertEqual(response.getheader('Sec-WebSocket-Accept'), 's3pPLMBiTxaQ9kYGzzhZRbK+xOo=')
            # The handler can still be finishing immediately after the upgrade response.
            for _ in range(100):
                if bridge.called:
                    break
                time.sleep(.01)
            self.assertEqual(bridge.call_args.args[1], ['ssh', '-o', 'EscapeChar=none', '-p', '2222', 'user@127.0.0.1'])
        conn.close()

    def test_the_page_is_public_and_the_api_needs_the_token_and_a_local_host(self):
        status, body = self.get("/", token=None)
        self.assertEqual(status, 200)
        self.assertIn(b"QEMU ISO Lab", body)
        self.assertEqual(self.get("/api/commands", token=None)[0], 401)
        self.assertEqual(self.get("/api/commands", token="wrong")[0], 401)
        self.assertEqual(self.get("/api/commands", host="attacker.example")[0], 403)
        status, body = self.get("/api/commands")
        self.assertEqual(status, 200)
        self.assertTrue(any(entry["name"] == "start" for entry in json.loads(body)))

    def test_percent_encoded_job_ids_are_decoded(self):
        # The page encodes "vm:<name>" as "vm%3A<name>": the log of a VM's job was "Unknown job".
        directory = vmctl.state.ROOT / "artifacts/testvm/runtime/tui-job"
        directory.mkdir(parents=True)
        (directory / "output.log").write_text("$ vmctl start testvm\n\nstarted\n")
        (directory / "status").write_text("completed\n")
        status, body = self.get("/api/jobs/vm%3Atestvm/log?offset=0")
        self.assertEqual(status, 200)
        self.assertIn("started", json.loads(body)["text"])

    def test_a_lab_map_is_served_only_for_a_plain_group_name(self):
        page = vmctl.state.ROOT / "artifacts/labs/netlab/network.html"
        page.parent.mkdir(parents=True)
        page.write_text("<html>map</html>")
        self.assertEqual(self.get("/labs/netlab/map"), (200, b"<html>map</html>"))
        self.assertEqual(self.get("/labs/nothing/map")[0], 404)


if __name__ == "__main__":
    unittest.main()
