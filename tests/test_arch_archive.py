"""The Arch Linux Archive helper: install closure and the patient host-side cache, no network."""
import io
import tarfile
import urllib.error
from unittest import mock

from tests._common import BaseVmctlTestCase
from vmctl import arch_archive
from vmctl.errors import VMError


def db(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, fields in entries.items():
            body = "".join(f"%{key}%\n" + "\n".join(values) + "\n\n" for key, values in fields.items()).encode()
            info = tarfile.TarInfo(f"{name}/desc")
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


class ArchArchiveTests(BaseVmctlTestCase):
    def test_the_closure_follows_groups_dependencies_and_provides(self):
        packages = {
            "base": {"GROUPS": [], "DEPENDS": []},
            "bash": {"DEPENDS": ["readline>=6"]},
            "readline": {},
            "mesa": {"PROVIDES": ["libgl"]},
            "xorg-server": {"DEPENDS": ["libgl", "bash"]},
            "coreutils": {"GROUPS": ["core-group"]},
        }
        closure, missing = arch_archive.resolve(["core-group", "xorg-server", "nowhere"], packages)
        self.assertEqual(sorted(closure), ["bash", "coreutils", "mesa", "readline", "xorg-server"])
        self.assertEqual(missing, ["nowhere"])

    def test_prefetch_retries_a_flaky_file_and_keeps_what_it_has(self):
        core = db({"linux-3.12-1": {"FILENAME": ["linux-3.12-1-x86_64.pkg.tar.xz"], "CSIZE": ["4"], "DEPENDS": []}})
        calls = {"linux": 0}

        def fetch(url, timeout=60):
            if url.endswith("core.db"):
                return core
            if url.endswith(".db"):
                raise urllib.error.HTTPError(url, 404, "no repo", {}, None)
            calls["linux"] += 1
            if calls["linux"] < 3:  # archive.org: 500, 500, then the file
                raise urllib.error.HTTPError(url, 500, "flaky", {}, None)
            return b"DATA"

        cfg = {"packages": [], "kernels": ["linux"], "bootloader": "none"}
        with mock.patch.object(arch_archive, "BASE_PACKAGES", ()), mock.patch.object(arch_archive, "fetch", side_effect=fetch), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            directory = arch_archive.prefetch("2014/01/05", cfg, delay=0)
            self.assertEqual((directory / "linux-3.12-1-x86_64.pkg.tar.xz").read_bytes(), b"DATA")
            self.assertEqual(calls["linux"], 3)
            arch_archive.prefetch("2014/01/05", cfg, delay=0)  # cached: nothing downloaded again
            self.assertEqual(calls["linux"], 3)
        self.assertEqual(directory, self.root / "isos" / "arch-archive" / "2014-01-05")

    def test_prefetch_names_what_the_archive_never_served(self):
        core = db({"linux-3.12-1": {"FILENAME": ["linux-3.12-1-x86_64.pkg.tar.xz"], "CSIZE": ["4"]}})

        def fetch(url, timeout=60):
            if url.endswith("core.db"):
                return core
            raise urllib.error.HTTPError(url, 404 if url.endswith(".db") else 500, "x", {}, None)

        cfg = {"packages": [], "kernels": ["linux"], "bootloader": "none"}
        with mock.patch.object(arch_archive, "BASE_PACKAGES", ()), mock.patch.object(arch_archive, "fetch", side_effect=fetch), \
             mock.patch("sys.stdout", new_callable=io.StringIO), self.assertRaisesRegex(VMError, r"linux \(HTTP 500\)"):
            arch_archive.prefetch("2014/01/05", cfg, attempts=2, delay=0)
        with mock.patch.object(arch_archive, "fetch", side_effect=fetch), self.assertRaisesRegex(VMError, "Not in the"):
            arch_archive.prefetch("2014/01/05", {"packages": ["ghost"], "kernels": [], "bootloader": "none"}, delay=0)
