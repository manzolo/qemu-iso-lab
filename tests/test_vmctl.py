import argparse
import importlib.machinery
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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
        (self.config_dir / "catalog.json").write_text(
            json.dumps({"catalog": {"schema_version": 1}}, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.config_dir / "profiles" / "test.json").write_text(
            json.dumps({"vms": {self.vm_name: self.vm_config}}, indent=2) + "\n",
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
        self.assertEqual(config["catalog"]["schema_version"], 1)

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

    def test_ensure_iso_removes_invalid_cached_html_and_redownloads(self):
        iso_path = self.root / self.vm_config["iso"]
        iso_path.parent.mkdir(parents=True, exist_ok=True)
        iso_path.write_text("<!doctype html><html></html>", encoding="utf-8")

        with mock.patch.object(self.vmctl, "download_file") as download_file:
            resolved = self.vmctl.ensure_iso(self.vm_config)

        self.assertEqual(resolved, iso_path)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=False)

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
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True)
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
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True)
        run_cmd.assert_called_once()
        self.assertEqual(run_cmd.call_args.args[0][:3], ["qemu-img", "create", "-f"])

    def test_cmd_install_dry_run_builds_qemu_command_with_cdrom(self):
        disk_path = self.create_disk()
        iso_path = self.root / self.vm_config["iso"]
        args = argparse.Namespace(vm=self.vm_name, video="std", dry_run=True)

        with mock.patch.object(self.vmctl, "download_file") as download_file, \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True)
        run_cmd.assert_called_once()
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertEqual(run_cmd.call_args.kwargs["dry_run"], True)
        self.assertEqual(qemu_cmd[0], "qemu-system-x86_64")
        self.assertIn(f"file={disk_path},format=qcow2,if=virtio", qemu_cmd)
        self.assertIn("-cdrom", qemu_cmd)
        self.assertEqual(qemu_cmd[qemu_cmd.index("-cdrom") + 1], str(iso_path))
        self.assertIn("-vga", qemu_cmd)
        self.assertIn("std", qemu_cmd)

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
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_flash_helper(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(run_cmd.call_count, 2)
        convert_cmd = run_cmd.call_args_list[0].args[0]
        self.assertEqual(
            convert_cmd,
            ["qemu-img", "convert", "-n", "-p", "-f", "qcow2", "-O", "raw", str(disk_path), "/dev/sdz"],
        )

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
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_flash_helper(args)

        self.assertEqual(exit_code, 0)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(executed[0], ["wipefs", "-a", "-f", "/dev/sdz1"])
        self.assertEqual(executed[1], ["wipefs", "-a", "-f", "/dev/sdz2"])
        self.assertEqual(executed[2], ["wipefs", "-a", "-f", "/dev/sdz"])
        self.assertEqual(executed[3], ["blockdev", "--rereadpt", "/dev/sdz"])

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

        class FakeTempDir:
            def __init__(self, path):
                self.path = path

            def __enter__(self):
                self.path.mkdir(parents=True, exist_ok=True)
                return str(self.path)

            def __exit__(self, exc_type, exc, tb):
                return False

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
             mock.patch.object(self.vmctl, "run") as run_cmd, \
             mock.patch.object(self.vmctl, "run_progress") as run_progress, \
             mock.patch.object(self.vmctl.tempfile, "TemporaryDirectory", return_value=FakeTempDir(temp_root)), \
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

    def test_cmd_start_dry_run_builds_qemu_command_without_cdrom(self):
        disk_path = self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, video=None, dry_run=True)

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

    def test_common_args_supports_sata_disk_interface(self):
        disk_path = self.create_disk()
        self.vm_config["disk"]["interface"] = "sata"

        with mock.patch.object(self.vmctl, "require_command"):
            qemu_cmd = self.vmctl.common_args(self.vm_config, variant=None, dry_run=True)

        self.assertIn("ich9-ahci,id=ahci0", qemu_cmd)
        self.assertIn(f"id=disk0,file={disk_path},format=qcow2,if=none", qemu_cmd)
        self.assertIn("ide-hd,drive=disk0,bus=ahci0.0", qemu_cmd)

    def test_cmd_boot_check_dry_run_uses_ci_settings(self):
        self.create_disk()
        self.vm_config["ci"] = {
            "accel": "tcg",
            "headless": True,
            "boot_from": "cdrom",
            "auto_input": [{"match": "boot:", "send": "\n"}],
            "expect": "login:",
            "timeout_sec": 42,
        }
        self.write_config_dir()
        iso_path = self.root / self.vm_config["iso"]
        args = argparse.Namespace(vm=self.vm_name, expect=None, timeout=None, dry_run=True)

        with mock.patch.object(self.vmctl, "download_file") as download_file, \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run_and_expect") as run_and_expect:
            exit_code = self.vmctl.cmd_boot_check(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True)
        run_and_expect.assert_called_once()
        qemu_cmd, expected_text, timeout_sec = run_and_expect.call_args.args
        self.assertIn("-cdrom", qemu_cmd)
        self.assertEqual(qemu_cmd[qemu_cmd.index("-cdrom") + 1], str(iso_path))
        self.assertEqual(expected_text, "login:")
        self.assertEqual(timeout_sec, 42)
        self.assertEqual(
            run_and_expect.call_args.kwargs["auto_inputs"],
            [("boot:", "\n")],
        )

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
        self.assertIn("[missing] testvm:", output)
        self.assertIn("sudo apt install -y qemu-system-x86 qemu-utils ovmf python3 make dialog", output)

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
