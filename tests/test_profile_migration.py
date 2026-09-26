import json
import os
from pathlib import Path
import tempfile
import unittest

from tools import migrate_profile_names as migration


class ProfileMigrationTests(unittest.TestCase):
    def test_preserves_disk_bytes_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "artifacts/arch-dms-local"
            source.mkdir(parents=True)
            (source / "disk.qcow2").write_bytes(b"installed disk")
            for old, new in migration.migration_plan(root):
                migration.rename_no_replace(old, new)
            self.assertEqual((root / "artifacts/arch-dms/disk.qcow2").read_bytes(), b"installed disk")
            self.assertEqual(migration.migration_plan(root), [])

    def test_refuses_conflicts_even_if_destination_appears_after_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "artifacts/arch-dms-local"
            source.mkdir(parents=True)
            moves = migration.migration_plan(root)
            destination = root / "artifacts/arch-dms"
            destination.mkdir()
            with self.assertRaises(FileExistsError):
                migration.rename_no_replace(*moves[0])
            with self.assertRaisesRegex(ValueError, "Conflict"):
                migration.migration_plan(root)
            self.assertTrue(source.is_dir())
            self.assertTrue(destination.is_dir())

    def test_refuses_running_vm(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "artifacts/arch-dms-local/runtime"
            runtime.mkdir(parents=True)
            (runtime / "bootstrap-start.pid").write_text(str(os.getpid()))
            with self.assertRaisesRegex(ValueError, "active"):
                migration.migration_plan(root)

    def test_local_backup_preserves_original_and_rewrites_only_path_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local.json"
            original = json.dumps({"vms": {"cachyos-local": {"disk": {"path": "artifacts/cachyos-local/disk.qcow2"}, "iso": "isos/cachyos-desktop-linux-latest.iso"}}})
            path.write_text(original)
            migration.migrate_local_file(path, apply=False)
            self.assertEqual(path.read_text(), original)
            migration.migrate_local_file(path, apply=True)
            migration.migrate_local_file(path, apply=True)
            backups = list(Path(tmp).glob("local.json.backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), original)
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
            vm = json.loads(path.read_text())["vms"]["cachyos-desktop"]
            self.assertEqual(vm["disk"]["path"], "artifacts/cachyos-desktop/disk.qcow2")
            self.assertEqual(vm["iso"], "isos/cachyos-desktop-linux-latest.iso")

    def test_refuses_ambiguous_local_override(self):
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            migration.migrate_local_data({"vms": {"arch-dms-local": {}, "arch-dms": {}}})


class TwinRenameTests(unittest.TestCase):
    """2026-09-26: the manual freebsd moves to freebsd-installer before freebsd-unattended takes its name."""

    def test_manual_twin_moves_first_and_a_second_run_changes_nothing(self):
        import json as _json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "artifacts/freebsd").mkdir(parents=True)
            (root / "artifacts/freebsd/state.json").write_text(_json.dumps({"install": {"flow": "provision"}}))
            (root / "artifacts/freebsd-unattended").mkdir()
            (root / "artifacts/freebsd-unattended/state.json").write_text(_json.dumps({"install": {"flow": "bootstrap-freebsd"}}))
            moves = migration.migration_plan(root)
            self.assertEqual([(s.name, d.name) for s, d in moves][:2],
                             [("freebsd", "freebsd-installer"), ("freebsd-unattended", "freebsd")])
            for source, destination in moves:
                migration.rename_no_replace(source, destination)
            self.assertEqual(migration.migration_plan(root), [])  # the new freebsd is unattended: it stays

    def test_old_local_file_maps_twins_and_a_migrated_one_is_left_alone(self):
        old = {"vms": {"freebsd": {"memory_mb": 1}, "freebsd-unattended": {"memory_mb": 2},
                       "windows11-unattended": {"disk": {"path": "artifacts/windows11-unattended/disk.qcow2"}}}}
        new = migration.migrate_local_data(old)
        self.assertEqual(new["vms"]["freebsd-installer"], {"memory_mb": 1})
        self.assertEqual(new["vms"]["freebsd"], {"memory_mb": 2})
        self.assertEqual(new["vms"]["windows-11"]["disk"]["path"], "artifacts/windows-11/disk.qcow2")
        self.assertEqual(migration.migrate_local_data(new), new)
