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
    def test_every_offered_command_comes_from_the_parser_and_terminal_ones_stay_out(self):
        catalog = {entry["name"]: entry for entry in webui.command_catalog()}
        for excluded in ("shell", "console", "flash", "import-device", "web"):
            self.assertNotIn(excluded, catalog)
        self.assertIn("bootstrap-haiku", catalog)
        group = {arg["dest"]: arg for arg in catalog["group"]["args"]}
        self.assertIn("cluster", group["action"]["choices"])
        start = {arg["dest"]: arg for arg in catalog["start"]["args"]}
        self.assertEqual((start["headless"]["kind"], start["headless"]["flag"]), ("flag", "--headless"))
        self.assertTrue(catalog["clean"]["destructive"])
        self.assertIn("restore", catalog["checkpoint"]["destructive_actions"])

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
        return response.status, response.read()

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

    def test_a_lab_map_is_served_only_for_a_plain_group_name(self):
        page = vmctl.state.ROOT / "artifacts/labs/netlab/network.html"
        page.parent.mkdir(parents=True)
        page.write_text("<html>map</html>")
        self.assertEqual(self.get("/labs/netlab/map"), (200, b"<html>map</html>"))
        self.assertEqual(self.get("/labs/nothing/map")[0], 404)


if __name__ == "__main__":
    unittest.main()
