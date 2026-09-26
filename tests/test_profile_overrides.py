"""Local profile editing validates before writing and preserves the shared catalog."""
import json
from unittest import mock

from tests._common import BaseVmctlTestCase
from vmctl import config, profile_overrides
from vmctl.errors import VMError


class ProfileOverrideTests(BaseVmctlTestCase):
    def local_path(self):
        return self.config_dir / 'profiles/local.json'

    def test_resources_save_as_a_partial_override_without_changing_catalog(self):
        catalog = {path: path.read_bytes() for path in (self.config_dir / 'profiles').glob('*.json')}
        loaded = profile_overrides.read_override(self.vm_name)
        self.assertEqual(loaded['override'], {})
        saved = profile_overrides.save_override(self.vm_name, {'memory_mb': 4096, 'cpus': 4}, loaded['revision'])
        self.assertEqual(saved['effective']['memory_mb'], 4096)
        self.assertEqual(saved['base']['memory_mb'], 1024)
        self.assertEqual(json.loads(self.local_path().read_text())['vms'][self.vm_name], {'memory_mb': 4096, 'cpus': 4})
        for path, before in catalog.items():
            self.assertEqual(path.read_bytes(), before)

    def test_nested_values_and_lists_merge_only_once_and_reset_keeps_other_overrides(self):
        self.local_path().write_text(json.dumps({'note': 'keep me', 'vms': {self.vm_name: {'cpus': 2}}}))
        before = self.local_path().read_bytes()
        loaded = profile_overrides.read_override(self.vm_name)
        saved = profile_overrides.save_override(self.vm_name, {'video': {'variants': {'std': ['-device', 'test-device']}}}, loaded['revision'])
        self.assertEqual(saved['effective']['video']['variants']['std'], ['-vga', 'std', '-device', 'test-device'])
        self.assertEqual(self.local_path().with_suffix('.json.bak').read_bytes(), before)
        saved = profile_overrides.save_override(self.vm_name, saved['override'], saved['revision'])
        self.assertEqual(saved['effective']['video']['variants']['std'], ['-vga', 'std', '-device', 'test-device'])
        reset = profile_overrides.save_override(self.vm_name, {}, saved['revision'])
        self.assertEqual(reset['effective']['cpus'], 1)
        self.assertEqual(json.loads(self.local_path().read_text()), {'note': 'keep me', 'vms': {}})

    def test_invalid_overrides_never_write_the_file(self):
        loaded = profile_overrides.read_override(self.vm_name)
        for value in ([], {'cpus': 0}, {'memory_mb': -1}, {'cpus': True}, {'memory_mb': 'oops'}, {'disk': None}):
            with self.subTest(value=value), self.assertRaises(VMError):
                profile_overrides.save_override(self.vm_name, value, loaded['revision'])
            self.assertFalse(self.local_path().exists())

    def test_stale_editor_never_overwrites_external_changes(self):
        loaded = profile_overrides.read_override(self.vm_name)
        self.local_path().write_text(json.dumps({'vms': {self.vm_name: {'cpus': 8}}}))
        before = self.local_path().read_bytes()
        with self.assertRaisesRegex(VMError, 'changed since'):
            profile_overrides.save_override(self.vm_name, {'cpus': 4}, loaded['revision'])
        self.assertEqual(self.local_path().read_bytes(), before)

    def test_validation_failure_keeps_existing_override(self):
        self.local_path().write_text(json.dumps({'vms': {self.vm_name: {'cpus': 8}}}))
        loaded = profile_overrides.read_override(self.vm_name)
        before = self.local_path().read_bytes()
        with mock.patch.object(config, 'load_config', side_effect=[config.load_config(), VMError('port conflict')]):
            with self.assertRaisesRegex(VMError, 'port conflict'):
                profile_overrides.save_override(self.vm_name, {'cpus': 4}, loaded['revision'])
        self.assertEqual(self.local_path().read_bytes(), before)

    def test_local_only_profile_can_be_edited_but_not_reset(self):
        self.local_path().write_text(json.dumps({'vms': {'my-local-vm': self.vm_config}}))
        loaded = profile_overrides.read_override('my-local-vm')
        self.assertTrue(loaded['local_only'])
        with self.assertRaisesRegex(VMError, 'no catalog template'):
            profile_overrides.save_override('my-local-vm', {}, loaded['revision'])
        saved = profile_overrides.save_override('my-local-vm', {**loaded['override'], 'cpus': 3}, loaded['revision'])
        self.assertEqual(saved['effective']['cpus'], 3)

    def test_replacing_local_document_for_validation_does_not_write_it(self):
        candidate = config.load_config(local_profiles={'vms': {self.vm_name: {'memory_mb': 2048}}})
        self.assertEqual(candidate['vms'][self.vm_name]['memory_mb'], 2048)
        self.assertEqual(config.load_config()['vms'][self.vm_name]['memory_mb'], 1024)
        self.assertFalse(self.local_path().exists())
