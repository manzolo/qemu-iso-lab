import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'vms/profile-files/common/bin/verify-desktop'


class DesktopReadinessTests(unittest.TestCase):
    def probe(self, *, target='graphical.target', service=True, package='install ok installed',
              sessions=None, compositor=False, delayed=False):
        if sessions is None:
            sessions = [{'Name': 'lab', 'Active': 'yes', 'Remote': 'no', 'Class': 'user', 'Type': 'wayland'}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = root / 'tools'
            tools.mkdir()
            fixture = root / 'fixture.json'
            fixture.write_text(json.dumps(dict(target=target, service=service, package=package,
                                               sessions=sessions, compositor=compositor, delayed=delayed)))
            fake = '''import json, os, pathlib, sys
cfg = json.loads(pathlib.Path(os.environ['FIXTURE']).read_text())
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
if name == 'systemctl':
    if args == ['get-default']:
        print(cfg['target'])
    else:
        counter = pathlib.Path(os.environ['HOME']) / 'service-probed'
        if cfg['delayed'] and not counter.exists():
            counter.touch()
            sys.exit(1)
        sys.exit(0 if cfg['service'] else 1)
elif name == 'dpkg-query':
    print(cfg['package'], end='')
elif name == 'loginctl':
    if args[0] == 'list-sessions':
        for i in range(len(cfg['sessions'])):
            print(i, '1000 lab seat0 tty1')
    else:
        for k, v in cfg['sessions'][int(args[1])].items():
            print(k + '=' + v)
elif name == 'pgrep':
    sys.exit(0 if cfg['compositor'] and args == ['-u', 'lab', '-x', 'niri'] else 1)
else:
    sys.exit(99)
'''
            for name in ('systemctl', 'dpkg-query', 'loginctl', 'pgrep'):
                path = tools / name
                path.write_text(f'#!{sys.executable}\n' + fake)
                path.chmod(0o755)
            env = {'PATH': str(tools) + os.pathsep + os.defpath, 'HOME': tmp,
                   'LC_ALL': 'C.UTF-8', 'FIXTURE': str(fixture)}
            command = ['/bin/sh', str(SCRIPT), '--user', 'lab', '--package-manager', 'dpkg',
                       '--package', 'ubuntu-budgie-desktop', '--attempts', '2', '--interval', '0']
            if compositor:
                command += ['--compositor', 'niri']
            return subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)

    def test_active_display_manager_without_autologin_fails(self):
        result = self.probe(sessions=[{'Name': 'lightdm', 'Active': 'yes', 'Remote': 'no',
                                      'Class': 'greeter', 'Type': 'x11'}])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('graphical session for lab is missing', result.stderr)

    def test_ssh_inactive_wrong_user_and_non_graphical_sessions_fail(self):
        base = {'Name': 'lab', 'Active': 'yes', 'Remote': 'no', 'Class': 'user', 'Type': 'wayland'}
        for change in ({'Remote': 'yes'}, {'Active': 'no'}, {'Name': 'another-user'},
                       {'Type': 'tty'}, {'Class': 'greeter'}):
            with self.subTest(change=change):
                self.assertNotEqual(self.probe(sessions=[base | change]).returncode, 0)

    def test_graphical_user_session_passes_after_delayed_service_start(self):
        result = self.probe(delayed=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Desktop ready: lab', result.stdout)

    def test_greetd_tty_requires_user_owned_compositor(self):
        session = {'Name': 'lab', 'Active': 'yes', 'Remote': 'no', 'Class': 'user', 'Type': 'tty'}
        self.assertEqual(self.probe(sessions=[session], compositor=True).returncode, 0)
        self.assertNotEqual(self.probe(sessions=[session]).returncode, 0)

    def test_wrong_target_stopped_manager_and_removed_package_fail(self):
        for settings in ({'target': 'multi-user.target'}, {'service': False},
                         {'package': 'deinstall ok config-files'}):
            with self.subTest(settings=settings):
                self.assertNotEqual(self.probe(**settings).returncode, 0)

    def test_all_flavors_use_shared_assertive_checks(self):
        profiles = json.loads((ROOT / 'vms/profiles/ubuntu-flavors.json').read_text())['vms']
        for vm in profiles.values():
            cfg = vm['ssh_provision']
            self.assertTrue(any('verify-desktop --user "{{user}}"' in c for c in cfg['post_install_run']))
            self.assertNotIn('systemctl get-default', cfg['post_install_run'])
            self.assertTrue(any(e['source'].endswith('/verify-desktop') for e in cfg['copy_from_host']))
