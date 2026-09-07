"""Protocol regressions use fragmented/coalesced wire data without a live VM."""
import json
import socket
from unittest import mock

from tests._common import BaseVmctlTestCase
from vmctl import cli, config, guest_agent, lifecycle, qemu
from vmctl.errors import VMError


class GuestAgentTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.vm_config["guest_agent"] = True
        self.write_config_dir()
        path = guest_agent.socket_path(self.vm_config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        self.sock = mock.MagicMock()
        self.sock.__enter__.return_value = self.sock
        patcher = mock.patch.object(guest_agent.socket, "socket", return_value=self.sock)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(guest_agent.secrets, "randbits", return_value=42)
        patcher.start()
        self.addCleanup(patcher.stop)

    def replies(self, *chunks):
        self.sock.recv.side_effect = [b'\xff{"return":42}\n', *chunks]

    def test_sync_ignores_stale_and_partial_replies_and_preserves_buffer(self):
        self.sock.recv.side_effect = [
            b'broken\n{"return":9}\n{"error":{"desc":"stale"}}\npartial',
            b'\xff{"ret', b'urn":42}\n{"return":', b'{}}\n',
        ]
        self.assertEqual(guest_agent.command(self.vm_config, "guest-ping"), {})
        sent = [call.args[0] for call in self.sock.sendall.call_args_list]
        self.assertEqual(json.loads(sent[0][1:]), {
            "execute": "guest-sync-delimited", "arguments": {"id": 42},
        })
        self.assertEqual(json.loads(sent[1]), {"execute": "guest-ping"})

    def test_shutdown_does_not_wait_for_nonexistent_success_reply(self):
        self.replies()
        guest_agent.shutdown(self.vm_config)
        self.assertEqual(self.sock.recv.call_count, 1)
        self.assertEqual(json.loads(self.sock.sendall.call_args.args[0]), {
            "execute": "guest-shutdown", "arguments": {"mode": "powerdown"},
        })

    def test_bad_replies_raise_vmerror(self):
        for reply in (b'bad json\n', b'\xff\n', b'[]\n', b'{}\n',
                      b'{"error":{"desc":"disabled"}}\n', b'{"error":"disabled"}\n', b''):
            with self.subTest(reply=reply):
                self.replies(reply)
                with self.assertRaises(VMError):
                    guest_agent.command(self.vm_config, "guest-ping")

    def test_timeout_is_shared_by_sync_and_command(self):
        self.replies(b'{"return":{}}\n')
        with mock.patch.object(guest_agent.time, "monotonic", side_effect=[0, 0, 1, 2, 3, 6]):
            with self.assertRaisesRegex(VMError, "timed out"):
                guest_agent.command(self.vm_config, "guest-ping", timeout=5)
        self.assertEqual(self.sock.sendall.call_count, 1)

    def test_silent_agent_and_connection_error_are_unavailable(self):
        for error in (socket.timeout("silent"), ConnectionRefusedError("stale socket")):
            with self.subTest(error=error):
                self.sock.connect.side_effect = error
                self.assertFalse(guest_agent.responds(self.vm_config))

    def test_oversized_reply_is_rejected(self):
        self.replies(b'x' * 33)
        with mock.patch.object(guest_agent, "MAX_REPLY_BYTES", 32):
            with self.assertRaisesRegex(VMError, "size limit"):
                guest_agent.command(self.vm_config, "guest-ping")

    def test_disabled_and_missing_socket_fail_before_connect(self):
        self.vm_config["guest_agent"] = False
        with self.assertRaisesRegex(VMError, "disabled"):
            guest_agent.command(self.vm_config, "guest-ping")
        self.vm_config["guest_agent"] = True
        guest_agent.socket_path(self.vm_config).unlink()
        with self.assertRaisesRegex(VMError, "socket not found"):
            guest_agent.command(self.vm_config, "guest-ping")
        self.sock.connect.assert_not_called()

    def test_invalid_timeouts_fail_before_connect(self):
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(VMError):
                guest_agent.command(self.vm_config, "guest-ping", timeout=timeout)
        self.sock.connect.assert_not_called()

    def test_addresses_filter_windows_loopback_invalid_and_duplicate_ips(self):
        interfaces = [{"name": "Loopback Pseudo-Interface 1", "ip-addresses": [
            {"ip-address": "127.0.0.1"}, {"ip-address": "::1"},
        ]}, {"name": "Ethernet", "ip-addresses": [
            {"ip-address": value} for value in ("", "invalid", "0.0.0.0", "::", "10.0.2.15", "10.0.2.15", "fe80::1")
        ]}]
        with mock.patch.object(guest_agent, "command", return_value=interfaces):
            self.assertEqual(guest_agent.addresses(self.vm_config), [
                ("Ethernet", "10.0.2.15"), ("Ethernet", "fe80::1"),
            ])

    def test_info_keeps_addresses_when_osinfo_is_unsupported(self):
        with mock.patch.object(guest_agent, "command", return_value={}), \
             mock.patch.object(guest_agent, "os_description", side_effect=VMError("unsupported")), \
             mock.patch.object(guest_agent, "addresses", return_value=[("eth0", "10.0.2.15")]), \
             mock.patch.object(guest_agent.ui, "print_kv") as print_kv:
            guest_agent.print_report(self.vm_name, self.vm_config)
        print_kv.assert_called_once_with("eth0", "10.0.2.15")

    def test_agent_channel_has_explicit_bus_with_clipboard_and_spice(self):
        self.create_disk()
        self.vm_config["clipboard"] = True
        for options in ({}, {"headless": True}, {"spice_port": 5901}):
            with self.subTest(options=options), mock.patch.object(qemu.runtime, "require_command"):
                args = qemu.common_args(self.vm_config, None, dry_run=True, **options)
            self.assertEqual(args.count("virtio-serial-pci,id=qga-serial"), 1)
            self.assertIn("virtserialport,bus=qga-serial.0,chardev=qga0,name=org.qemu.guest_agent.0", args)
        self.vm_config["guest_agent"] = False
        self.assertEqual(qemu.guest_agent_args(self.vm_config), [])

    def test_guest_agent_config_requires_boolean(self):
        for value in ("false", 1, {}, None):
            self.vm_config["guest_agent"] = value
            self.assertIn(f"{self.vm_name}: guest_agent must be a boolean",
                          config.validate_vm_profile(self.vm_name, self.vm_config))

    def test_cli_default_and_dry_run(self):
        args = cli.build_parser().parse_args(["--dry-run", "agent", self.vm_name])
        self.assertEqual(args.action, "info")
        self.assertEqual(args.func(args), 0)
        self.sock.connect.assert_not_called()

    def test_stop_uses_agent_and_observes_process_exit(self):
        with mock.patch.object(guest_agent, "shutdown") as shutdown, \
             mock.patch.object(lifecycle, "process_cmdline", side_effect=["qemu", None]), \
             mock.patch.object(qemu, "qmp_command") as qmp, \
             mock.patch.object(lifecycle.os, "kill") as kill:
            self.assertEqual(lifecycle.stop_qemu_process(123, "Stop", "VM", agent_vm=self.vm_config), 0)
        shutdown.assert_called_once_with(self.vm_config)
        qmp.assert_not_called()
        kill.assert_not_called()

    def test_stop_falls_back_to_acpi_if_agent_fails_or_guest_stays_up(self):
        qmp_path = qemu.qmp_socket_path(self.vm_config)
        qmp_path.touch()
        for failure in (VMError("unavailable"), None):
            with self.subTest(failure=failure), \
                 mock.patch.object(guest_agent, "shutdown", side_effect=failure), \
                 mock.patch.object(lifecycle, "process_cmdline", side_effect=["qemu", None]), \
                 mock.patch.object(qemu, "qmp_command", return_value=True) as qmp, \
                 mock.patch.object(lifecycle.os, "kill"), \
                 mock.patch.object(lifecycle.time, "sleep"), \
                 mock.patch.object(lifecycle.time, "monotonic", side_effect=range(100)):
                self.assertEqual(lifecycle.stop_qemu_process(
                    123, "Stop", "VM", agent_vm=self.vm_config, qmp_socket=qmp_path, grace_sec=0,
                ), 0)
                qmp.assert_called_once_with(qmp_path, "system_powerdown")
            qmp_path.touch()

    def test_stop_dry_run_does_not_contact_agent(self):
        with mock.patch.object(guest_agent, "shutdown") as shutdown, \
             mock.patch.object(lifecycle, "process_cmdline", return_value="qemu"):
            self.assertEqual(lifecycle.stop_qemu_process(
                123, "Stop", "VM", agent_vm=self.vm_config, dry_run=True,
            ), 0)
        shutdown.assert_not_called()
