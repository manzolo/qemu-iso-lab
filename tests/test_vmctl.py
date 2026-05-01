import argparse
import importlib.machinery
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import io


ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "bin" / "vmctl"


def load_vmctl_module():
    loader = importlib.machinery.SourceFileLoader("vmctl_module", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class VmctlTests(unittest.TestCase):
    def setUp(self):
        self.vmctl = load_vmctl_module()
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.config_dir = self.root / "vms"
        self.original_root = self.vmctl.ROOT
        self.original_config_dir = self.vmctl.CONFIG_DIR
        self.vm_name = "testvm"
        self.vm_config = {
            "name": "Test VM",
            "iso": "isos/test.iso",
            "iso_url": "https://example.invalid/test.iso",
            "disk": {
                "path": "artifacts/testvm/disk.qcow2",
                "size": "1G",
                "format": "qcow2",
                "interface": "virtio",
            },
            "firmware": {"type": "bios"},
            "machine": "pc",
            "memory_mb": 1024,
            "cpus": 1,
            "network": "user",
            "audio": False,
            "video": {"default": "std", "variants": {"std": ["-vga", "std"]}},
        }
        self.vmctl.ROOT = self.root
        self.vmctl.CONFIG_DIR = self.config_dir
        self.write_config_dir()

    def tearDown(self):
        self.vmctl.ROOT = self.original_root
        self.vmctl.CONFIG_DIR = self.original_config_dir
        self.tempdir.cleanup()

    def create_disk(self):
        disk_path = self.root / self.vm_config["disk"]["path"]
        disk_path.parent.mkdir(parents=True, exist_ok=True)
        disk_path.write_text("disk", encoding="utf-8")
        return disk_path

    def write_config_dir(self):
        (self.config_dir / "profiles").mkdir(parents=True, exist_ok=True)
        (self.config_dir / "profiles" / "test.json").write_text(
            json.dumps({"vms": {self.vm_name: self.vm_config}}, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_extra_profile(self, filename: str, payload: dict) -> None:
        (self.config_dir / "profiles" / filename).write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_ensure_iso_skips_download_when_file_exists(self):
        iso_path = self.root / self.vm_config["iso"]
        iso_path.parent.mkdir(parents=True, exist_ok=True)
        iso_path.write_text("already here", encoding="utf-8")

        with mock.patch.object(self.vmctl, "download_file") as download_file:
            resolved = self.vmctl.ensure_iso(self.vm_config)

        self.assertEqual(resolved, iso_path)
        download_file.assert_not_called()

    def test_load_config_reads_profiles_from_config_dir(self):
        config = self.vmctl.load_config()

        self.assertIn(self.vm_name, config["vms"])
        self.assertNotIn("catalog", config)

    def test_load_config_reads_local_profile_override_file(self):
        local_vm = json.loads(json.dumps(self.vm_config))
        local_vm["name"] = "Local VM"
        local_vm["disk"]["path"] = "artifacts/localvm/disk.qcow2"
        self.write_extra_profile("local.json", {"vms": {"localvm": local_vm}})

        config = self.vmctl.load_config()

        self.assertIn("localvm", config["vms"])
        self.assertEqual(config["vms"]["localvm"]["name"], "Local VM")

    def test_cmd_list_includes_local_profile_override_file(self):
        local_vm = json.loads(json.dumps(self.vm_config))
        local_vm["name"] = "Local VM"
        local_vm["disk"]["path"] = "artifacts/localvm/disk.qcow2"
        self.write_extra_profile("local.json", {"vms": {"localvm": local_vm}})

        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_list(argparse.Namespace())

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("localvm", output)
        self.assertIn("Local VM", output)

    def test_cmd_status_reports_disk_iso_and_nvram_state(self):
        disk_path = self.create_disk()
        iso_path = self.root / self.vm_config["iso"]
        iso_path.parent.mkdir(parents=True, exist_ok=True)
        iso_path.write_text("iso", encoding="utf-8")
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        vars_path = self.root / self.vm_config["firmware"]["vars_path"]
        vars_path.parent.mkdir(parents=True, exist_ok=True)
        vars_path.write_text("vars", encoding="utf-8")
        args = argparse.Namespace(all=False)

        with mock.patch.object(self.vmctl.shutil, "which", return_value="/usr/bin/qemu-img"), \
             mock.patch.object(self.vmctl, "image_info", return_value={"virtual-size": 2 * 1024**3}), \
             mock.patch.object(self.vmctl, "vm_runtime_status", return_value=("-", "-")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_status(args)

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn(self.vm_name, output)
        self.assertIn("ready", output)
        self.assertIn(self.vmctl.format_bytes(disk_path.stat().st_size), output)
        self.assertIn(self.vmctl.format_bytes(2 * 1024**3), output)
        self.assertIn("RUNTIME", output)

    def test_cmd_status_hides_untouched_vms_by_default(self):
        other_vm = json.loads(json.dumps(self.vm_config))
        other_vm["disk"]["path"] = "artifacts/othervm/disk.qcow2"
        self.create_disk()
        (self.config_dir / "profiles" / "other.json").write_text(
            json.dumps({"vms": {"othervm": other_vm}}, indent=2) + "\n",
            encoding="utf-8",
        )

        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_status(argparse.Namespace(all=False))

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn(self.vm_name, output)
        self.assertNotIn("othervm", output)

    def test_cmd_status_reports_runtime_port_and_note(self):
        self.create_disk()
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.write_config_dir()

        with mock.patch.object(self.vmctl, "vm_runtime_status", return_value=("hostfwd:2222", "pid=4242")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_status(argparse.Namespace(all=False))

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("hostfwd:2222", output)
        self.assertIn("pid=4242", output)

    def test_cmd_status_handles_locked_disk_image_quietly(self):
        self.create_disk()
        self.write_config_dir()

        with mock.patch.object(self.vmctl.shutil, "which", return_value="/usr/bin/qemu-img"), \
             mock.patch.object(
                 self.vmctl,
                 "image_info",
                 side_effect=self.vmctl.subprocess.CalledProcessError(
                     1,
                     ["qemu-img", "info"],
                     stderr='qemu-img: Failed to get shared "write" lock',
                 ),
             ), \
             mock.patch.object(self.vmctl, "vm_runtime_status", return_value=("-", "-")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_status(argparse.Namespace(all=False))

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn(self.vm_name, output)
        self.assertIn("?", output)
        self.assertNotIn("Failed to get shared", output)

    def test_cmd_prep_downloads_iso_when_missing(self):
        args = argparse.Namespace(vm=self.vm_name, dry_run=False)

        with mock.patch.object(self.vmctl, "download_file") as download_file, \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_prep(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once()
        run_cmd.assert_called_once()
        qemu_img_cmd = run_cmd.call_args.args[0]
        self.assertEqual(qemu_img_cmd[:3], ["qemu-img", "create", "-f"])

    def test_download_file_sets_user_agent_header(self):
        destination = self.root / "isos" / "download.iso"

        class FakeResponse:
            def __init__(self):
                self._chunks = [b"payload", b""]
                self.headers = {"Content-Type": "application/octet-stream"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, size=-1):
                return self._chunks.pop(0)

        with mock.patch.object(self.vmctl.urllib.request, "urlopen", return_value=FakeResponse()) as urlopen_mock:
            self.vmctl.download_file("https://example.invalid/test.iso", destination)

        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.invalid/test.iso")
        self.assertEqual(request.headers["User-agent"], self.vmctl.HTTP_USER_AGENT)
        self.assertEqual(destination.read_bytes(), b"payload")

    def test_download_file_rejects_html_response(self):
        destination = self.root / "isos" / "fedora.iso"

        class FakeResponse:
            def __init__(self):
                self.headers = {"Content-Type": "text/html; charset=utf-8"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, size=-1):
                return b"<!doctype html><html></html>"

        with mock.patch.object(self.vmctl.urllib.request, "urlopen", return_value=FakeResponse()):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.download_file("https://example.invalid/fedora.iso", destination)

        self.assertFalse(destination.exists())

    def test_download_file_rejects_incomplete_response_and_keeps_existing_iso(self):
        destination = self.root / "isos" / "download.iso"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"old iso")

        class FakeResponse:
            def __init__(self):
                self._chunks = [b"partial", b""]
                self.headers = {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": "1024",
                }

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, size=-1):
                return self._chunks.pop(0)

        with mock.patch.object(self.vmctl.urllib.request, "urlopen", return_value=FakeResponse()):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.download_file("https://example.invalid/test.iso", destination)

        self.assertEqual(destination.read_bytes(), b"old iso")
        self.assertFalse(destination.with_name(destination.name + ".part").exists())

    def test_ensure_iso_removes_invalid_cached_html_and_redownloads(self):
        iso_path = self.root / self.vm_config["iso"]
        iso_path.parent.mkdir(parents=True, exist_ok=True)
        iso_path.write_text("<!doctype html><html></html>", encoding="utf-8")

        with mock.patch.object(self.vmctl, "download_file") as download_file:
            resolved = self.vmctl.ensure_iso(self.vm_config)

        self.assertEqual(resolved, iso_path)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=False, vm=self.vm_config)

    def test_ensure_iso_removes_cached_file_with_bad_size_and_redownloads(self):
        iso_path = self.root / self.vm_config["iso"]
        iso_path.parent.mkdir(parents=True, exist_ok=True)
        iso_path.write_bytes(b"partial")
        self.vm_config["iso_size"] = 1024

        with mock.patch.object(self.vmctl, "download_file") as download_file:
            resolved = self.vmctl.ensure_iso(self.vm_config)

        self.assertEqual(resolved, iso_path)
        self.assertFalse(iso_path.exists())
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=False, vm=self.vm_config)

    def test_ensure_iso_uses_discovered_url_before_hardcoded_fallback(self):
        iso_path = self.root / self.vm_config["iso"]
        self.vm_config["iso_discovery"] = {
            "index_url": "https://example.invalid/releases/",
            "pattern": r'href="(?P<url>test-[0-9]+\.iso)"',
        }

        with mock.patch.object(self.vmctl, "fetch_text", return_value='<a href="test-2.iso">test-2.iso</a>'), \
             mock.patch.object(self.vmctl, "download_file") as download_file:
            resolved = self.vmctl.ensure_iso(self.vm_config)

        self.assertEqual(resolved, iso_path)
        download_file.assert_called_once_with("https://example.invalid/releases/test-2.iso", iso_path, dry_run=False, vm=self.vm_config)

    def test_ensure_iso_dry_run_skips_remote_discovery(self):
        iso_path = self.root / self.vm_config["iso"]
        self.vm_config["iso_discovery"] = {
            "index_url": "https://example.invalid/releases/",
            "pattern": r'href="(?P<url>test-[0-9]+\.iso)"',
        }

        with mock.patch.object(self.vmctl, "fetch_text") as fetch_text, \
             mock.patch.object(self.vmctl, "download_file") as download_file:
            resolved = self.vmctl.ensure_iso(self.vm_config, dry_run=True)

        self.assertEqual(resolved, iso_path)
        fetch_text.assert_not_called()
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True, vm=self.vm_config)

    def test_ensure_iso_falls_back_to_hardcoded_url_when_discovered_url_fails(self):
        iso_path = self.root / self.vm_config["iso"]
        self.vm_config["iso_discovery"] = {
            "index_url": "https://example.invalid/releases/",
            "pattern": r'href="(?P<url>test-[0-9]+\.iso)"',
        }

        def fail_first(url, destination, dry_run=False, vm=None):
            if url.endswith("test-2.iso"):
                raise self.vmctl.VMError("mirror failed")

        with mock.patch.object(self.vmctl, "fetch_text", return_value='<a href="test-2.iso">test-2.iso</a>'), \
             mock.patch.object(self.vmctl, "download_file", side_effect=fail_first) as download_file:
            resolved = self.vmctl.ensure_iso(self.vm_config)

        self.assertEqual(resolved, iso_path)
        self.assertEqual(download_file.call_args_list[0].args[:2], ("https://example.invalid/releases/test-2.iso", iso_path))
        self.assertEqual(download_file.call_args_list[1].args[:2], (self.vm_config["iso_url"], iso_path))

    def test_ensure_iso_falls_back_to_hardcoded_url_when_discovery_index_fails(self):
        iso_path = self.root / self.vm_config["iso"]
        self.vm_config["iso_discovery"] = {
            "index_url": "https://example.invalid/releases/",
            "pattern": r'href="(?P<url>test-[0-9]+\.iso)"',
        }

        with mock.patch.object(self.vmctl, "fetch_text", side_effect=self.vmctl.VMError("index failed")), \
             mock.patch.object(self.vmctl, "download_file") as download_file:
            resolved = self.vmctl.ensure_iso(self.vm_config)

        self.assertEqual(resolved, iso_path)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=False, vm=self.vm_config)

    def test_cmd_prep_fails_without_iso_url(self):
        self.vm_config.pop("iso_url")
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, dry_run=False)

        with mock.patch.object(self.vmctl, "require_command"):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.cmd_prep(args)

    def test_cmd_provision_dry_run_prepares_and_starts_installer(self):
        iso_path = self.root / self.vm_config["iso"]
        args = argparse.Namespace(vm=self.vm_name, video="std", no_start=False, dry_run=True)

        with mock.patch.object(self.vmctl, "download_file") as download_file, \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_provision(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True, vm=self.vm_config)
        self.assertEqual(run_cmd.call_count, 2)
        qemu_img_cmd = run_cmd.call_args_list[0].args[0]
        qemu_cmd = run_cmd.call_args_list[1].args[0]
        self.assertEqual(qemu_img_cmd[:3], ["qemu-img", "create", "-f"])
        self.assertEqual(qemu_cmd[0], "qemu-system-x86_64")
        self.assertIn("-cdrom", qemu_cmd)
        self.assertEqual(qemu_cmd[qemu_cmd.index("-cdrom") + 1], str(iso_path))
        self.assertIn(f"file={self.root / self.vm_config['disk']['path']},format=qcow2,if=virtio", qemu_cmd)

    def test_cmd_provision_no_start_only_prepares_artifacts(self):
        iso_path = self.root / self.vm_config["iso"]
        args = argparse.Namespace(vm=self.vm_name, video=None, no_start=True, dry_run=True)

        with mock.patch.object(self.vmctl, "download_file") as download_file, \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_provision(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True, vm=self.vm_config)
        run_cmd.assert_called_once()
        self.assertEqual(run_cmd.call_args.args[0][:3], ["qemu-img", "create", "-f"])

    def test_cmd_install_dry_run_builds_qemu_command_with_cdrom(self):
        disk_path = self.create_disk()
        iso_path = self.root / self.vm_config["iso"]
        args = argparse.Namespace(vm=self.vm_name, video="std", cloud_init=False, dry_run=True)

        with mock.patch.object(self.vmctl, "download_file") as download_file, \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True, vm=self.vm_config)
        run_cmd.assert_called_once()
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertEqual(run_cmd.call_args.kwargs["dry_run"], True)
        self.assertEqual(qemu_cmd[0], "qemu-system-x86_64")
        self.assertIn(f"file={disk_path},format=qcow2,if=virtio", qemu_cmd)
        self.assertIn("-cdrom", qemu_cmd)
        self.assertEqual(qemu_cmd[qemu_cmd.index("-cdrom") + 1], str(iso_path))
        self.assertIn("-vga", qemu_cmd)
        self.assertIn("std", qemu_cmd)

    def test_cmd_install_defaults_to_safe_video_for_non_ubuntu_installer(self):
        self.create_disk()
        self.vm_config["video"]["default"] = "virtio-gl"
        self.vm_config["video"]["variants"]["safe"] = ["-vga", "std", "-display", "gtk", "-serial", "mon:stdio"]
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video=None, cloud_init=False, dry_run=True)

        with mock.patch.object(self.vmctl, "download_file"), \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-vga", qemu_cmd)
        self.assertIn("std", qemu_cmd)
        self.assertIn("-serial", qemu_cmd)
        self.assertNotIn("virtio-vga-gl", qemu_cmd)

    def test_cmd_install_defaults_to_std_video_for_ubuntu_installer(self):
        self.create_disk()
        self.vm_config["meta"] = {"slug": "ubuntu"}
        self.vm_config["video"]["default"] = "virtio-gl"
        self.vm_config["video"]["variants"]["safe"] = ["-vga", "std", "-display", "gtk", "-serial", "mon:stdio"]
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video=None, cloud_init=False, dry_run=True)

        with mock.patch.object(self.vmctl, "download_file"), \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-vga", qemu_cmd)
        self.assertIn("std", qemu_cmd)
        self.assertNotIn("-serial", qemu_cmd)
        self.assertNotIn("virtio-vga-gl", qemu_cmd)

    def test_cmd_flash_requires_matching_confirmation(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdy", dry_run=True)

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.cmd_flash(args)

    def test_cmd_flash_rejects_non_empty_device(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=False, dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(
                 self.vmctl,
                 "validate_flash_target",
                 side_effect=self.vmctl.VMError("Refusing non-empty target device '/dev/sdz' with signatures: gpt"),
             ):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.cmd_flash(args)

    def test_cmd_flash_rejects_efi_image_without_gpt(self):
        self.create_disk()
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=False, dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(
                 self.vmctl,
                 "validate_flash_target",
                 side_effect=self.vmctl.VMError("EFI guest requires a GPT VM disk before flash; detected: dos"),
             ):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.cmd_flash(args)

    def test_cmd_flash_allows_unknown_layout_for_efi_qcow2(self):
        self.create_disk()
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=True, dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(
                 self.vmctl,
                 "validate_flash_target",
                 return_value=(
                     {
                         "path": "/dev/sdz",
                         "size": 16 * 1024**3,
                         "model": "USB",
                         "mountpoints": [],
                         "children": [{"path": "/dev/sdz1"}],
                         "signatures": [{"type": "gpt"}],
                         "is_root_disk": False,
                         "is_empty": False,
                     },
                     None,
                     1 * 1024**3,
                 ),
             ), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_flash(args)

        self.assertEqual(exit_code, 0)
        run_cmd.assert_called_once()

    def test_cmd_flash_dry_run_builds_qemu_img_convert(self):
        disk_path = self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=False, dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(
                 self.vmctl,
                 "validate_flash_target",
                 return_value=(
                     {
                         "path": "/dev/sdz",
                         "size": 16 * 1024**3,
                         "model": "USB",
                         "mountpoints": [],
                         "children": [],
                         "signatures": [],
                         "is_root_disk": False,
                         "is_empty": True,
                     },
                     "dos",
                     1 * 1024**3,
                 ),
             ), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_flash(args)

        self.assertEqual(exit_code, 0)
        run_cmd.assert_called_once()
        helper_cmd = run_cmd.call_args.args[0]
        self.assertEqual(
            helper_cmd,
            [
                "sudo",
                str(self.vmctl.Path(self.vmctl.__file__).resolve()),
                "flash-helper",
                "--vm",
                self.vm_name,
                "--device",
                "/dev/sdz",
                "--confirm-device",
                "/dev/sdz",
            ],
        )

    def test_cmd_flash_force_target_passes_flag_to_helper(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=True, dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(
                 self.vmctl,
                 "validate_flash_target",
                 return_value=(
                     {
                         "path": "/dev/sdz",
                         "size": 16 * 1024**3,
                         "model": "USB",
                         "mountpoints": [],
                         "children": [{"path": "/dev/sdz1"}],
                         "signatures": [{"type": "gpt"}],
                         "is_root_disk": False,
                         "is_empty": False,
                     },
                     "dos",
                     1 * 1024**3,
                 ),
             ), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_flash(args)

        self.assertEqual(exit_code, 0)
        helper_cmd = run_cmd.call_args.args[0]
        self.assertIn("--force-target", helper_cmd)

    def test_cmd_flash_helper_runs_convert_on_existing_device(self):
        disk_path = self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=False)

        with mock.patch.object(self.vmctl.os, "geteuid", return_value=0), \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(
                 self.vmctl,
                 "validate_flash_target",
                 return_value=(
                     {
                         "path": "/dev/sdz",
                         "size": 16 * 1024**3,
                         "model": "USB",
                         "mountpoints": [],
                         "children": [],
                         "signatures": [],
                         "is_root_disk": False,
                         "is_empty": True,
                     },
                     "dos",
                     1 * 1024**3,
                 ),
             ), \
             mock.patch.object(
                 self.vmctl,
                 "inspect_block_device",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 16 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [],
                     "signatures": [],
                     "is_root_disk": False,
                     "is_empty": True,
                 },
             ), \
             mock.patch.object(
                 self.vmctl,
                 "inspect_block_device_basic",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 16 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [],
                     "logical_sector_size": 512,
                     "pttype": "dos",
                     "is_root_disk": False,
                 },
             ), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_flash_helper(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(run_cmd.call_count, 3)
        convert_cmd = run_cmd.call_args_list[0].args[0]
        self.assertEqual(
            convert_cmd,
            ["qemu-img", "convert", "-n", "-p", "-f", "qcow2", "-O", "raw", str(disk_path), "/dev/sdz"],
        )

    def test_cmd_flash_helper_relocates_gpt_after_writing_to_larger_disk(self):
        disk_path = self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=False)

        with mock.patch.object(self.vmctl.os, "geteuid", return_value=0), \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(
                 self.vmctl,
                 "validate_flash_target",
                 return_value=(
                     {
                         "path": "/dev/sdz",
                         "size": 16 * 1024**3,
                         "model": "USB",
                         "mountpoints": [],
                         "children": [],
                         "signatures": [],
                         "is_root_disk": False,
                         "is_empty": True,
                     },
                     "gpt",
                     1 * 1024**3,
                 ),
             ), \
             mock.patch.object(
                 self.vmctl,
                 "inspect_block_device_basic",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 16 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [],
                     "logical_sector_size": 512,
                     "pttype": "gpt",
                     "is_root_disk": False,
                 },
             ), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_flash_helper(args)

        self.assertEqual(exit_code, 0)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(
            executed[0],
            ["qemu-img", "convert", "-n", "-p", "-f", "qcow2", "-O", "raw", str(disk_path), "/dev/sdz"],
        )
        self.assertEqual(executed[1], ["blockdev", "--rereadpt", "/dev/sdz"])
        self.assertEqual(executed[2], ["sgdisk", "-e", "/dev/sdz"])
        self.assertEqual(executed[3], ["blockdev", "--rereadpt", "/dev/sdz"])
        self.assertEqual(executed[4], ["sync"])

    def test_cmd_flash_helper_force_target_wipes_signatures_first(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=True)

        with mock.patch.object(self.vmctl.os, "geteuid", return_value=0), \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(
                 self.vmctl,
                 "validate_flash_target",
                 return_value=(
                     {
                         "path": "/dev/sdz",
                         "size": 16 * 1024**3,
                         "model": "USB",
                         "mountpoints": [],
                         "children": [{"path": "/dev/sdz1"}, {"path": "/dev/sdz2"}],
                         "signatures": [{"type": "gpt"}],
                         "is_root_disk": False,
                         "is_empty": False,
                     },
                     "dos",
                     1 * 1024**3,
                 ),
             ), \
             mock.patch.object(
                 self.vmctl,
                 "inspect_block_device_basic",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 16 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [],
                     "logical_sector_size": 512,
                     "pttype": "dos",
                     "is_root_disk": False,
                 },
             ), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_flash_helper(args)

        self.assertEqual(exit_code, 0)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(executed[0], ["wipefs", "-a", "-f", "/dev/sdz1"])
        self.assertEqual(executed[1], ["wipefs", "-a", "-f", "/dev/sdz2"])
        self.assertEqual(executed[2], ["wipefs", "-a", "-f", "/dev/sdz"])
        self.assertEqual(executed[3], ["blockdev", "--rereadpt", "/dev/sdz"])

    def test_cmd_flash_helper_continues_when_rereadpt_fails(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=True)

        def fake_run(cmd, dry_run=False, quiet=False):
            if cmd == ["blockdev", "--rereadpt", "/dev/sdz"]:
                raise self.vmctl.subprocess.CalledProcessError(1, cmd)

        with mock.patch.object(self.vmctl.os, "geteuid", return_value=0), \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(
                 self.vmctl,
                 "validate_flash_target",
                 return_value=(
                     {
                         "path": "/dev/sdz",
                         "size": 16 * 1024**3,
                         "model": "USB",
                         "mountpoints": [],
                         "children": [{"path": "/dev/sdz1"}],
                         "signatures": [{"type": "gpt"}],
                         "is_root_disk": False,
                         "is_empty": False,
                     },
                     "dos",
                     1 * 1024**3,
                 ),
             ), \
             mock.patch.object(
                 self.vmctl,
                 "inspect_block_device_basic",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 16 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [],
                     "logical_sector_size": 512,
                     "pttype": "dos",
                     "is_root_disk": False,
                 },
             ), \
             mock.patch.object(self.vmctl, "run", side_effect=fake_run) as run_cmd, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_flash_helper(args)

        self.assertEqual(exit_code, 0)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertIn(["blockdev", "--rereadpt", "/dev/sdz"], executed)
        self.assertIn(["sync"], executed)
        self.assertIn("Kernel did not reread the partition table", stdout.getvalue())

    def test_cmd_import_device_requires_matching_confirmation(self):
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdy", dry_run=True)

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.cmd_import_device(args)

    def test_cmd_import_device_dry_run_builds_helper_command(self):
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(
                 self.vmctl,
                 "validate_import_source",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 16 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [],
                     "logical_sector_size": 512,
                     "pttype": None,
                     "is_root_disk": False,
                 },
             ), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_import_device(args)

        self.assertEqual(exit_code, 0)
        helper_cmd = run_cmd.call_args.args[0]
        self.assertEqual(
            helper_cmd,
            [
                "sudo",
                str(self.vmctl.Path(self.vmctl.__file__).resolve()),
                "import-helper",
                "--vm",
                self.vm_name,
                "--device",
                "/dev/sdz",
                "--confirm-device",
                "/dev/sdz",
            ],
        )

    def test_cmd_import_helper_runs_bounded_pipeline_for_dos_disk(self):
        disk_path = self.root / self.vm_config["disk"]["path"]
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz")

        with mock.patch.object(self.vmctl.os, "geteuid", return_value=0), \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(
                 self.vmctl,
                 "validate_import_source",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 16 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [{"type": "part", "start": 2048, "size": 4 * 1024**3}],
                     "logical_sector_size": 512,
                     "pttype": "dos",
                     "is_root_disk": False,
                 },
             ), \
             mock.patch.object(self.vmctl, "run") as run_cmd, \
             mock.patch.object(self.vmctl, "run_progress") as run_progress, \
             mock.patch.object(self.vmctl, "run_pipeline") as run_pipeline, \
             mock.patch.object(self.vmctl, "maybe_restore_sudo_owner") as restore_owner, \
             mock.patch.object(self.vmctl, "maybe_restore_sudo_owner_tree") as restore_tree:
            exit_code = self.vmctl.cmd_import_helper(args)

        self.assertEqual(exit_code, 0)
        run_cmd.assert_not_called()
        run_progress.assert_not_called()
        run_pipeline.assert_called_once_with(
            [
                [
                    "dd",
                    "if=/dev/sdz",
                    "iflag=fullblock,count_bytes",
                    f"count={self.vmctl.round_up((2048 * 512) + (4 * 1024**3), 1024**2)}",
                    "bs=4M",
                    "status=progress",
                ],
                ["qemu-img", "convert", "-p", "-f", "raw", "-O", "qcow2", "-", str(disk_path)],
            ],
            dry_run=False,
        )
        restore_owner.assert_called_once_with(disk_path)
        restore_tree.assert_called_once_with(disk_path.parent)

    def test_cmd_import_helper_compacts_gpt_and_repairs_backup_header(self):
        disk_path = self.root / self.vm_config["disk"]["path"]
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz")
        temp_root = self.root / "tmp-import"

        with mock.patch.object(self.vmctl.os, "geteuid", return_value=0), \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(
                 self.vmctl,
                 "validate_import_source",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 115 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [{"type": "part", "start": 2048, "size": 32 * 1024**3}],
                     "logical_sector_size": 512,
                     "pttype": "gpt",
                     "is_root_disk": False,
                 },
             ), \
             mock.patch.object(self.vmctl, "maybe_read_gpt_geometry", return_value={}), \
             mock.patch.object(self.vmctl, "run") as run_cmd, \
             mock.patch.object(self.vmctl, "run_progress") as run_progress, \
             mock.patch.object(self.vmctl.tempfile, "mkdtemp", return_value=str(temp_root)), \
             mock.patch.object(self.vmctl, "run_pipeline") as run_pipeline, \
             mock.patch.object(self.vmctl, "maybe_restore_sudo_owner") as restore_owner, \
             mock.patch.object(self.vmctl, "maybe_restore_sudo_owner_tree") as restore_tree:
            exit_code = self.vmctl.cmd_import_helper(args)

        self.assertEqual(exit_code, 0)
        expected_count = self.vmctl.round_up((2048 * 512) + (32 * 1024**3) + (33 * 512), 1024**2)
        temp_raw = temp_root / "source.raw"
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(
            executed[0],
            [
                "dd",
                "if=/dev/sdz",
                f"of={temp_raw}",
                "iflag=fullblock,count_bytes",
                f"count={expected_count}",
                "bs=4M",
                "conv=sparse",
                "status=progress",
            ],
        )
        self.assertEqual(executed[1], ["sgdisk", "-e", str(temp_raw)])
        self.assertEqual(executed[2], ["sgdisk", "-v", str(temp_raw)])
        run_progress.assert_called_once_with(
            ["qemu-img", "convert", "-p", "-f", "raw", "-O", "qcow2", str(temp_raw), str(disk_path)],
            dry_run=False,
        )
        run_pipeline.assert_not_called()
        restore_owner.assert_called_once_with(disk_path)
        restore_tree.assert_called_once_with(disk_path.parent)

    def test_cmd_import_helper_uses_real_gpt_geometry_for_compaction(self):
        disk_path = self.root / self.vm_config["disk"]["path"]
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz")
        temp_root = self.root / "tmp-import-geometry"

        with mock.patch.object(self.vmctl.os, "geteuid", return_value=0), \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(
                 self.vmctl,
                 "validate_import_source",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 250 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [{"type": "part", "start": 2048, "size": 5 * 1024**3}],
                     "logical_sector_size": 512,
                     "pttype": "gpt",
                     "is_root_disk": False,
                 },
             ), \
             mock.patch.object(
                 self.vmctl,
                 "maybe_read_gpt_geometry",
                 return_value={
                     "gpt_partition_entry_count": 1024,
                     "gpt_partition_entry_size": 128,
                     "gpt_first_usable_lba": 34,
                 },
             ) as read_geometry, \
             mock.patch.object(self.vmctl, "run") as run_cmd, \
             mock.patch.object(self.vmctl, "run_progress") as run_progress, \
             mock.patch.object(self.vmctl.tempfile, "mkdtemp", return_value=str(temp_root)), \
             mock.patch.object(self.vmctl, "run_pipeline") as run_pipeline, \
             mock.patch.object(self.vmctl, "maybe_restore_sudo_owner") as restore_owner, \
             mock.patch.object(self.vmctl, "maybe_restore_sudo_owner_tree") as restore_tree:
            exit_code = self.vmctl.cmd_import_helper(args)

        self.assertEqual(exit_code, 0)
        read_geometry.assert_called_once_with("/dev/sdz", 512)
        backup_bytes = 512 * (1 + ((1024 * 128) // 512))
        expected_count = self.vmctl.round_up((2048 * 512) + (5 * 1024**3) + backup_bytes, 1024**2)
        temp_raw = temp_root / "source.raw"
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(
            executed[0],
            [
                "dd",
                "if=/dev/sdz",
                f"of={temp_raw}",
                "iflag=fullblock,count_bytes",
                f"count={expected_count}",
                "bs=4M",
                "conv=sparse",
                "status=progress",
            ],
        )
        self.assertEqual(executed[1], ["sgdisk", "-e", str(temp_raw)])
        self.assertEqual(executed[2], ["sgdisk", "-v", str(temp_raw)])
        run_progress.assert_called_once_with(
            ["qemu-img", "convert", "-p", "-f", "raw", "-O", "qcow2", str(temp_raw), str(disk_path)],
            dry_run=False,
        )
        run_pipeline.assert_not_called()
        restore_owner.assert_called_once_with(disk_path)
        restore_tree.assert_called_once_with(disk_path.parent)

    def test_validate_import_source_rejects_mounted_disk(self):
        with mock.patch.object(
            self.vmctl,
            "inspect_block_device_basic",
            return_value={
                "path": "/dev/sdz",
                "size": 16 * 1024**3,
                "model": "USB",
                "mountpoints": ["/media/usb"],
                "children": [],
                "logical_sector_size": 512,
                "pttype": None,
                "is_root_disk": False,
            },
        ):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.validate_import_source("/dev/sdz")

    def test_ensure_vm_dirs_wraps_permission_errors(self):
        with mock.patch.object(self.vmctl.Path, "mkdir", side_effect=PermissionError("denied")):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.ensure_vm_dirs(self.vm_name)

    def test_suggested_import_bytes_trims_trailing_space_for_dos(self):
        import_bytes, compacted = self.vmctl.suggested_import_bytes(
            {
                "size": 250 * 1024**3,
                "pttype": "dos",
                "logical_sector_size": 512,
                "children": [
                    {"type": "part", "start": 2048, "size": 5 * 1024**3},
                    {"type": "part", "start": 6 * 1024**3 // 512, "size": 2 * 1024**3},
                ],
            }
        )

        self.assertEqual(import_bytes, 8 * 1024**3)
        self.assertEqual(compacted, True)

    def test_suggested_import_bytes_compacts_gpt_and_keeps_backup_room(self):
        import_bytes, compacted = self.vmctl.suggested_import_bytes(
            {
                "size": 250 * 1024**3,
                "pttype": "gpt",
                "logical_sector_size": 512,
                "children": [{"type": "part", "start": 2048, "size": 5 * 1024**3}],
            }
        )

        self.assertEqual(import_bytes, self.vmctl.round_up((2048 * 512) + (5 * 1024**3) + (33 * 512), 1024**2))
        self.assertEqual(compacted, True)

    def test_suggested_import_bytes_uses_actual_gpt_entry_array_size(self):
        import_bytes, compacted = self.vmctl.suggested_import_bytes(
            {
                "size": 250 * 1024**3,
                "pttype": "gpt",
                "logical_sector_size": 512,
                "gpt_partition_entry_count": 1024,
                "gpt_partition_entry_size": 128,
                "gpt_first_usable_lba": 34,
                "children": [{"type": "part", "start": 2048, "size": 5 * 1024**3}],
            }
        )

        backup_bytes = 512 * (1 + ((1024 * 128) // 512))
        self.assertEqual(import_bytes, self.vmctl.round_up((2048 * 512) + (5 * 1024**3) + backup_bytes, 1024**2))
        self.assertEqual(compacted, True)

    def test_suggested_import_bytes_uses_first_usable_lba_for_gpt_tail_gap(self):
        import_bytes, compacted = self.vmctl.suggested_import_bytes(
            {
                "size": 250 * 1024**3,
                "pttype": "gpt",
                "logical_sector_size": 512,
                "gpt_partition_entry_count": 128,
                "gpt_partition_entry_size": 128,
                "gpt_first_usable_lba": 2048,
                "children": [
                    {"type": "part", "start": 4096, "size": 4 * 1024**3},
                    {"type": "part", "start": 8392704, "size": 58716088 * 512},
                ],
            }
        )

        backup_bytes = (2048 - 1) * 512
        last_partition_end = (8392704 * 512) + (58716088 * 512)
        self.assertEqual(import_bytes, self.vmctl.round_up(last_partition_end + backup_bytes, 1024**2))
        self.assertEqual(compacted, True)

    def test_maybe_read_gpt_geometry_reads_entry_array_metadata(self):
        sector_size = 512
        disk_path = self.root / "gpt-header.img"
        header = bytearray(sector_size * 2)
        header[sector_size:sector_size + 8] = b"EFI PART"
        header[sector_size + 40:sector_size + 48] = (2048).to_bytes(8, "little")
        header[sector_size + 80:sector_size + 84] = (1024).to_bytes(4, "little")
        header[sector_size + 84:sector_size + 88] = (128).to_bytes(4, "little")
        disk_path.write_bytes(header)

        geometry = self.vmctl.maybe_read_gpt_geometry(str(disk_path), sector_size)

        self.assertEqual(
            geometry,
            {
                "gpt_first_usable_lba": 2048,
                "gpt_partition_entry_count": 1024,
                "gpt_partition_entry_size": 128,
            },
        )

    def test_cmd_start_dry_run_builds_qemu_command_without_cdrom(self):
        disk_path = self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, video=None, cloud_init=False, headless=False, background=False, dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_start(args)

        self.assertEqual(exit_code, 0)
        run_cmd.assert_called_once()
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertEqual(run_cmd.call_args.kwargs["dry_run"], True)
        self.assertEqual(qemu_cmd[0], "qemu-system-x86_64")
        self.assertIn(f"file={disk_path},format=qcow2,if=virtio", qemu_cmd)
        self.assertNotIn("-cdrom", qemu_cmd)
        self.assertIn("-netdev", qemu_cmd)
        self.assertIn("user,id=n1", qemu_cmd)

    def test_cmd_start_headless_dry_run_uses_no_display(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, video="std", cloud_init=False, headless=True, background=False, dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_start(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-display", qemu_cmd)
        self.assertIn("none", qemu_cmd)
        self.assertIn("-monitor", qemu_cmd)
        self.assertNotIn("-vga", qemu_cmd)

    def test_cmd_start_headless_background_tracks_pid_and_log(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, video=None, cloud_init=False, headless=True, background=True, dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run_background", return_value=None) as run_background:
            exit_code = self.vmctl.cmd_start(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_background.call_args.args[0]
        log_path = run_background.call_args.args[1]
        self.assertIn("-display", qemu_cmd)
        self.assertIn("none", qemu_cmd)
        self.assertEqual(log_path, self.root / "artifacts/testvm/logs/bootstrap-start.log")

    def test_cmd_start_spice_background_tracks_pid_and_log(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, video=None, cloud_init=False, headless=False, background=True, spice_port=5930, dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run_background", return_value=None) as run_background:
            exit_code = self.vmctl.cmd_start(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_background.call_args.args[0]
        self.assertIn("-spice", qemu_cmd)
        self.assertIn("addr=127.0.0.1,port=5930,disable-ticketing=on", qemu_cmd)
        self.assertIn("virtserialport,chardev=vdagent0,name=com.redhat.spice.0", qemu_cmd)
        self.assertIn("-display", qemu_cmd)
        self.assertIn("none", qemu_cmd)

    def test_cmd_start_background_requires_headless(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, video=None, cloud_init=False, headless=False, background=True, dry_run=True)

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.cmd_start(args)

    def test_common_args_tcg_headless_serial(self):
        disk_path = self.create_disk()

        with mock.patch.object(self.vmctl, "require_command"):
            qemu_cmd = self.vmctl.common_args(
                self.vm_config,
                variant=None,
                dry_run=True,
                accel="tcg",
                headless=True,
                serial_stdio=True,
                no_reboot=True,
            )

        self.assertEqual(qemu_cmd[0], "qemu-system-x86_64")
        self.assertNotIn("-enable-kvm", qemu_cmd)
        self.assertIn("pc,accel=tcg", qemu_cmd)
        self.assertIn(f"file={disk_path},format=qcow2,if=virtio", qemu_cmd)
        self.assertIn("-display", qemu_cmd)
        self.assertIn("none", qemu_cmd)
        self.assertIn("-serial", qemu_cmd)
        self.assertIn("-no-reboot", qemu_cmd)

    def test_common_args_honors_custom_network_device(self):
        self.create_disk()
        self.vm_config["network_device"] = "e1000e"

        with mock.patch.object(self.vmctl, "require_command"):
            qemu_cmd = self.vmctl.common_args(self.vm_config, variant=None, dry_run=True)

        self.assertIn("e1000e,netdev=n1", qemu_cmd)

    def test_common_args_adds_hostfwd_for_cloud_init_ssh(self):
        self.create_disk()
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}

        with mock.patch.object(self.vmctl, "require_command"):
            qemu_cmd = self.vmctl.common_args(self.vm_config, variant=None, dry_run=True)

        self.assertIn("user,id=n1,hostfwd=tcp:127.0.0.1:2222-:22", qemu_cmd)

    def test_common_args_adds_hostfwd_for_ssh_provision(self):
        self.create_disk()
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2223}

        with mock.patch.object(self.vmctl, "require_command"):
            qemu_cmd = self.vmctl.common_args(self.vm_config, variant=None, dry_run=True)

        self.assertIn("user,id=n1,hostfwd=tcp:127.0.0.1:2223-:22", qemu_cmd)

    def test_common_args_supports_sata_disk_interface(self):
        disk_path = self.create_disk()
        self.vm_config["disk"]["interface"] = "sata"

        with mock.patch.object(self.vmctl, "require_command"):
            qemu_cmd = self.vmctl.common_args(self.vm_config, variant=None, dry_run=True)

        self.assertIn("ich9-ahci,id=ahci0", qemu_cmd)
        self.assertIn(f"id=disk0,file={disk_path},format=qcow2,if=none", qemu_cmd)
        self.assertIn("ide-hd,drive=disk0,bus=ahci0.0", qemu_cmd)

    def test_common_args_enables_clipboard_agent_for_graphical_vm(self):
        self.create_disk()
        self.vm_config["clipboard"] = True

        with mock.patch.object(self.vmctl, "require_command"):
            qemu_cmd = self.vmctl.common_args(self.vm_config, variant="std", dry_run=True)

        self.assertIn("virtio-serial-pci", qemu_cmd)
        self.assertIn("qemu-vdagent,id=vdagent0,name=vdagent,clipboard=on", qemu_cmd)
        self.assertIn("virtserialport,chardev=vdagent0,name=com.redhat.spice.0", qemu_cmd)

    def test_common_args_can_disable_clipboard_agent(self):
        self.create_disk()
        self.vm_config["clipboard"] = True

        with mock.patch.object(self.vmctl, "require_command"):
            qemu_cmd = self.vmctl.common_args(
                self.vm_config,
                variant="std",
                dry_run=True,
                enable_clipboard=False,
            )

        self.assertNotIn("virtio-serial-pci", qemu_cmd)
        self.assertNotIn("qemu-vdagent,id=vdagent0,name=vdagent,clipboard=on", qemu_cmd)
        self.assertNotIn("virtserialport,chardev=vdagent0,name=com.redhat.spice.0", qemu_cmd)

    def test_cmd_boot_check_dry_run_uses_ci_settings(self):
        self.create_disk()
        self.vm_config["ci"] = {
            "accel": "tcg",
            "headless": True,
            "boot_from": "cdrom",
            "auto_input": [{"match": "boot:", "send": "\r"}, {"match": "boot:", "send": "\n"}],
            "expect": "login:",
            "timeout_sec": 180,
        }
        self.write_config_dir()
        iso_path = self.root / self.vm_config["iso"]
        args = argparse.Namespace(vm=self.vm_name, expect=None, timeout=None, dry_run=True)

        with mock.patch.object(self.vmctl, "download_file") as download_file, \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run_and_expect") as run_and_expect:
            exit_code = self.vmctl.cmd_boot_check(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True, vm=self.vm_config)
        run_and_expect.assert_called_once()
        qemu_cmd, expected_text, timeout_sec = run_and_expect.call_args.args
        self.assertIn("-cdrom", qemu_cmd)
        self.assertEqual(qemu_cmd[qemu_cmd.index("-cdrom") + 1], str(iso_path))
        self.assertEqual(expected_text, "login:")
        self.assertEqual(timeout_sec, 180)
        self.assertEqual(
            run_and_expect.call_args.kwargs["auto_inputs"],
            [("boot:", "\r"), ("boot:", "\n")],
        )

    def test_create_cloud_init_seed_writes_artifacts_and_runs_cloud_localds(self):
        pubkey = self.root / ".ssh" / "id_ed25519.pub"
        pubkey.parent.mkdir(parents=True, exist_ok=True)
        pubkey.write_text("ssh-ed25519 AAAA from-file\n", encoding="utf-8")
        self.vm_config["cloud_init"] = {
            "hostname": "testvm",
            "user": "tester",
            "ssh_authorized_keys": ["ssh-ed25519 AAAA test"],
            "ssh_authorized_keys_file": str(pubkey),
            "packages": ["niri"],
            "runcmd": ["echo ready"],
        }

        with mock.patch.object(self.vmctl.shutil, "which", side_effect=lambda name: "/usr/bin/cloud-localds" if name == "cloud-localds" else None), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            seed_path = self.vmctl.create_cloud_init_seed(self.vm_name, self.vm_config)

        self.assertEqual(seed_path, self.root / "artifacts/testvm/cloud-init/seed.iso")
        user_data = (self.root / "artifacts/testvm/cloud-init/user-data").read_text(encoding="utf-8")
        self.assertIn("#cloud-config", user_data)
        self.assertIn("ssh-ed25519 AAAA test", user_data)
        self.assertIn("ssh-ed25519 AAAA from-file", user_data)
        self.assertIn('"local-hostname": "testvm"', (self.root / "artifacts/testvm/cloud-init/meta-data").read_text(encoding="utf-8"))
        self.assertEqual(
            run_cmd.call_args.args[0],
            [
                "cloud-localds",
                str(self.root / "artifacts/testvm/cloud-init/seed.iso"),
                str(self.root / "artifacts/testvm/cloud-init/user-data"),
                str(self.root / "artifacts/testvm/cloud-init/meta-data"),
            ],
        )

    def test_collect_ssh_authorized_keys_requires_existing_file(self):
        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.collect_ssh_authorized_keys({"ssh_authorized_keys_file": str(self.root / "missing.pub")})

    def test_collect_ssh_authorized_keys_allows_missing_file_in_dry_run_mode(self):
        keys = self.vmctl.collect_ssh_authorized_keys(
            {"ssh_authorized_keys_file": str(self.root / "missing.pub")},
            allow_missing_file=True,
        )

        self.assertEqual(keys, [])

    def test_ssh_base_cmd_allows_missing_private_key_in_dry_run_mode(self):
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
            "ssh_key": str(self.root / "missing-key"),
        }

        cmd = self.vmctl.ssh_base_cmd(self.vm_config, dry_run=True)

        self.assertEqual(cmd[0], "ssh")
        self.assertNotIn("-i", cmd)

    def test_ssh_base_cmd_requires_existing_private_key_when_not_dry_run(self):
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
            "ssh_key": str(self.root / "missing-key"),
        }

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.ssh_base_cmd(self.vm_config, dry_run=False)

    def test_ssh_shell_cmd_omits_batch_mode(self):
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
        }

        cmd = self.vmctl.ssh_shell_cmd(self.vm_config, dry_run=True)

        self.assertEqual(cmd[0], "ssh")
        self.assertEqual(cmd[1:3], ["-F", "/dev/null"])
        self.assertNotIn("BatchMode=yes", cmd)
        self.assertEqual(cmd[-1], "tester@127.0.0.1")

    def test_wait_for_ssh_retries_until_probe_succeeds(self):
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
        }
        self.write_config_dir()

        results = [
            self.vmctl.subprocess.CompletedProcess(args=["ssh"], returncode=255),
            self.vmctl.subprocess.CompletedProcess(args=["ssh"], returncode=0),
        ]
        with mock.patch.object(self.vmctl.subprocess, "run", side_effect=results) as run_cmd, \
             mock.patch.object(self.vmctl.time, "sleep") as sleep_mock:
            self.vmctl.wait_for_ssh(self.vm_config, timeout_sec=10, dry_run=False)

        self.assertEqual(run_cmd.call_count, 2)
        self.assertEqual(run_cmd.call_args_list[0].args[0][-1], "true")
        sleep_mock.assert_called_once_with(2)

    def test_cmd_start_cloud_init_attaches_seed_drive(self):
        self.create_disk()
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video=None, cloud_init=True, headless=False, background=False, dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "create_cloud_init_seed", return_value=self.root / "artifacts/testvm/cloud-init/seed.iso") as create_seed, \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_start(args)

        self.assertEqual(exit_code, 0)
        create_seed.assert_called_once_with(self.vm_name, self.vm_config, dry_run=True)
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-drive", qemu_cmd)
        self.assertIn(
            f"file={self.root / 'artifacts/testvm/cloud-init/seed.iso'},format=raw,if=virtio,media=cdrom,readonly=on",
            qemu_cmd,
        )

    def test_render_autoinstall_user_data_embeds_identity_ssh_and_first_boot_cloud_init(self):
        pubkey = self.root / ".ssh" / "id_ed25519.pub"
        pubkey.parent.mkdir(parents=True, exist_ok=True)
        pubkey.write_text("ssh-ed25519 AAAA from-file\n", encoding="utf-8")
        self.vm_config["cloud_init"] = {
            "hostname": "testvm",
            "user": "tester",
            "ssh_authorized_keys_file": str(pubkey),
            "packages": ["niri"],
            "runcmd": ["echo ready"],
        }
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
            "timezone": "Europe/Rome",
        }

        rendered = self.vmctl.render_autoinstall_user_data(self.vm_name, self.vm_config)

        self.assertIn('"username": "tester"', rendered)
        self.assertIn('"password": "$6$hash"', rendered)
        self.assertIn('"authorized-keys": [', rendered)
        self.assertIn('"ssh-ed25519 AAAA from-file"', rendered)
        self.assertIn('"user-data": {', rendered)
        self.assertIn('"packages": [', rendered)
        self.assertIn('"runcmd": [', rendered)

    def test_render_autoinstall_user_data_rejects_invalid_updates_value(self):
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
            "updates": "none",
        }

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.render_autoinstall_user_data(self.vm_name, self.vm_config)

    def test_cmd_install_unattended_builds_autoinstall_qemu_command(self):
        iso_path = self.root / self.vm_config["iso"]
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video="std", dry_run=True)

        with mock.patch.object(self.vmctl, "download_file") as download_file, \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "create_autoinstall_seed", return_value=self.root / "artifacts/testvm/autoinstall/seed.iso") as create_seed, \
             mock.patch.object(self.vmctl, "extract_installer_boot_artifacts", return_value=(self.root / "artifacts/testvm/installer/vmlinuz", self.root / "artifacts/testvm/installer/initrd")) as extract_boot, \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install_unattended(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True, vm=self.vm_config)
        create_seed.assert_called_once_with(self.vm_name, self.vm_config, dry_run=True)
        extract_boot.assert_called_once_with(self.vm_config, iso_path, dry_run=True)
        self.assertEqual(run_cmd.call_args_list[0].args[0][:3], ["qemu-img", "create", "-f"])
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-cdrom", qemu_cmd)
        self.assertIn("-kernel", qemu_cmd)
        self.assertIn(str(self.root / "artifacts/testvm/installer/vmlinuz"), qemu_cmd)
        self.assertIn("-initrd", qemu_cmd)
        self.assertIn(str(self.root / "artifacts/testvm/installer/initrd"), qemu_cmd)
        self.assertIn("-append", qemu_cmd)
        self.assertIn("autoinstall", qemu_cmd)
        self.assertIn("-no-reboot", qemu_cmd)
        self.assertIn(
            f"file={self.root / 'artifacts/testvm/autoinstall/seed.iso'},format=raw,if=virtio,media=cdrom,readonly=on",
            qemu_cmd,
        )

    def test_cmd_install_unattended_defaults_to_safe_video_for_non_ubuntu_installer(self):
        iso_path = self.root / self.vm_config["iso"]
        self.vm_config["video"]["default"] = "virtio-gl"
        self.vm_config["video"]["variants"]["safe"] = ["-vga", "std", "-display", "gtk", "-serial", "mon:stdio"]
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video=None, dry_run=True)

        with mock.patch.object(self.vmctl, "download_file"), \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "create_autoinstall_seed", return_value=self.root / "artifacts/testvm/autoinstall/seed.iso"), \
             mock.patch.object(self.vmctl, "extract_installer_boot_artifacts", return_value=(self.root / "artifacts/testvm/installer/vmlinuz", self.root / "artifacts/testvm/installer/initrd")), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install_unattended(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-vga", qemu_cmd)
        self.assertIn("std", qemu_cmd)
        self.assertIn("-serial", qemu_cmd)
        self.assertNotIn("virtio-vga-gl", qemu_cmd)

    def test_cmd_install_unattended_defaults_to_std_video_for_ubuntu_installer(self):
        iso_path = self.root / self.vm_config["iso"]
        self.vm_config["meta"] = {"slug": "ubuntu"}
        self.vm_config["video"]["default"] = "virtio-gl"
        self.vm_config["video"]["variants"]["safe"] = ["-vga", "std", "-display", "gtk", "-serial", "mon:stdio"]
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video=None, dry_run=True)

        with mock.patch.object(self.vmctl, "download_file"), \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "create_autoinstall_seed", return_value=self.root / "artifacts/testvm/autoinstall/seed.iso"), \
             mock.patch.object(self.vmctl, "extract_installer_boot_artifacts", return_value=(self.root / "artifacts/testvm/installer/vmlinuz", self.root / "artifacts/testvm/installer/initrd")), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install_unattended(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-vga", qemu_cmd)
        self.assertIn("std", qemu_cmd)
        self.assertNotIn("-serial", qemu_cmd)
        self.assertNotIn("virtio-vga-gl", qemu_cmd)

    def test_cmd_install_unattended_headless_uses_no_display(self):
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video="std", headless=True, dry_run=True)

        with mock.patch.object(self.vmctl, "download_file"), \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "create_autoinstall_seed", return_value=self.root / "artifacts/testvm/autoinstall/seed.iso"), \
             mock.patch.object(self.vmctl, "extract_installer_boot_artifacts", return_value=(self.root / "artifacts/testvm/installer/vmlinuz", self.root / "artifacts/testvm/installer/initrd")), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install_unattended(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-display", qemu_cmd)
        self.assertIn("none", qemu_cmd)
        self.assertIn("-monitor", qemu_cmd)
        self.assertNotIn("-vga", qemu_cmd)
        self.assertNotIn("gtk", qemu_cmd)

    def test_cmd_bootstrap_unattended_runs_install_background_start_and_post_install(self):
        self.create_disk()
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
            "post_install_run": ["echo ready"],
        }
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video="std", timeout=45, dry_run=True)

        with mock.patch.object(self.vmctl, "cmd_install_unattended") as install_unattended, \
             mock.patch.object(self.vmctl, "run_background", return_value=None) as run_background, \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "wait_for_ssh") as wait_for_ssh, \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_bootstrap_unattended(args)

        self.assertEqual(exit_code, 0)
        install_args = install_unattended.call_args.args[0]
        self.assertEqual(install_args.vm, self.vm_name)
        self.assertEqual(install_args.video, "std")
        self.assertEqual(install_args.headless, False)
        self.assertEqual(install_args.dry_run, True)
        qemu_cmd = run_background.call_args.args[0]
        log_path = run_background.call_args.args[1]
        self.assertEqual(qemu_cmd[0], "qemu-system-x86_64")
        self.assertIn("-display", qemu_cmd)
        self.assertIn("none", qemu_cmd)
        self.assertNotIn("-vga", qemu_cmd)
        self.assertEqual(log_path, self.root / "artifacts/testvm/logs/bootstrap-start.log")
        wait_for_ssh.assert_called_once_with(self.vm_config, 45, dry_run=True)
        self.assertEqual(
            run_cmd.call_args.args[0][-1],
            "sh -lc 'echo ready'",
        )

    def test_cmd_bootstrap_unattended_forwards_headless_to_installer(self):
        self.create_disk()
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video=None, headless=True, timeout=45, dry_run=True)

        with mock.patch.object(self.vmctl, "cmd_install_unattended") as install_unattended, \
             mock.patch.object(self.vmctl, "run_background", return_value=None), \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "wait_for_ssh"), \
             mock.patch.object(self.vmctl, "run"):
            exit_code = self.vmctl.cmd_bootstrap_unattended(args)

        self.assertEqual(exit_code, 0)
        install_args = install_unattended.call_args.args[0]
        self.assertEqual(install_args.headless, True)

    def test_cmd_bootstrap_unattended_requires_autoinstall(self):
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.cmd_bootstrap_unattended(argparse.Namespace(vm=self.vm_name, video=None, timeout=30, dry_run=True))

    def test_cmd_bootstrap_unattended_requires_cloud_init(self):
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.cmd_bootstrap_unattended(argparse.Namespace(vm=self.vm_name, video=None, timeout=30, dry_run=True))

    def test_cmd_stop_removes_stale_pid_file(self):
        pid_path = self.root / "artifacts/testvm/runtime/bootstrap-start.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text("424242\n", encoding="utf-8")
        args = argparse.Namespace(vm=self.vm_name, dry_run=False)

        exit_code = self.vmctl.cmd_stop(args)

        self.assertEqual(exit_code, 0)
        self.assertFalse(pid_path.exists())

    def test_cmd_shell_runs_interactive_ssh_command(self):
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
        }
        self.write_config_dir()

        with mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_shell(argparse.Namespace(vm=self.vm_name, dry_run=True))

        self.assertEqual(exit_code, 0)
        ssh_cmd = run_cmd.call_args.args[0]
        self.assertEqual(ssh_cmd[0], "ssh")
        self.assertNotIn("BatchMode=yes", ssh_cmd)
        self.assertEqual(ssh_cmd[-1], "tester@127.0.0.1")

    def test_cmd_shell_runs_with_ssh_provision(self):
        self.vm_config["ssh_provision"] = {
            "user": "tester",
            "ssh_host_port": 2223,
        }
        self.write_config_dir()

        with mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_shell(argparse.Namespace(vm=self.vm_name, dry_run=True))

        self.assertEqual(exit_code, 0)
        ssh_cmd = run_cmd.call_args.args[0]
        self.assertEqual(ssh_cmd[0], "ssh")
        self.assertNotIn("BatchMode=yes", ssh_cmd)
        self.assertEqual(ssh_cmd[-1], "tester@127.0.0.1")

    def test_cmd_stop_terminates_running_qemu_pid(self):
        pid_path = self.root / "artifacts/testvm/runtime/bootstrap-start.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text("1234\n", encoding="utf-8")
        args = argparse.Namespace(vm=self.vm_name, dry_run=False)

        with mock.patch.object(self.vmctl, "process_cmdline", side_effect=["qemu-system-x86_64 -display none", "qemu-system-x86_64 -display none", None]), \
             mock.patch.object(self.vmctl.os, "kill") as kill_mock, \
             mock.patch.object(self.vmctl.time, "sleep") as sleep_mock:
            exit_code = self.vmctl.cmd_stop(args)

        self.assertEqual(exit_code, 0)
        kill_mock.assert_called_once_with(1234, self.vmctl.signal.SIGTERM)
        self.assertLessEqual(sleep_mock.call_count, 1)
        self.assertFalse(pid_path.exists())

    def test_cmd_stop_terminates_discovered_qemu_when_pid_file_missing(self):
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, dry_run=False)

        with mock.patch.object(self.vmctl, "find_qemu_process_by_hostfwd_port", return_value=(5678, "qemu-system-x86_64 -netdev user,id=n1,hostfwd=tcp:127.0.0.1:2222-:22")), \
             mock.patch.object(self.vmctl, "process_cmdline", side_effect=["qemu-system-x86_64 -netdev user,id=n1,hostfwd=tcp:127.0.0.1:2222-:22", None]), \
             mock.patch.object(self.vmctl.os, "kill") as kill_mock, \
             mock.patch.object(self.vmctl.time, "sleep") as sleep_mock:
            exit_code = self.vmctl.cmd_stop(args)

        self.assertEqual(exit_code, 0)
        kill_mock.assert_called_once_with(5678, self.vmctl.signal.SIGTERM)
        self.assertLessEqual(sleep_mock.call_count, 1)

    def test_cmd_stop_falls_back_to_discovered_qemu_after_stale_pid_file(self):
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
        }
        self.write_config_dir()
        pid_path = self.root / "artifacts/testvm/runtime/bootstrap-start.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text("424242\n", encoding="utf-8")
        args = argparse.Namespace(vm=self.vm_name, dry_run=False)

        with mock.patch.object(self.vmctl, "find_qemu_process_by_hostfwd_port", return_value=(5678, "qemu-system-x86_64 -netdev user,id=n1,hostfwd=tcp:127.0.0.1:2222-:22")), \
             mock.patch.object(self.vmctl, "process_cmdline", side_effect=[None, "qemu-system-x86_64 -netdev user,id=n1,hostfwd=tcp:127.0.0.1:2222-:22", None]), \
             mock.patch.object(self.vmctl.os, "kill") as kill_mock, \
             mock.patch.object(self.vmctl.time, "sleep") as sleep_mock:
            exit_code = self.vmctl.cmd_stop(args)

        self.assertEqual(exit_code, 0)
        self.assertFalse(pid_path.exists())
        kill_mock.assert_called_once_with(5678, self.vmctl.signal.SIGTERM)
        self.assertLessEqual(sleep_mock.call_count, 1)

    def test_cmd_clean_removes_generated_artifact_subdirs(self):
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        disk_path = self.create_disk()
        vars_path = self.root / self.vm_config["firmware"]["vars_path"]
        vars_path.parent.mkdir(parents=True, exist_ok=True)
        vars_path.write_text("vars", encoding="utf-8")
        for relative in [
            "artifacts/testvm/runtime/bootstrap-start.pid",
            "artifacts/testvm/logs/bootstrap-start.log",
            "artifacts/testvm/cloud-init/seed.iso",
            "artifacts/testvm/autoinstall/seed.iso",
            "artifacts/testvm/installer/vmlinuz",
        ]:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("artifact", encoding="utf-8")

        with mock.patch.object(self.vmctl, "cmd_stop", return_value=0):
            exit_code = self.vmctl.cmd_clean(argparse.Namespace(vm=self.vm_name, all=False, dry_run=False))

        self.assertEqual(exit_code, 0)
        self.assertFalse(disk_path.exists())
        self.assertFalse(vars_path.exists())
        self.assertFalse((self.root / "artifacts/testvm/runtime").exists())
        self.assertFalse((self.root / "artifacts/testvm/logs").exists())
        self.assertFalse((self.root / "artifacts/testvm/cloud-init").exists())
        self.assertFalse((self.root / "artifacts/testvm/autoinstall").exists())
        self.assertFalse((self.root / "artifacts/testvm/installer").exists())

    def test_cmd_clean_stops_vm_before_removing_artifacts(self):
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        self.create_disk()

        with mock.patch.object(self.vmctl, "cmd_stop", return_value=0) as stop_cmd:
            exit_code = self.vmctl.cmd_clean(argparse.Namespace(vm=self.vm_name, all=False, dry_run=False))

        self.assertEqual(exit_code, 0)
        stop_cmd.assert_called_once()
        stop_args = stop_cmd.call_args.args[0]
        self.assertEqual(stop_args.vm, self.vm_name)
        self.assertFalse(stop_args.dry_run)

    def test_cmd_delete_iso_removes_cached_iso_and_partial_download(self):
        iso_path = self.root / self.vm_config["iso"]
        partial_path = iso_path.with_name(iso_path.name + ".part")
        iso_path.parent.mkdir(parents=True, exist_ok=True)
        iso_path.write_text("iso", encoding="utf-8")
        partial_path.write_text("partial", encoding="utf-8")

        exit_code = self.vmctl.cmd_delete_iso(argparse.Namespace(vm=self.vm_name, dry_run=False))

        self.assertEqual(exit_code, 0)
        self.assertFalse(iso_path.exists())
        self.assertFalse(partial_path.exists())

    def test_cmd_delete_iso_dry_run_keeps_cached_iso(self):
        iso_path = self.root / self.vm_config["iso"]
        iso_path.parent.mkdir(parents=True, exist_ok=True)
        iso_path.write_text("iso", encoding="utf-8")

        exit_code = self.vmctl.cmd_delete_iso(argparse.Namespace(vm=self.vm_name, dry_run=True))

        self.assertEqual(exit_code, 0)
        self.assertTrue(iso_path.exists())

    def test_cmd_post_install_waits_copies_and_runs_remote_commands(self):
        source_dir = self.root / "host-niri"
        source_dir.mkdir()
        (source_dir / "config.kdl").write_text("layout {}", encoding="utf-8")
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
            "copy_from_host": [{"source": str(source_dir) + "/", "dest": "/home/tester/.config/niri"}],
            "post_install_run": ["sudo apt update", "sudo apt install -y niri"],
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=30, dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "wait_for_ssh") as wait_for_ssh, \
             mock.patch.object(self.vmctl, "wait_for_guest_post_install_ready") as wait_ready, \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_post_install(args)

        self.assertEqual(exit_code, 0)
        wait_for_ssh.assert_called_once_with(self.vm_config, 30, dry_run=True)
        wait_ready.assert_called_once_with(self.vm_config, dry_run=True)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(
            executed[0],
            [
                "ssh",
                "-F",
                "/dev/null",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "BatchMode=yes",
                "-p",
                "2222",
                "tester@127.0.0.1",
                "sh -lc 'mkdir -p /home/tester/.config/niri'",
            ],
        )
        self.assertEqual(
            executed[1],
            mock.ANY,
        )
        self.assertEqual(executed[1][0], "scp")
        self.assertEqual(executed[1][-1], "tester@127.0.0.1:/home/tester/.config/niri")
        self.assertTrue(executed[1][-2].endswith("/host-niri/."))
        self.assertEqual(
            executed[2][-1],
            "sh -lc 'sudo apt update'",
        )
        self.assertEqual(
            executed[3][-1],
            "sh -lc 'sudo apt install -y niri'",
        )

    def test_cmd_post_install_supports_ssh_provision(self):
        source_dir = self.root / "host-niri"
        source_dir.mkdir()
        (source_dir / "config.kdl").write_text("layout {}", encoding="utf-8")
        self.vm_config["ssh_provision"] = {
            "user": "tester",
            "ssh_host_port": 2223,
            "copy_from_host": [{"source": str(source_dir) + "/", "dest": "/home/tester/.config/niri"}],
            "post_install_run": ["sudo pacman -Sy --noconfirm --needed foot niri || true"],
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=30, dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "wait_for_ssh") as wait_for_ssh, \
             mock.patch.object(self.vmctl, "wait_for_guest_post_install_ready") as wait_ready, \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_post_install(args)

        self.assertEqual(exit_code, 0)
        wait_for_ssh.assert_called_once_with(self.vm_config, 30, dry_run=True)
        wait_ready.assert_called_once_with(self.vm_config, dry_run=True)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(executed[0][-1], "sh -lc 'mkdir -p /home/tester/.config/niri'")
        self.assertEqual(executed[1][-1], "tester@127.0.0.1:/home/tester/.config/niri")
        self.assertEqual(
            executed[2][-1],
            "sh -lc 'sudo pacman -Sy --noconfirm --needed foot niri || true'",
        )

    def test_cmd_post_install_supports_sudo_copy_for_system_connections(self):
        source_file = self.root / "vpn.nmconnection"
        source_file.write_text("[connection]\nid=test\n", encoding="utf-8")
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
            "copy_from_host": [
                {
                    "source": str(source_file),
                    "source_sudo": True,
                    "dest": "/etc/NetworkManager/system-connections/test.nmconnection",
                    "dest_sudo": True,
                    "dest_mode": "600",
                }
            ],
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=30, dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "wait_for_ssh") as wait_for_ssh, \
             mock.patch.object(self.vmctl, "wait_for_guest_post_install_ready") as wait_ready, \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_post_install(args)

        self.assertEqual(exit_code, 0)
        wait_for_ssh.assert_called_once_with(self.vm_config, 30, dry_run=True)
        wait_ready.assert_called_once_with(self.vm_config, dry_run=True)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(executed[0], ["sudo", "cp", "--archive", str(source_file), mock.ANY])
        self.assertEqual(
            executed[1][-1],
            "sudo sh -lc 'mkdir -p /etc/NetworkManager/system-connections'",
        )
        self.assertTrue(executed[2][-1].startswith("tester@127.0.0.1:/tmp/test.nmconnection"))
        self.assertEqual(
            executed[3][-1],
            "sudo sh -lc 'install -D -m 600 /tmp/test.nmconnection /etc/NetworkManager/system-connections/test.nmconnection && rm -f /tmp/test.nmconnection'",
        )

    def test_cmd_post_install_recursive_copy_stages_directory_before_scp(self):
        source_dir = self.root / "host-geany"
        source_dir.mkdir()
        (source_dir / "geany.conf").write_text("config", encoding="utf-8")
        (source_dir / "runtime-link").symlink_to(source_dir / "geany.conf")
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
            "copy_from_host": [{"source": str(source_dir) + "/", "dest": "/home/tester/.config/geany"}],
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=30, dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "wait_for_ssh") as wait_for_ssh, \
             mock.patch.object(self.vmctl, "wait_for_guest_post_install_ready") as wait_ready, \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_post_install(args)

        self.assertEqual(exit_code, 0)
        wait_for_ssh.assert_called_once_with(self.vm_config, 30, dry_run=True)
        wait_ready.assert_called_once_with(self.vm_config, dry_run=True)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(executed[0][-1], "sh -lc 'mkdir -p /home/tester/.config/geany'")
        self.assertEqual(executed[1][0], "scp")
        self.assertTrue(executed[1][-2].endswith("/."))
        self.assertEqual(executed[1][-1], "tester@127.0.0.1:/home/tester/.config/geany")

    def test_cmd_post_install_recursive_copy_skips_dangling_symlink(self):
        source_dir = self.root / "host-geany-dangling"
        source_dir.mkdir()
        (source_dir / "geany.conf").write_text("config", encoding="utf-8")
        (source_dir / "geany_socket_wayland-0").symlink_to(source_dir / "missing-socket")
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
            "copy_from_host": [{"source": str(source_dir) + "/", "dest": "/home/tester/.config/geany"}],
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=30, dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "wait_for_ssh") as wait_for_ssh, \
             mock.patch.object(self.vmctl, "wait_for_guest_post_install_ready") as wait_ready, \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_post_install(args)

        self.assertEqual(exit_code, 0)
        wait_for_ssh.assert_called_once_with(self.vm_config, 30, dry_run=True)
        wait_ready.assert_called_once_with(self.vm_config, dry_run=True)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(executed[0][-1], "sh -lc 'mkdir -p /home/tester/.config/geany'")
        self.assertEqual(executed[1][0], "scp")
        self.assertEqual(executed[1][-1], "tester@127.0.0.1:/home/tester/.config/geany")

    def test_cmd_post_install_skips_missing_host_path(self):
        missing_dir = self.root / "missing-config"
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
            "copy_from_host": [{"source": str(missing_dir) + "/", "dest": "/home/tester/.config/missing"}],
            "post_install_run": ["echo done"],
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=30, dry_run=False)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "wait_for_ssh") as wait_for_ssh, \
             mock.patch.object(self.vmctl, "wait_for_guest_post_install_ready") as wait_ready, \
             mock.patch.object(self.vmctl, "run") as run_cmd, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_post_install(args)

        self.assertEqual(exit_code, 0)
        wait_for_ssh.assert_called_once_with(self.vm_config, 30, dry_run=False)
        wait_ready.assert_called_once_with(self.vm_config, dry_run=False)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0][-1], "sh -lc 'echo done'")
        self.assertIn("Skipping missing host path", stdout.getvalue())

    def test_firmware_args_uses_common_ovmf_fallback_when_configured_paths_are_missing(self):
        self.create_disk()
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "/missing/OVMF_CODE_4M.fd",
            "vars_template": "/missing/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()

        fallback_code = self.root / "firmware" / "OVMF_CODE_4M.fd"
        fallback_vars = self.root / "firmware" / "OVMF_VARS_4M.fd"
        fallback_code.parent.mkdir(parents=True, exist_ok=True)
        fallback_code.write_text("code", encoding="utf-8")
        fallback_vars.write_text("vars", encoding="utf-8")

        with mock.patch.object(
            self.vmctl,
            "COMMON_OVMF_PAIRS",
            [(str(fallback_code), str(fallback_vars))],
        ):
            qemu_fw_args = self.vmctl.firmware_args(self.vm_config)

        self.assertIn(f"if=pflash,format=raw,readonly=on,file={fallback_code}", qemu_fw_args)
        self.assertIn(
            f"if=pflash,format=raw,file={self.root / 'artifacts/testvm/OVMF_VARS.fd'}",
            qemu_fw_args,
        )
        self.assertEqual((self.root / "artifacts/testvm/OVMF_VARS.fd").read_text(encoding="utf-8"), "vars")

    def test_cmd_setup_reports_missing_dependencies_and_install_hints(self):
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "/missing/OVMF_CODE_4M.fd",
            "vars_template": "/missing/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        args = argparse.Namespace()

        def fake_which(name):
            if name in {"qemu-system-x86_64", "python3"}:
                return f"/usr/bin/{name}"
            return None

        with mock.patch.object(self.vmctl.shutil, "which", side_effect=fake_which), \
             mock.patch.object(self.vmctl, "COMMON_OVMF_PAIRS", []), \
             mock.patch.object(self.vmctl, "read_os_release", return_value={"ID": "ubuntu", "ID_LIKE": "debian"}), \
             mock.patch("sys.stdout", new_callable=mock.MagicMock()) as stdout:
            exit_code = self.vmctl.cmd_setup(args)

        output = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertEqual(exit_code, 1)
        self.assertIn("[missing] qemu-img", output)
        self.assertIn("Unable to locate OVMF firmware files for EFI guest.", output)
        self.assertIn("Affected EFI profiles: testvm", output)
        self.assertIn("sudo apt install -y qemu-system-x86 qemu-utils ovmf python3 make dialog cloud-image-utils xorriso", output)

    def test_cmd_setup_can_install_missing_packages_after_confirmation(self):
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        firmware_dir = self.root / "firmware"
        firmware_dir.mkdir(parents=True, exist_ok=True)
        (firmware_dir / "OVMF_CODE_4M.fd").write_text("code", encoding="utf-8")
        (firmware_dir / "OVMF_VARS_4M.fd").write_text("vars", encoding="utf-8")
        args = argparse.Namespace()

        which_calls = {"count": 0}

        def fake_which(name):
            which_calls["count"] += 1
            if which_calls["count"] <= 4:
                if name in {"qemu-system-x86_64", "python3"}:
                    return f"/usr/bin/{name}"
                return None
            return "/usr/bin/fake"

        with mock.patch.object(self.vmctl.shutil, "which", side_effect=fake_which), \
             mock.patch.object(self.vmctl, "prompt_yes_no", return_value=True), \
             mock.patch.object(self.vmctl, "run") as run_cmd, \
             mock.patch("sys.stdout", new_callable=mock.MagicMock()):
            exit_code = self.vmctl.cmd_setup(args)

        self.assertEqual(exit_code, 0)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(executed[0], ["sudo", "apt", "update"])
        self.assertEqual(
            executed[1],
            ["sudo", "apt", "install", "-y", "qemu-system-x86", "qemu-utils", "ovmf", "python3", "make", "dialog", "cloud-image-utils", "xorriso"],
        )

    def test_cmd_setup_passes_when_requirements_and_firmware_are_available(self):
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        firmware_dir = self.root / "firmware"
        firmware_dir.mkdir(parents=True, exist_ok=True)
        (firmware_dir / "OVMF_CODE_4M.fd").write_text("code", encoding="utf-8")
        (firmware_dir / "OVMF_VARS_4M.fd").write_text("vars", encoding="utf-8")
        args = argparse.Namespace()

        with mock.patch.object(self.vmctl.shutil, "which", return_value="/usr/bin/fake"), \
             mock.patch("sys.stdout", new_callable=mock.MagicMock()) as stdout:
            exit_code = self.vmctl.cmd_setup(args)

        output = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertEqual(exit_code, 0)
        self.assertIn("Setup check passed.", output)


if __name__ == "__main__":
    unittest.main()
