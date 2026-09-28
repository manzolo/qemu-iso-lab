"""Guest integration with local processes, fixtures and SFTP, never a running QEMU."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
import http.client
import io
import json
import shlex
import sys
import threading
import time
import unittest
from unittest import mock

from tests._common import BaseVmctlTestCase
from tests.test_web_files import SFTP_SERVER
from vmctl import cli, integration, ssh, tui_jobs, webui
from vmctl.errors import VMError


def result(stdout="", stderr="", code=0, stopped=""):
    return {"stdout": stdout, "stderr": stderr, "returncode": code, "stopped": stopped}


class IntegrationTests(BaseVmctlTestCase):
    def test_bounded_process_preserves_streams_and_exit_code(self):
        value = integration.bounded_run([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)"], 2)
        self.assertEqual(value, result("out\n", "err\n", 7))

    def test_output_limit_and_timeout_terminate_local_processes(self):
        scripts = ["import os; os.write(1, b'x'*1000000)",
                   "import os; os.write(2, b'x'*1000000)"]
        for script in scripts:
            value = integration.bounded_run([sys.executable, "-c", script], 2, 1000)
            self.assertEqual(value["stopped"], "output limit")
            self.assertEqual(len(value["stdout"]) + len(value["stderr"]), 1000)
        for script in ("import time; time.sleep(10)", "import os,time; os.close(1); os.close(2); time.sleep(10)"):
            started = time.monotonic()
            value = integration.bounded_run([sys.executable, "-c", script], .15)
            self.assertEqual(value["stopped"], "timeout")
            self.assertLess(time.monotonic() - started, 2)

    def test_read_only_ssh_never_creates_a_key(self):
        self.vm_config["ssh_provision"] = {"user": "lab", "ssh_host_port": 2222}
        with mock.patch.object(ssh, "ensure_generated_ssh_keypair") as generate, \
             mock.patch.object(ssh, "key_needs_passphrase") as keycheck:
            with self.assertRaisesRegex(VMError, "private key not found"):
                ssh.ssh_base_cmd(self.vm_config, read_only=True)
        generate.assert_not_called()
        keycheck.assert_not_called()
        key = ssh.generated_ssh_key_path(self.vm_config)
        key.parent.mkdir(parents=True)
        key.write_text("fixture key")
        argv = ssh.ssh_base_cmd(self.vm_config, read_only=True)
        self.assertIn(str(key), argv)

    def test_stopped_vm_skips_all_guest_transports(self):
        console = mock.Mock(return_value={"running": False, "clipboard_channel": None})
        with mock.patch.object(integration, "remote") as remote, \
             mock.patch.object(integration.web_files, "SFTP") as sftp, \
             mock.patch.object(integration.guest_agent, "responds") as ping:
            checks = integration.connections(self.vm_name, console)["checks"]
        self.assertTrue(all(check["status"] == "stopped" for check in checks.values()))
        remote.assert_not_called()
        sftp.assert_not_called()
        ping.assert_not_called()

    def test_checks_reuse_classifier_sftp_and_ping_without_console_input(self):
        self.vm_config["guest_agent"] = True
        self.write_config_dir()
        console = mock.Mock(return_value={"running": True, "clipboard_channel": True})
        with mock.patch.object(integration, "remote", return_value=result()) as remote, \
             mock.patch.object(integration.web_files, "SFTP") as sftp, \
             mock.patch.object(integration.guest_agent, "responds", return_value=True) as ping, \
             mock.patch("vmctl.report.wake_console", side_effect=AssertionError("must not wake")), \
             mock.patch("vmctl.qemu.qmp_execute", side_effect=AssertionError("must use console_info")):
            checks = integration.connections(self.vm_name, console)["checks"]
        self.assertEqual(checks["ssh"]["status"], "available")
        self.assertEqual(checks["agent"]["status"], "available")
        self.assertEqual(checks["sftp"]["status"], "available")
        self.assertEqual(checks["clipboard"]["status"], "unknown")
        self.assertEqual(remote.call_args.args[1:], ("exit 0", integration.PROBE_TIMEOUT, 4096))
        self.assertEqual(ping.call_args.kwargs["timeout"], integration.PROBE_TIMEOUT)
        sftp.return_value.__enter__.return_value.canonical.assert_called_once_with(".")

    def test_ssh_errors_and_sftp_failure_are_distinct(self):
        console = mock.Mock(return_value={"running": True, "clipboard_channel": False})
        for error, kind in (("Permission denied (publickey)", "denied"),
                            ("no matching host key type", "negotiate"),
                            ("Connection refused", "closed")):
            with self.subTest(kind=kind), mock.patch.object(integration, "remote", return_value=result(stderr=error, code=255)), \
                 mock.patch.object(ssh, "classify_ssh_failure", wraps=ssh.classify_ssh_failure) as classify, \
                 mock.patch.object(integration.web_files, "SFTP") as sftp:
                checks = integration.connections(self.vm_name, console)["checks"]
                self.assertEqual(checks["ssh"]["status"], kind)
                self.assertEqual(checks["clipboard"]["status"], "unavailable")
                classify.assert_called_once_with(error)
                sftp.assert_not_called()
        with mock.patch.object(integration, "remote", return_value=result()), \
             mock.patch.object(integration.web_files, "SFTP", side_effect=VMError("subsystem unavailable")):
            checks = integration.connections(self.vm_name, console)["checks"]
        self.assertEqual(checks["ssh"]["status"], "available")
        self.assertEqual(checks["sftp"]["status"], "unavailable")

    def test_cache_coalesces_concurrent_requests_and_expires(self):
        cache = integration.ConnectionCache(ttl=5)
        with mock.patch.object(integration, "connections", return_value={"checks": {}}) as probe:
            with ThreadPoolExecutor(max_workers=6) as pool:
                values = list(pool.map(lambda _: cache.get(self.vm_name, mock.Mock()), range(6)))
            self.assertEqual(probe.call_count, 1)
            self.assertTrue(all(value is values[0] for value in values))
            cache.values[self.vm_name] = (time.monotonic() - 6, values[0])
            cache.get(self.vm_name, mock.Mock())
            self.assertEqual(probe.call_count, 2)

    def test_diagnostics_select_legacy_systemd_and_windows_commands(self):
        for family in ("sysv-apt", "systemd-apt", "systemd-dnf", "systemd-apt+sudo", "windows"):
            with self.subTest(family=family):
                self.vm_config.pop("windows_config", None)
                if family == "windows":
                    self.vm_config["windows_config"] = {}
                self.write_config_dir()
                responses = [result("" if family == "windows" else family)] + [result("fixture log")] * 10
                with mock.patch.object(integration, "remote", side_effect=responses) as remote:
                    target = integration.diagnostics(self.vm_name)
                self.assertEqual(target.parent, self.root / "artifacts" / self.vm_name / "logs")
                self.assertIn("fixture log", target.read_text())
                commands = [call.args[1] for call in remote.call_args_list[1:]]
                self.assertEqual(commands, [command for _, command in integration.diagnostic_commands(family)])
                if family == "sysv-apt":
                    self.assertNotIn("journalctl", " ".join(commands))
                if family == "systemd-dnf":
                    self.assertNotIn("dnf ", " ".join(commands))  # no metadata refresh
                if family.endswith("+sudo"):
                    self.assertTrue(all(command.startswith("sudo -n sh -c ") for command in commands))
                    self.assertEqual(shlex.split(commands[0])[4], integration.POSIX_COMMANDS[0][1])
                else:
                    self.assertNotIn("sudo", " ".join(commands))

    def test_diagnostics_fall_back_to_bounded_serial_without_ssh_or_waking(self):
        directory = self.root / "artifacts" / self.vm_name / "logs"
        directory.mkdir(parents=True)
        (directory / "bootstrap-serial.log").write_bytes(b"x" * 100 + b"INSTALL ERROR")
        with mock.patch.object(integration, "remote", side_effect=VMError("No SSH")), \
             mock.patch.object(integration, "MAX_OUTPUT", 32), \
             mock.patch("vmctl.report.wake_console", side_effect=AssertionError("wake")):
            target = integration.diagnostics(self.vm_name)
        content = target.read_text()
        self.assertIn("No SSH", content)
        self.assertIn("INSTALL ERROR", content)
        self.assertNotIn("x" * 33, content)

    def test_diagnostics_continue_after_a_command_times_out(self):
        replies = [result("sysv-apt"), result("partial", code=-9, stopped="timeout")] + [result("later")] * 8
        with mock.patch.object(integration, "remote", side_effect=replies):
            text = integration.diagnostics(self.vm_name).read_text()
        self.assertIn("stopped: timeout", text)
        self.assertIn("later", text)

    def test_guest_command_needs_boolean_confirmation_and_preserves_literal_script(self):
        script = "echo 'hello'\nprintf '%s' '$HOME'"
        for consent in (False, None, "true", 1):
            with self.subTest(consent=consent), self.assertRaisesRegex(VMError, "Confirm"):
                webui.prepare_guest_command(self.vm_name, script, consent)
        for invalid in ("", "\0", "a" * 16385, []):
            with self.assertRaises(VMError):
                webui.prepare_guest_command(self.vm_name, invalid, True)
        argv = webui.prepare_guest_command(self.vm_name, script, True)
        self.assertEqual(argv, [str(self.root / "bin/vmctl"), "guest-command-helper", "--vm", self.vm_name, "--script=" + script, "--yes"])
        with mock.patch.object(ssh, "ssh_base_cmd", return_value=["ssh", "host"]), \
             mock.patch.object(integration, "bounded_run", return_value=result()) as run:
            integration.remote({}, "-oProxyCommand=anything")
        self.assertEqual(run.call_args.args[0][-3:], ["--", "host", "-oProxyCommand=anything"])

    def test_guest_result_contains_both_streams_and_returns_failure(self):
        output = io.StringIO()
        with mock.patch.object(integration, "remote", return_value=result("out", "err", 7)), contextlib.redirect_stdout(output):
            code = integration.run_guest(self.vm_name, "fixture")
        self.assertEqual(code, 7)
        self.assertIn("[stdout]\nout", output.getvalue())
        self.assertIn("[stderr]\nerr", output.getvalue())
        self.assertIn("exit: 7", output.getvalue())

    def test_job_helper_checks_confirmation_and_preserves_script(self):
        script = "printf 'first\\n'\nprintf 'second\\n'"
        argv = ["--vm", self.vm_name, "--script=" + script]
        with mock.patch.object(integration, "run_guest", return_value=7) as run:
            with self.assertRaisesRegex(VMError, "confirmation"):
                cli.dispatch_internal("guest-command-helper", argv)
            run.assert_not_called()
            self.assertEqual(cli.dispatch_internal("guest-command-helper", [*argv, "--yes"]), 7)
            run.assert_called_once_with(self.vm_name, script)

    def test_cancelling_guest_command_does_not_power_off_vm(self):
        command = webui.prepare_guest_command(self.vm_name, "sleep 10", True)
        with mock.patch.object(tui_jobs, "command", return_value=command), \
             mock.patch.object(tui_jobs, "cancel", side_effect=lambda root, name, stop: stop()), \
             mock.patch.object(webui.subprocess, "run") as stop:
            webui.cancel_job("vm:" + self.vm_name)
        stop.assert_not_called()

    def test_vm_history_keeps_a_bounded_number_of_archived_runs(self):
        directory = tui_jobs.job_dir(self.root, self.vm_name)
        archive = directory / "history"
        for index in range(3):
            (archive / f"20260101-00000{index}-old").mkdir(parents=True)
        (directory / "output.log").write_text("$ previous\n")
        (directory / "status").write_text("completed\n")
        with mock.patch.object(tui_jobs, "HISTORY_KEEP", 2):
            webui.start_job([sys.executable, "-c", "pass"], self.vm_name)
        deadline = time.monotonic() + 5
        while tui_jobs.status(directory) == "running" and time.monotonic() < deadline:
            time.sleep(.02)
        kept = sorted(path.name for path in archive.iterdir())
        self.assertEqual(len(kept), 2)
        self.assertEqual(kept[0], "20260101-000002-old")  # the oldest entries went first

    def test_jobs_preserve_multiline_commands_output_and_exit_in_vm_history(self):
        first = [sys.executable, "-c", "import sys\nprint('first result')\nsys.exit(7)"]
        second = [sys.executable, "-c", "print('second result')"]
        for command in (first, second):
            webui.start_job(command, self.vm_name)
            directory = tui_jobs.job_dir(self.root, self.vm_name)
            deadline = time.monotonic() + 5
            while tui_jobs.status(directory) == "running" and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertNotEqual(tui_jobs.status(directory), "running")
        history = webui.vm_history(self.vm_name)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["status"], "completed")
        self.assertEqual(history[1]["status"], "failed (7)")
        archived = webui.job_directory(history[1]["id"])
        self.assertEqual(tui_jobs.command(archived), first)
        self.assertIn("first result", webui.read_log(history[1]["id"], 0)["text"])
        for bad in ("history:testvm:..", "history:testvm:../../etc"):
            with self.assertRaises(VMError):
                webui.job_directory(bad)


class IntegrationHttpTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.server = webui.make_server(0, "fixture-token")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, endpoint, body=None, token="fixture-token", method=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"X-Vmctl-Token": token} if token else {}
        conn.request(method or ("GET" if body is None else "POST"), "/api/vm/testvm/" + endpoint,
                     body=None if body is None else json.dumps(body), headers=headers)
        response = conn.getresponse()
        value = response.status, response.read(), dict(response.getheaders())
        conn.close()
        return value

    def test_new_endpoints_require_token(self):
        for endpoint, body in (("connections", None), ("history", None), ("diagnostics", {}),
                               ("guest-command", {}), ("files-session", {}), ("files-session-close", {})):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.request(endpoint, body, token="")[0], 401)

    def test_connections_cache_and_command_confirmation_cannot_be_bypassed(self):
        with mock.patch.object(integration, "connections", return_value={"checks": {}}) as checks:
            self.assertEqual(self.request("connections")[0], 200)
            self.assertEqual(self.request("connections")[0], 200)
            checks.assert_called_once()
        with mock.patch.object(webui, "start_job", return_value="vm:testvm") as start:
            for value in (False, "true", 1):
                self.assertEqual(self.request("guest-command", {"command": "echo hello", "confirmed": value})[0], 400)
            start.assert_not_called()
            code, data, _ = self.request("guest-command", {"command": "echo hello", "confirmed": True})
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(data)["job"], "vm:testvm")
            self.assertEqual(start.call_args.args[1], "testvm")

    def test_diagnostics_reject_browser_commands_and_download_saved_text(self):
        with mock.patch.object(integration, "diagnostics") as collect:
            self.assertEqual(self.request("diagnostics", {"command": "reboot"})[0], 400)
            collect.assert_not_called()
        with mock.patch.object(integration, "remote", side_effect=VMError("SSH unavailable")):
            code, data, headers = self.request("diagnostics", {})
        self.assertEqual(code, 200)
        self.assertTrue(headers["Content-Disposition"].startswith('attachment; filename="diagnostics-'))
        saved = list((self.root / "artifacts/testvm/logs").glob("diagnostics-*.txt"))
        self.assertEqual(saved[0].read_bytes(), data)

    @unittest.skipUnless(SFTP_SERVER, "OpenSSH sftp-server is not installed")
    def test_upload_api_reuses_local_sftp_and_preserves_duplicates(self):
        guest = self.root / "guest"
        guest.mkdir()
        with mock.patch.object(ssh, "ssh_base_cmd", return_value=["ssh", "fixture"]):
            client = integration.web_files.SFTP({})
        client.command = [SFTP_SERVER, "-d", str(guest)]
        with mock.patch.object(integration.web_files, "SFTP", return_value=client) as start:
            code, data, _ = self.request("files-session", {})
            self.assertEqual(code, 200)
            token = json.loads(data)["session"]
            for content in (b"first", b"second"):
                conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
                conn.request("POST", f"/api/vm/testvm/files-upload?path=.&name=same.txt&session={token}",
                             body=content, headers={"X-Vmctl-Token": "fixture-token"})
                response = conn.getresponse()
                self.assertEqual(response.status, 200, response.read())
                conn.close()
            start.assert_called_once()
        self.assertEqual((guest / "same.txt").read_bytes(), b"first")
        self.assertEqual((guest / "same (2).txt").read_bytes(), b"second")
        self.assertEqual(self.request("files-session-close", {"session": token})[0], 200)
        self.assertIsNotNone(client.process.poll())
