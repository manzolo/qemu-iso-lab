from _common import *
from pathlib import Path
from unittest import mock
from vmctl.errors import VMError
from vmctl import haiku, lifecycle, qemu


class HaikuTests(BaseVmctlTestCase):
    def profile(self, name='haiku'):
        # Tracked profile file only; never consult the host's local.json.
        return json.loads((ROOT / 'vms/profiles/haiku.json').read_text())['vms'][name]

    def test_manual_and_unattended_profiles_pick_their_flows(self):
        self.assertEqual(lifecycle.local_test_mode(self.profile())[0], 'bootstrap-haiku')
        self.assertNotEqual(lifecycle.local_test_mode(self.profile('haiku-installer'))[0], 'bootstrap-haiku')

    def test_profile_checks_refuse_what_the_script_cannot_drive(self):
        for section, key, value in [('firmware', 'type', 'efi'), ('disk', 'interface', 'virtio'),
                                    (None, 'usb_tablet', False), ('ssh_provision', 'user', 'lab')]:
            with self.subTest(key=key):
                vm = self.profile()
                (vm[section] if section else vm)[key] = value
                with self.assertRaises(VMError):
                    haiku.render_install_script('test', vm, [])

    def test_script_flushes_and_unmounts_before_the_token_then_powers_off(self):
        script = haiku.render_install_script('test', self.profile(), ['ssh-ed25519 TEST'])
        order = [script.rindex(marker) for marker in
                 ('makebootable $T', 'unmount $T', 'set +x', f'echo "{haiku.BOOTSTRAP_COMPLETE_TOKEN}"', 'shutdown -q\n')]
        self.assertEqual(order, sorted(order))
        self.assertIn('ssh-ed25519 TEST', script)
        self.assertIn('config/settings/ssh/authorized_keys', script)
        # The failure path reports and still powers off, without the trace echoing the token first.
        self.assertIn(f'fail() {{ set +x; sync; echo "{haiku.BOOTSTRAP_FAILED_TOKEN}: $1"; shutdown -q', script)

    def test_typed_line_avoids_dead_keys_and_every_char_is_typeable(self):
        line = haiku.typed_command()
        self.assertFalse(set(line) & set('\'"`~^'))
        self.assertTrue(all(haiku.keys_for(char) for char in line))
        self.assertEqual(haiku.keys_for('_'), ['shift', 'minus'])
        with self.assertRaises(VMError):
            haiku.keys_for('"')

    def test_ppm_parsing_and_pixel_matching(self):
        width, height = haiku.SCREEN
        pixels = bytearray(bytes((51, 102, 152)) * width * height)
        for (x, y), colour in haiku.DESKTOP.items():
            pixels[(y * width + x) * 3:(y * width + x) * 3 + 3] = bytes(colour)
        path = Path(self.tempdir.name) / 'frame.ppm'
        path.write_bytes(b'P6\n# comment\n%d %d\n255\n' % (width, height) + bytes(pixels))
        frame = haiku.read_ppm(path)
        self.assertTrue(haiku.matches(frame, haiku.DESKTOP))
        self.assertFalse(haiku.matches(frame, haiku.WELCOME))
        self.assertFalse(haiku.matches((640, 480, b''), haiku.DESKTOP))

    def test_pilot_walks_every_screen_then_types_the_line(self):
        pilot = haiku.Pilot(Path(self.tempdir.name) / 'qmp.sock', Path(self.tempdir.name),
                            log=lambda _: None, sleep=lambda _: None)
        screens = iter([None, haiku.WELCOME, haiku.DESKTOP, haiku.DESKBAR_MENU, haiku.TERMINAL])
        current = {}
        def wait_for(name, expected, timeout_sec=0):
            state = next(screens)
            while state is None:
                state = next(screens)
            self.assertIs(state, expected, name)
        sent = []
        with mock.patch.object(pilot, 'wait_for', side_effect=wait_for), \
             mock.patch.object(qemu, 'qmp_command', side_effect=lambda sock, cmd, **kw: sent.append((cmd, kw.get('arguments'))) or True):
            pilot.drive()
        keys = [args['keys'] for cmd, args in sent if cmd == 'send-key']
        names = [[key['data'] for key in combo] for combo in keys if combo != [{'type': 'qcode', 'data': 'shift'}]]
        letters = ''.join(combo[-1].upper() if combo[0] == 'shift' else combo[-1] for combo in names
                          if len(combo[-1]) == 1 and combo[-1].isalpha())
        self.assertTrue(letters.startswith('Terminal'))
        self.assertIn('mountvolumeVMCTLSEEDshVMCTLSEEDinstallsh', letters)
        self.assertEqual(names[-1], ['ret'])
        self.assertEqual(sum(1 for cmd, args in sent if cmd == 'input-send-event'
                             and args['events'][0]['type'] == 'btn') , 6)  # three clicks, down and up

    def test_pilot_failure_is_reported_and_ends_qemu(self):
        pilot = haiku.Pilot(Path(self.tempdir.name) / 'qmp.sock', Path(self.tempdir.name),
                            log=lambda _: None, sleep=lambda _: None)
        with mock.patch.object(pilot, 'drive', side_effect=VMError('the live session never showed the desktop')), \
             mock.patch.object(qemu, 'qmp_command', return_value=True) as qmp, \
             mock.patch('sys.stdout', new_callable=io.StringIO):
            pilot.run()
        self.assertIn('desktop', pilot.error)
        qmp.assert_called_with(pilot.qmp_socket, 'quit')
