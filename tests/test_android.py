"""Android live boot and console regressions; no QEMU or ADB server is needed."""
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests._common import BaseVmctlTestCase
from vmctl import android, cli, iso, lifecycle, qemu, runtime, state, webui
from vmctl.errors import VMError

ROOT = Path(__file__).resolve().parent.parent


def run_cli(args):
    with mock.patch.object(sys, "argv", ["vmctl", *args]):
        return cli.main()


class AndroidTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.vm_config.update(json.loads((ROOT / "vms/profiles/android.json").read_text())["vms"]["blissos-16"])
        self.vm_config["disk"]["path"] = f"artifacts/{self.vm_name}/disk.qcow2"
        self.write_config_dir()

    def test_endpoint_uses_runtime_forward_and_clone_port(self):
        self.vm_config["networks"] = [
            {"type": "user", "phase": "install", "hostfwd": [{"host_port": 2000, "guest_port": 5555}]},
            {"type": "user", "phase": "runtime", "hostfwd": [{"host_port": 2456, "guest_port": 5555}]},
        ]
        self.assertEqual(android.adb_endpoint(self.vm_config), "127.0.0.1:2456")

    def test_invalid_endpoints_and_other_families_are_rejected(self):
        for forwards in ([], [{"host_port": 0, "guest_port": 5555}],
                         [{"host_port": 65536, "guest_port": 5555}],
                         [{"host_port": 1234, "guest_port": 22}],
                         [{"host_port": 1234, "guest_port": 5555}] * 2):
            with self.subTest(forwards=forwards):
                self.vm_config["networks"][0]["hostfwd"] = forwards
                with self.assertRaises(VMError):
                    android.adb_endpoint(self.vm_config)
        self.vm_config["meta"]["family"] = "debian"
        with self.assertRaisesRegex(VMError, "Android profile"):
            android.adb_endpoint(self.vm_config)

    def test_qemu_forwards_adb_only_on_loopback(self):
        args = qemu.network_args(self.vm_config)
        self.assertIn("hostfwd=tcp:127.0.0.1:2373-:5555", " ".join(args))
        self.assertNotIn("-:22", " ".join(args))

    def test_console_dry_run_needs_no_host_tools(self):
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("external command")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(run_cli(["--dry-run", "android-console", self.vm_name,
                                       "--fix-bliss-codecs", "--no-audio", "--no-playback",
                                       "--record", "video with spaces.mp4", "--time-limit", "10"]), 0)
        self.assertIn("--serial=127.0.0.1:2373", output.getvalue())
        self.assertIn("--record 'video with spaces.mp4'", output.getvalue())

    def test_console_rejects_bad_options_before_host_access(self):
        for options in (["--timeout", "nan"], ["--timeout", "inf"], ["--timeout", "0"],
                        ["--time-limit", "0"], ["--no-playback"]):
            with self.subTest(options=options), mock.patch.object(runtime, "require_command") as require, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(run_cli(["--dry-run", "android-console", self.vm_name, *options]), 0)
                require.assert_not_called()

    def test_boot_wait_reconnects_only_the_offline_target(self):
        def result(out="", err="", code=0):
            return subprocess.CompletedProcess([], code, out, err)
        with mock.patch.object(runtime, "require_command"), \
                mock.patch.object(android.time, "sleep"), \
                mock.patch.object(subprocess, "run", side_effect=[
                    result("connected"), result(err="error: device offline", code=1),
                    result("reconnecting"), result("connected"), result("1\n"),
                ]) as run:
            android.wait_for_boot("127.0.0.1:2456", 10)
        self.assertEqual(run.call_args_list[2].args[0], ["adb", "-s", "127.0.0.1:2456", "reconnect"])
        self.assertEqual(run.call_args.args[0], ["adb", "-s", "127.0.0.1:2456", "shell", "getprop", "sys.boot_completed"])
        self.assertTrue(all(0 < call.kwargs["timeout"] <= 5 for call in run.call_args_list))

    def test_unauthorized_adb_fails_without_waiting_or_scrcpy(self):
        result = subprocess.CompletedProcess([], 0, "device unauthorized", "warning")
        with mock.patch.object(runtime, "require_command"), \
                mock.patch.object(subprocess, "run", return_value=result), \
                mock.patch.object(android.time, "sleep") as sleep:
            with self.assertRaisesRegex(VMError, "authorize this host"):
                android.wait_for_boot("127.0.0.1:2373", 10)
            sleep.assert_not_called()

    def test_adb_request_timeout_obeys_total_deadline(self):
        with mock.patch.object(runtime, "require_command"), \
                mock.patch.object(android.time, "monotonic", side_effect=[0, 0, 0, 3, 3]), \
                mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("adb", 2)) as run:
            with self.assertRaisesRegex(VMError, "ADB request timed out"):
                android.wait_for_boot("127.0.0.1:2373", 2)
        self.assertEqual(run.call_args.kwargs["timeout"], 2)

    def test_nonfinite_wait_never_calls_adb(self):
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), mock.patch.object(runtime, "require_command") as require:
                with self.assertRaises(VMError):
                    android.wait_for_boot("127.0.0.1:2373", timeout)
                require.assert_not_called()

    def test_console_repairs_before_scrcpy_and_propagates_failure(self):
        events = []
        with mock.patch.object(runtime, "require_command"), \
                mock.patch.object(android, "wait_for_boot", side_effect=lambda *a: events.append("boot")), \
                mock.patch.object(android, "repair_bliss_codecs", side_effect=lambda *a: events.append("repair")), \
                mock.patch.object(runtime, "run", side_effect=lambda *a: events.append("scrcpy")) as run:
            self.assertEqual(run_cli(["android-console", self.vm_name, "--fix-bliss-codecs", "--no-audio"]), 0)
            self.assertEqual(events, ["boot", "repair", "scrcpy"])
            self.assertEqual(run.call_args.args[0], ["scrcpy", "--serial=127.0.0.1:2373", "--no-audio"])
        with mock.patch.object(runtime, "require_command"), \
                mock.patch.object(android, "wait_for_boot"), \
                mock.patch.object(android, "repair_bliss_codecs", side_effect=VMError("wrong build")), \
                mock.patch.object(runtime, "run") as run, contextlib.redirect_stderr(io.StringIO()):
            self.assertNotEqual(run_cli(["android-console", self.vm_name, "--fix-bliss-codecs"]), 0)
            run.assert_not_called()

    def test_repair_waits_after_root_then_submits_guarded_script(self):
        success = subprocess.CompletedProcess([], 0, "repaired", "")
        with mock.patch.object(state, "ROOT", ROOT), \
                mock.patch.object(android, "wait_for_boot") as wait, \
                mock.patch.object(subprocess, "run", return_value=success) as run:
            android.repair_bliss_codecs("127.0.0.1:2456", 20)
        self.assertEqual(run.call_args_list[0].args[0], ["adb", "-s", "127.0.0.1:2456", "root"])
        wait.assert_called_once_with("127.0.0.1:2456", 20)
        self.assertEqual(run.call_args.args[0], ["adb", "-s", "127.0.0.1:2456", "shell", "sh"])
        self.assertIn("ro.build.fingerprint", run.call_args.kwargs["input"])
        self.assertEqual(run.call_args.kwargs["timeout"], 15)

    def test_repair_reports_root_script_and_timeout_failures(self):
        success = subprocess.CompletedProcess([], 0, "", "")
        failure = subprocess.CompletedProcess([], 1, "", "unsupported build")
        for results, message in (([failure], "Cannot request root"),
                                 ([success, failure], "unsupported build"),
                                 ([subprocess.TimeoutExpired("adb", 10)], "Timed out")):
            with self.subTest(message=message), mock.patch.object(state, "ROOT", ROOT), \
                    mock.patch.object(android, "wait_for_boot"), \
                    mock.patch.object(subprocess, "run", side_effect=results):
                with self.assertRaisesRegex(VMError, message):
                    android.repair_bliss_codecs("127.0.0.1:2373", 20)

    def test_live_boot_is_snapshot_and_does_not_record_an_install(self):
        with mock.patch.object(runtime, "require_command"), \
                mock.patch.object(iso, "ensure_iso", return_value=self.root / "android.iso"), \
                mock.patch.object(iso, "extract_installer_boot_artifacts", return_value=(self.root / "kernel", self.root / "initrd")), \
                mock.patch.object(lifecycle, "ensure_vm_disk") as disk, \
                mock.patch.object(runtime, "run_background", return_value=None) as run, \
                mock.patch.object(lifecycle.vmstate, "begin_install") as install:
            self.assertEqual(run_cli(["--dry-run", "android-live", self.vm_name]), 0)
        command = run.call_args.args[0]
        self.assertIn("-snapshot", command)
        self.assertIn("virtio-vga", command)
        self.assertIn("GRALLOC4_MINIGBM=1", command[command.index("-append") + 1])
        self.assertIn("-qmp", command)
        self.assertTrue(run.call_args.kwargs["dry_run"])
        disk.assert_not_called()
        install.assert_not_called()

    def test_manual_install_commands_refuse_live_profiles_before_side_effects(self):
        for command in (["provision"], ["provision", "--no-start"], ["install"]):
            for dry_run in (False, True):
                with self.subTest(command=command, dry_run=dry_run), \
                        mock.patch.object(runtime, "ensure_vm_dirs") as directories, \
                        mock.patch.object(iso, "ensure_iso") as medium, \
                        mock.patch.object(lifecycle, "ensure_vm_disk") as disk, \
                        mock.patch.object(qemu, "common_args") as qemu_args, \
                        mock.patch.object(lifecycle.vmstate, "begin_install") as install, \
                        contextlib.redirect_stderr(io.StringIO()) as error:
                    argv = (["--dry-run"] if dry_run else []) + [command[0], self.vm_name, *command[1:]]
                    self.assertNotEqual(run_cli(argv), 0)
                    self.assertIn(f"vmctl android-live {self.vm_name}", error.getvalue())
                    self.assertIn(f"vmctl prep {self.vm_name}", error.getvalue())
                    for operation in (directories, medium, disk, qemu_args, install):
                        operation.assert_not_called()

    def test_poweroff_targets_only_the_profile_adb_endpoint(self):
        with mock.patch.object(runtime, "require_command") as require, \
                mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            android.poweroff(self.vm_config)
        require.assert_called_once_with("adb")
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ["adb", "connect", "127.0.0.1:2373"],
            ["adb", "-s", "127.0.0.1:2373", "shell", "reboot", "-p"],
        ])
        self.assertTrue(all(call.kwargs["timeout"] == 5 for call in run.call_args_list))

    def test_poweroff_reports_adb_errors_and_timeouts(self):
        success = subprocess.CompletedProcess([], 0, "", "")
        refused = subprocess.CompletedProcess([], 1, "", "device unauthorized")
        for responses in ([refused], [success, refused], [subprocess.TimeoutExpired("adb", 5)],
                          [OSError("adb disappeared")]):
            with self.subTest(responses=responses), mock.patch.object(runtime, "require_command"), \
                    mock.patch.object(subprocess, "run", side_effect=responses):
                with self.assertRaisesRegex(VMError, "ADB power-off request failed"):
                    android.poweroff(self.vm_config)
        with mock.patch.object(runtime, "require_command", side_effect=VMError("Missing command: adb")), \
                mock.patch.object(subprocess, "run") as run:
            with self.assertRaisesRegex(VMError, "Missing command"):
                android.poweroff(self.vm_config)
            run.assert_not_called()

    def test_stop_android_uses_adb_before_acpi_and_cleans_runtime_files(self):
        pid_path = lifecycle.bootstrap_pid_path(self.vm_name)
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text("4242")
        sock_path = qemu.qmp_socket_path(self.vm_config)
        sock_path.parent.mkdir(parents=True, exist_ok=True)
        sock_path.touch()
        with mock.patch.object(lifecycle, "is_bootstrap_vm_running", return_value=(True, 4242, "qemu")), \
                mock.patch.object(lifecycle, "process_cmdline", side_effect=["qemu", None]), \
                mock.patch.object(android, "poweroff") as poweroff, \
                mock.patch.object(qemu, "qmp_command") as acpi, \
                mock.patch.object(lifecycle.os, "kill") as kill:
            self.assertEqual(run_cli(["stop", self.vm_name]), 0)
        self.assertEqual(poweroff.call_args.args[0]["meta"]["family"], "android")
        poweroff.assert_called_once()
        acpi.assert_not_called()
        kill.assert_not_called()
        self.assertFalse(pid_path.exists())
        self.assertFalse(sock_path.exists())

    def test_stop_android_force_and_dry_run_skip_adb(self):
        for option in ("--force", "--dry-run"):
            with self.subTest(option=option), \
                    mock.patch.object(lifecycle, "is_bootstrap_vm_running", return_value=(True, 4242, "qemu")), \
                    mock.patch.object(lifecycle, "process_cmdline", side_effect=["qemu", None]), \
                    mock.patch.object(android, "poweroff") as poweroff, \
                    mock.patch.object(lifecycle.os, "kill") as kill:
                argv = ["--dry-run", "stop", self.vm_name] if option == "--dry-run" else ["stop", self.vm_name, option]
                self.assertEqual(run_cli(argv), 0)
                poweroff.assert_not_called()
                if option == "--force":
                    kill.assert_called_once_with(4242, signal.SIGTERM)
                else:
                    kill.assert_not_called()

    def test_stop_android_falls_back_without_adb_grace_when_request_fails(self):
        sock_path = self.root / "qmp.sock"
        sock_path.touch()
        events = []
        def refused(vm):
            events.append("adb")
            raise VMError("ADB unavailable")
        with mock.patch.object(android, "poweroff", side_effect=refused), \
                mock.patch.object(lifecycle, "process_cmdline", side_effect=["qemu", "qemu", None]), \
                mock.patch.object(qemu, "qmp_command", side_effect=lambda *a: events.append("acpi") or True), \
                mock.patch.object(lifecycle.time, "sleep") as sleep, \
                mock.patch.object(lifecycle.os, "kill") as kill:
            self.assertEqual(lifecycle.stop_qemu_process(4242, "Stop", "Android", agent_vm=self.vm_config,
                                                       qmp_socket=sock_path), 0)
        self.assertEqual(events, ["adb", "acpi"])
        sleep.assert_not_called()
        kill.assert_not_called()

    def test_stop_android_accepts_disconnect_after_guest_has_exited(self):
        with mock.patch.object(android, "poweroff", side_effect=VMError("ADB disconnected")), \
                mock.patch.object(lifecycle, "process_cmdline", side_effect=["qemu", None]), \
                mock.patch.object(qemu, "qmp_command") as acpi, \
                mock.patch.object(lifecycle.os, "kill") as kill:
            self.assertEqual(lifecycle.stop_qemu_process(4242, "Stop", "Android", agent_vm=self.vm_config), 0)
        acpi.assert_not_called()
        kill.assert_not_called()

    def test_stop_android_does_not_wait_forever_after_accepted_adb_request(self):
        clock = {"now": 0, "alive": True}
        def sleep(seconds):
            clock["now"] += seconds
        def terminate(pid, sig):
            self.assertEqual(sig, signal.SIGTERM)
            self.assertEqual(clock["now"], 3)
            clock["alive"] = False
        with mock.patch.object(android, "poweroff"), \
                mock.patch.object(lifecycle, "process_cmdline", side_effect=lambda pid: "qemu" if clock["alive"] else None), \
                mock.patch.object(lifecycle.time, "monotonic", side_effect=lambda: clock["now"]), \
                mock.patch.object(lifecycle.time, "sleep", side_effect=sleep), \
                mock.patch.object(lifecycle.os, "kill", side_effect=terminate):
            self.assertEqual(lifecycle.stop_qemu_process(4242, "Stop", "Android", agent_vm=self.vm_config,
                                                       grace_sec=3), 0)

    def test_live_boot_refuses_running_vm_before_downloading(self):
        with mock.patch.object(lifecycle, "is_bootstrap_vm_running", return_value=(True, 123, None)), \
                mock.patch.object(iso, "ensure_iso") as medium, contextlib.redirect_stderr(io.StringIO()):
            self.assertNotEqual(run_cli(["android-live", self.vm_name]), 0)
            medium.assert_not_called()

    def test_live_dry_run_needs_no_qemu_or_iso_tools(self):
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("external command")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("external process")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_cli(["--dry-run", "android-live", self.vm_name]), 0)
        self.assertIn("-snapshot", output.getvalue())

    def test_web_console_is_discoverable_but_cannot_be_a_detached_job(self):
        catalog = {item["name"]: item for item in webui.command_catalog()}
        self.assertTrue(catalog["android-console"]["terminal_only"])
        self.assertFalse(catalog["android-live"]["terminal_only"])
        for confirmed in (False, True):
            with self.assertRaisesRegex(VMError, "cannot be run"):
                webui.prepare_command(["android-console", self.vm_name], confirmed)
        self.assertEqual(webui.prepare_terminal_command(["android-console", self.vm_name])[1:],
                         ["android-console", self.vm_name])


class BlissCodecScriptTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.linker = self.root / "linkerconfig/com.android.media.swcodec/ld.config.txt"
        self.linker.parent.mkdir(parents=True)
        self.original = (
            'namespace.sphal.search.paths = /vendor/${LIB}\n'
            'namespace.sphal.search.paths += /system/${LIB}/arm64\n'
            'namespace.sphal.asan.search.paths += /system/${LIB}/arm/nb\n'
            'namespace.sphal.search.paths += /system/${LIB}\n'
            'namespace.sphal.permitted.paths += /system/${LIB}/arm64\n'
            'namespace.default.search.paths += /system/${LIB}/arm64\n'
        )
        self.linker.write_text(self.original)
        script = (ROOT / "vms/profile-files/android/fix-bliss-codecs.sh").read_text()
        self.script = script.replace("/linkerconfig/", str(self.root / "linkerconfig") + "/")
        self.bindir = self.root / "bin"
        self.bindir.mkdir()
        self.tool("getprop", """case "$1" in
ro.build.fingerprint) echo "${FINGERPRINT-Android-x86/bliss_x86_64/x86_64:13/TQ3A.230901.001.C1/151:userdebug/test-keys}";;
ro.bliss.version) echo 16.9.7;;
ro.hardware.gralloc) echo "${GRALLOC-minigbm_arcvm}";;
debug.ui.default_mapper) echo 4;;
esac""")
        self.tool("id", 'echo "${TEST_UID-0}"')
        self.tool("setprop", 'echo "$*" >> "$RESTART_LOG"')
        self.env = dict(os.environ, PATH=f"{self.bindir}:/usr/bin:/bin", RESTART_LOG=str(self.root / "restart"))

    def tool(self, name, content):
        path = self.bindir / name
        path.write_text("#!/bin/sh\n" + content + "\n")
        path.chmod(0o755)

    def run_script(self):
        return subprocess.run(["/bin/sh"], input=self.script, text=True, capture_output=True, env=self.env)

    def test_workaround_changes_only_sphal_arm_searches_and_is_idempotent(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.linker.read_text()
        self.assertNotIn('search.paths += /system/${LIB}/arm/nb', text)
        self.assertNotIn('namespace.sphal.search.paths += /system/${LIB}/arm64', text)
        self.assertIn('namespace.sphal.permitted.paths += /system/${LIB}/arm64', text)
        self.assertIn('namespace.default.search.paths += /system/${LIB}/arm64', text)
        self.assertEqual(Path(str(self.linker) + ".vmctl-original").read_text(), self.original)
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual((self.root / "restart").read_text().splitlines(), ['ctl.restart media.swcodec'])

    def test_wrong_build_nonroot_and_wrong_graphics_do_not_write(self):
        for key, value in (("FINGERPRINT", "other"), ("TEST_UID", "2000"), ("GRALLOC", "gbm")):
            with self.subTest(key=key), mock.patch.dict(self.env, {key: value}):
                self.assertNotEqual(self.run_script().returncode, 0)
                self.assertEqual(self.linker.read_text(), self.original)
                self.assertFalse((self.root / "restart").exists())


if __name__ == "__main__":
    unittest.main()
