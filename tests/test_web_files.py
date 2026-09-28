"""File exchange against an isolated local SFTP server, never a real VM."""
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vmctl import web_files
from vmctl.errors import VMError

SFTP_SERVER = next((str(path) for path in (Path('/usr/lib/openssh/sftp-server'), Path('/usr/lib/ssh/sftp-server')) if path.is_file()), None)


class PacketTests(unittest.TestCase):
    def test_truncated_and_oversized_fields_are_rejected(self):
        for data in (b'', b'\0\0', web_files.uint(100) + b'x'):
            with self.subTest(data=data), self.assertRaises(VMError):
                web_files.Packet(data).string()

    def test_upload_names_are_single_components(self):
        for name in ('', '.', '..', '../escape', 'a/b', 'a\\b', 'line\nbreak', 'a\0b', 'x' * 241):
            with self.subTest(name=name), self.assertRaises(VMError):
                web_files.validate_name(name)
        self.assertEqual(web_files.validate_name('café $(touch pwned).txt'), 'café $(touch pwned).txt')

    def test_ssh_subsystem_uses_existing_key_options_without_shell(self):
        with mock.patch.object(web_files.ssh, 'ssh_base_cmd', return_value=['ssh', '-i', '/key', '-p', '2222', 'lab@127.0.0.1']):
            client = web_files.SFTP({})
        self.assertEqual(client.command[-3:], ['-s', 'lab@127.0.0.1', 'sftp'])
        self.assertIn('/key', client.command)
        self.assertIn('ConnectTimeout=8', client.command)


@unittest.skipUnless(SFTP_SERVER, 'OpenSSH sftp-server is not installed')
class SFTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with mock.patch.object(web_files.ssh, 'ssh_base_cmd', return_value=['ssh', 'fixture']):
            self.client = web_files.SFTP({}, timeout=10)
        self.client.command = [SFTP_SERVER, '-d', str(self.root)]

    def test_binary_round_trip_and_duplicate_names_preserve_original(self):
        name = 'café $(touch pwned); "quote".bin'
        payload = bytes(range(256)) * 400
        with self.client as client:
            first = client.upload('.', name, io.BytesIO(payload), len(payload))
            second = client.upload('.', name, io.BytesIO(b'new'), 3)
            self.assertEqual(first['name'], name)
            self.assertNotEqual(second['name'], name)
            target = io.BytesIO()
            self.assertEqual(client.download(first['path'], target), len(payload))
            self.assertEqual(target.getvalue(), payload)
            listing = client.listing('.')
            self.assertEqual(listing['home'], str(self.root))
            self.assertEqual({entry['name'] for entry in listing['entries']}, {first['name'], second['name']})
        self.assertFalse((self.root / 'pwned').exists())
        self.assertEqual((self.root / second['name']).read_bytes(), b'new')
        self.assertFalse(list(self.root.glob('.vmctl-upload-*')))

    def test_empty_file_and_nested_directory_navigation(self):
        (self.root / 'folder with spaces').mkdir()
        with self.client as client:
            directory = client.listing('folder with spaces')
            self.assertEqual(directory['parent'], str(self.root))
            saved = client.upload(directory['path'], 'empty', io.BytesIO(b''), 0)
            target = io.BytesIO()
            self.assertEqual(client.download(saved['path'], target), 0)
            self.assertEqual(client.listing('.')['entries'][0]['kind'], 'directory')

    def test_interrupted_upload_cleans_staging_and_never_replaces_a_file(self):
        (self.root / 'keep.txt').write_text('original')
        with self.client as client:
            with self.assertRaisesRegex(VMError, 'interrupted'):
                client.upload('.', 'keep.txt', io.BytesIO(b'short'), 100)
        self.assertEqual((self.root / 'keep.txt').read_text(), 'original')
        self.assertFalse(list(self.root.glob('.vmctl-upload-*')))

    def test_download_refuses_symlinks_directories_and_oversized_files(self):
        (self.root / 'original').write_bytes(b'12345')
        (self.root / 'link').symlink_to(self.root / 'original')
        with self.client as client:
            for path in ('link', '.'):
                with self.subTest(path=path), self.assertRaisesRegex(VMError, 'regular file'):
                    client.download(str(self.root / path), io.BytesIO())
            with mock.patch.object(web_files, 'MAX_FILE_SIZE', 4), self.assertRaisesRegex(VMError, 'no larger'):
                client.download(str(self.root / 'original'), io.BytesIO())
            with self.assertRaises(web_files.SFTPError):
                client.listing('does not exist')

    def test_directory_listing_is_bounded_and_marks_truncation(self):
        for name in ('a', 'b', 'c'):
            (self.root / name).touch()
        with self.client as client, mock.patch.object(web_files, 'MAX_ENTRIES', 2):
            listing = client.listing('.')
        self.assertEqual(len(listing['entries']), 2)
        self.assertTrue(listing['truncated'])

    def test_rename_failure_removes_staging(self):
        original = self.client.request
        def request(kind, *args, **kwargs):
            if kind == 18:
                raise web_files.SFTPError(3, 'permission denied')
            return original(kind, *args, **kwargs)
        with self.client as client, mock.patch.object(client, 'request', side_effect=request):
            with self.assertRaises(VMError):
                client.upload('.', 'test.txt', io.BytesIO(b'x'), 1)
        self.assertEqual(list(self.root.iterdir()), [])
