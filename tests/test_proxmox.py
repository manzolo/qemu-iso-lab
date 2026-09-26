from _common import *
import ipaddress
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: the parser arrived in 3.11
    tomllib = None
from vmctl.errors import VMError
from vmctl import checkpoint, lifecycle, libvirt, proxmox, qemu


class ProxmoxTests(BaseVmctlTestCase):
    def profile(self, name='proxmox-ve'):
        # Tracked profile file only; never consult the host's local.json.
        return json.loads((ROOT / 'vms/profiles/proxmox-lab.json').read_text())['vms'][name]

    def test_profiles_pick_their_flows(self):
        self.assertEqual(lifecycle.local_test_mode(self.profile())[0], 'bootstrap-proxmox')
        self.assertEqual(lifecycle.local_test_mode(self.profile('proxmox-lab-client'))[0], 'bootstrap-preseed')

    @unittest.skipIf(tomllib is None, "tomllib needs Python 3.11")
    def test_answer_file_is_valid_toml_with_zfs_mirror_over_both_disks(self):
        answer = tomllib.loads(proxmox.render_answer('proxmox-ve', self.profile(), ['ssh-ed25519 TEST']))
        self.assertEqual(answer['disk-setup'], {'filesystem': 'zfs', 'disk-list': ['vda', 'vdb'],
                                                'zfs': {'raid': 'raid1'}})
        glob = answer['global']
        self.assertEqual(glob['root-ssh-keys'], ['ssh-ed25519 TEST'])
        self.assertTrue(glob['root-password-hashed'].startswith('$6$'))
        self.assertNotIn('root-password', glob)
        self.assertEqual(glob['reboot-mode'], 'power-off')
        self.assertFalse(glob['reboot-on-error'])
        self.assertEqual(answer['network'], {'source': 'from-dhcp'})

    def test_rejects_profiles_the_answer_file_cannot_describe(self):
        cases = [
            ('mirror on one disk', lambda vm: vm.pop('extra_disks')),
            ('non-root ssh', lambda vm: vm['ssh_provision'].update(user='lab')),
            ('ide disk', lambda vm: vm['disk'].update(interface='ide')),
            ('two passwords', lambda vm: vm['proxmox_config'].update(root_password='x')),
            ('unknown raid', lambda vm: vm['proxmox_config']['zfs'].update(raid='raid5')),
            ('too little RAM', lambda vm: vm.update(memory_mb=1024)),
        ]
        for label, mutate in cases:
            with self.subTest(label):
                vm = self.profile()
                mutate(vm)
                with self.assertRaises(VMError):
                    proxmox.check_profile('proxmox-ve', vm)

    def test_install_iso_grafts_answer_and_mode_with_boot_records_kept(self):
        vm = self.profile()
        vm['disk']['path'] = str(self.root / 'artifacts/proxmox-ve/disk.qcow2')
        source = self.root / 'isos/pve.iso'
        source.parent.mkdir(parents=True)
        source.write_bytes(b'iso')
        with mock.patch.object(vmctl.runtime, 'run') as run, \
             mock.patch.object(vmctl.runtime, 'require_command'):
            run.side_effect = lambda cmd, **kw: Path(cmd[-1]).write_bytes(b'copy') if cmd[0] == 'cp' else None
            dest = proxmox.ensure_install_iso('proxmox-ve', vm, source, ['ssh-ed25519 TEST'])
            graft = next(call.args[0] for call in run.call_args_list if call.args[0][0] == 'xorriso')
            self.assertEqual(graft[1:4], ['-boot_image', 'any', 'keep'])
            self.assertIn('/answer.toml', graft)
            self.assertIn('/auto-installer-mode.toml', graft)
            self.assertTrue(dest.is_file())
            run.reset_mock()
            proxmox.ensure_install_iso('proxmox-ve', vm, source, ['ssh-ed25519 TEST'])
            run.assert_not_called()  # cached by stamp

    def test_extra_disks_follow_the_main_disk_and_are_created(self):
        vm = self.profile()
        args = qemu.disk_args(vm, allow_missing=True, bootindex=1)
        self.assertIn('virtio-blk-pci,drive=disk0,bootindex=1', args)
        self.assertIn('virtio-blk-pci,drive=disk1', args)
        self.assertGreater(args.index('virtio-blk-pci,drive=disk1'), args.index('virtio-blk-pci,drive=disk0,bootindex=1'))
        with self.assertRaises(VMError):
            qemu.disk_args(vm)  # missing images are an error outside dry runs
        with mock.patch.object(vmctl.runtime, 'run') as run, \
             mock.patch.object(vmctl.runtime, 'require_command'):
            lifecycle.ensure_vm_disk(vm)
        created = [call.args[0][-2] for call in run.call_args_list]
        self.assertEqual([Path(path).name for path in created], ['disk.qcow2', 'disk2.qcow2'])

    def test_clean_removes_extra_disks_and_the_prepared_iso(self):
        vm = self.profile()
        base = self.root / 'artifacts/proxmox-ve'
        for relative in ('disk.qcow2', 'disk2.qcow2', 'OVMF_VARS.fd', 'proxmox/install.iso'):
            (base / relative).parent.mkdir(parents=True, exist_ok=True)
            (base / relative).write_bytes(b'x')
        lifecycle.clean_vm('proxmox-ve', vm)
        self.assertFalse(base.exists())

    def test_single_disk_operations_refuse_extra_disks(self):
        vm = self.profile()
        with self.assertRaises(VMError):
            checkpoint.check_profile('proxmox-ve', vm)
        with self.assertRaises(VMError):
            libvirt.export(argparse.Namespace(vm='proxmox-ve', name=None), vm)

    def test_lab_services_match_the_post_install_and_the_client_bookmarks(self):
        pve, client = self.profile(), self.profile('proxmox-lab-client')
        commands = pve['ssh_provision']['post_install_run']
        segment = ipaddress.ip_network('10.10.10.0/24')
        slirp_dhcp = range(15, 31)
        for service in pve['lab_services']:
            with self.subTest(service['name']):
                expected = (f"/root/pve-community.sh {service['container']} {service['script']} {service['hostname']} "
                            f"{service['port']} {service['nat_address']} {service['address']}")
                self.assertIn(expected, commands)
                self.assertIn(ipaddress.ip_interface(service['address']).ip, segment)
                # Outside slirp's DHCP pool: a lease there once duplicated the host's own 10.0.2.15.
                self.assertNotIn(int(str(ipaddress.ip_interface(service['nat_address']).ip).split('.')[-1]), slirp_dhcp)
                self.assertIn(f'"URL":"{service["url"]}"', json.dumps(client['preseed_config']['late_commands']).replace('\\"', '"'))
        client_lan = next(c for c in client['ssh_provision']['post_install_run'] if 'client-lan.sh' in c)
        self.assertTrue(client_lan.endswith(' '.join(s['url'] for s in pve['lab_services'])))  # one tab per app
        self.assertLess(commands.index(next(c for c in commands if 'pve-repos.sh' in c)),
                        commands.index(next(c for c in commands if 'pve-community.sh' in c)))

    def test_cluster_nodes_are_distinct_and_the_runbook_matches_what_form_runs(self):
        from vmctl import pvecluster
        cfg = {'vms': json.loads((ROOT / 'vms/profiles/proxmox-lab.json').read_text())['vms']}
        names = list(cfg['vms'])
        found = pvecluster.clusters(cfg, names)
        self.assertEqual(found, {'pve-lab': {'primary': 'proxmox-ve',
                                             'nodes': ['proxmox-ve', 'proxmox-ve-node2', 'proxmox-ve-node3']}})
        nodes = [cfg['vms'][name] for name in found['pve-lab']['nodes']]
        for key in (lambda vm: pvecluster.lan_ip(vm), lambda vm: vm['proxmox_config']['fqdn'],
                    lambda vm: vm['ssh_provision']['ssh_host_port'], lambda vm: vm['networks'][1]['mac'],
                    lambda vm: vm['networks'][0]['hostfwd'][0]['host_port'], lambda vm: vm['extra_disks'][0]['path']):
            self.assertEqual(len({key(vm) for vm in nodes}), 3)
        steps = pvecluster.commands(cfg, 'pve-lab', found['pve-lab'])
        self.assertIn(('proxmox-ve', 'pvecm create pve-lab --link0 10.10.10.2'), steps)
        self.assertIn(('proxmox-ve-node3', 'pvecm add 10.10.10.2 --link0 10.10.10.4 --use_ssh'), steps)
        # Only node 1 carries the containers: a node that joins a cluster must hold no guests.
        self.assertEqual([name for name in names if cfg['vms'][name].get('lab_services')], ['proxmox-ve'])

    def test_lab_members_share_the_runtime_segment_only(self):
        for name in ('proxmox-ve', 'proxmox-lab-client'):
            vm = self.profile(name)
            with self.subTest(name):
                self.assertEqual([spec['type'] for spec in qemu.network_specs(vm, 'install')], ['user'])
                runtime_specs = qemu.network_specs(vm, 'runtime')
                self.assertEqual([spec['type'] for spec in runtime_specs], ['user', 'segment'])
                self.assertEqual(runtime_specs[1]['name'], 'pve-lan')
                self.assertEqual(runtime_specs[0]['mac'], qemu.network_specs(vm, 'install')[0]['mac'])
        self.assertIn(8006, qemu.host_ports(self.profile()))


if __name__ == '__main__':
    unittest.main()
