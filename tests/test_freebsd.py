from _common import *
from vmctl.errors import VMError
from vmctl import freebsd, lifecycle, cloud_init, config, qemu, runtime


class FreeBSDTests(BaseVmctlTestCase):
    def profile(self):
        # Tracked single profile only; never consult the host's local.json.
        return json.loads((ROOT / 'vms/profiles/freebsd-unattended.json').read_text())['vms']['freebsd-unattended']

    def test_manual_profile_stays_manual_and_new_profile_is_bootstrap(self):
        vm = self.profile()
        self.assertEqual(lifecycle.local_test_mode(vm)[0], 'bootstrap-freebsd')
        self.assertNotIn('systemctl', json.dumps(vm))
        self.assertNotIn('verify-desktop', json.dumps(vm))

    def test_rejects_incompatible_hardware_before_writes(self):
        for section, key, value in [('firmware', 'type', 'efi'), ('disk', 'interface', 'ide'),
                                    (None, 'machine', 'q35'), (None, 'network_device', 'e1000')]:
            with self.subTest(key=key):
                vm = self.profile()
                (vm[section] if section else vm)[key] = value
                with self.assertRaises(VMError):
                    freebsd.render_installerconfig('test', vm, [])

    def test_guest_identity_and_flush_before_token(self):
        vm = self.profile()
        vm['freebsd_config']['username'] = 'tester'
        vm['ssh_provision']['user'] = 'tester'
        script = freebsd.render_installerconfig('test', vm, ['ssh-ed25519 TEST'])
        self.assertIn('pw useradd tester', script)
        self.assertLess(script.index('cp /tmp/vmctl-resolv.conf'), script.index('#!/bin/sh'))
        self.assertIn('/home/tester/.ssh/authorized_keys', script)
        self.assertIn('ssh-ed25519 TEST', script)
        self.assertIn('/usr/local/sbin:/usr/local/bin', script)
        self.assertTrue(script.endswith('sync\n'))
        rc = freebsd.render_rc_local()
        self.assertLess(rc.index('trap failed EXIT'), rc.index('dhclient vtnet0'))
        self.assertLess(rc.index('bsdinstall script'), rc.index(f'echo "{freebsd.BOOTSTRAP_COMPLETE_TOKEN}"'))
        success = rc[rc.index('bsdinstall script'):]
        self.assertLess(success.index('sync\n'), success.index(freebsd.BOOTSTRAP_COMPLETE_TOKEN))
        self.assertLess(success.index(freebsd.BOOTSTRAP_COMPLETE_TOKEN), success.index('/sbin/shutdown -p now'))
        self.assertIn(freebsd.BOOTSTRAP_FAILED_TOKEN, rc)
        self.assertLess(rc.index('tail -n'), rc.index(freebsd.BOOTSTRAP_FAILED_TOKEN))
        self.assertLess(rc.index('cp /etc/resolv.conf /tmp/vmctl-resolv.conf'), rc.index('bsdinstall script'))

    def test_iso_cache_accounts_for_wrapper_and_key_changes(self):
        vm = self.profile()
        source = self.root / 'original.iso'
        source.write_bytes(b'\0' * (16 * 2048) + b'\x01CD001\x01' + b'\0' * 33 + b'14_3_RELEASE_AMD64_CD'.ljust(32, b' '))
        def fake_run(command, **kwargs):
            if command[0] == 'xorriso':
                Path(command[-1]).write_text('autoboot_delay="10"\n')
            elif command[0] == 'cp':
                shutil.copyfile(command[1], command[2])
        with mock.patch.object(runtime, 'require_command'), mock.patch.object(runtime, 'run', side_effect=fake_run) as run:
            dest = freebsd.ensure_install_iso('test', vm, source, ['KEY1'])
            self.assertTrue(dest.is_file())
            graft = next(c.args[0] for c in run.call_args_list if c.args[0][0] == 'growisofs')
            self.assertEqual(graft[graft.index('-V') + 1], '14_3_RELEASE_AMD64_CD')
            self.assertIn('growisofs', [c.args[0][0] for c in run.call_args_list])
            run.reset_mock()
            freebsd.ensure_install_iso('test', vm, source, ['KEY1'])
            run.assert_not_called()
            freebsd.ensure_install_iso('test', vm, source, ['KEY2'])
            self.assertTrue(run.called)
            run.reset_mock()
            with mock.patch.object(freebsd, 'render_rc_local', return_value='changed wrapper'):
                freebsd.ensure_install_iso('test', vm, source, ['KEY2'])
            self.assertTrue(run.called)

    def test_failed_installer_has_timeout_and_does_not_start_disk(self):
        vm = self.profile()
        args = argparse.Namespace(vm='freebsd-unattended', timeout=47, dry_run=True)
        error = VMError(freebsd.BOOTSTRAP_FAILED_TOKEN + ': pkg failed')
        with mock.patch.object(config, 'load_config', return_value={'vms': {args.vm: vm}}), \
             mock.patch.object(lifecycle.iso, 'ensure_iso', return_value=self.root / 'source.iso'), \
             mock.patch.object(lifecycle, 'ensure_vm_disk'), \
             mock.patch.object(cloud_init, '_authorized_keys_for_vm', return_value=['KEY']), \
             mock.patch.object(freebsd, 'ensure_install_iso', return_value=self.root / 'install.iso'), \
             mock.patch.object(qemu, 'common_args', return_value=[]), \
             mock.patch.object(qemu, 'run_and_expect', side_effect=error) as run, \
             mock.patch.object(lifecycle, 'start_installed_vm_headless') as start, \
             mock.patch.object(lifecycle, 'explain_failed_bootstrap', return_value=error) as explain:
            with self.assertRaises(VMError):
                lifecycle.cmd_bootstrap_freebsd(args)
            self.assertEqual(run.call_args.kwargs['timeout_sec'], 47)
            self.assertEqual(run.call_args.kwargs['exit_grace_sec'], freebsd.SHUTDOWN_GRACE_SEC)
            explain.assert_called_once()
            start.assert_not_called()
