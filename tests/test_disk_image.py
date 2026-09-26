"""disk_image profiles (SerenityOS): the medium is a prepared disk image, never an ISO."""

import argparse
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.config  # noqa: E402
import vmctl.iso  # noqa: E402
import vmctl.lifecycle  # noqa: E402
import vmctl.runtime  # noqa: E402
import vmctl.vmstate  # noqa: E402
from vmctl.errors import VMError  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class DiskImageTests(BaseVmctlTestCase):
    def image_vm(self, **extra):
        vm = dict(self.vm_config)
        vm.pop("iso", None)
        vm["disk_image"] = {"path": "isos/os.img", "format": "raw", "help": "Build it with tools/build_x.sh."}
        vm.update(extra)
        return vm

    def test_an_image_profile_needs_no_iso_but_not_both(self):
        self.assertEqual(vmctl.config.validate_vm_profile("img", self.image_vm()), [])
        both = self.image_vm(iso="isos/x.iso")
        self.assertTrue(any("not both" in e for e in vmctl.config.validate_vm_profile("img", both)))
        bad = self.image_vm(disk_image={"path": "isos/os.img", "format": "vdi"})
        self.assertTrue(any("raw or qcow2" in e for e in vmctl.config.validate_vm_profile("img", bad)))

    def test_a_raw_image_is_never_validated_as_an_iso(self):
        # ensure_iso deletes an "invalid cached ISO": a raw disk image must not go near that check.
        image = self.root / "isos/os.img"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"\0" * 4096)  # no CD001 anywhere
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(vmctl.iso.ensure_iso(self.image_vm()), image)
        self.assertTrue(image.exists())
        self.assertEqual(vmctl.iso.iso_source_kind(self.image_vm()), "cached")

    def test_a_missing_image_explains_how_to_build_it(self):
        with self.assertRaisesRegex(VMError, "has to be built first") as ctx:
            vmctl.iso.ensure_iso(self.image_vm())
        self.assertIn("tools/build_x.sh", str(ctx.exception))
        self.assertIn('"disk_image.path"', str(ctx.exception))
        self.assertEqual(vmctl.iso.iso_source_kind(self.image_vm()), "manual")

    def test_prep_converts_the_image_into_the_disk_and_records_its_origin(self):
        image = self.root / "isos/os.img"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"\0" * 4096)
        with mock.patch.object(vmctl.runtime, "run") as run, mock.patch("sys.stdout", new_callable=io.StringIO):
            vmctl.lifecycle.ensure_vm_disk(self.image_vm(), vm_name=self.vm_name)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["qemu-img", "convert"])
        self.assertEqual(command[-2:], [str(image), str(self.root / self.vm_config["disk"]["path"])])
        self.assertEqual(vmctl.vmstate.load(self.vm_name)["origin"]["kind"], "image")

    def test_install_and_provision_refuse_an_image_profile(self):
        self.vm_config = self.image_vm()
        self.write_config_dir()
        for handler in (vmctl.lifecycle.cmd_install, vmctl.lifecycle.cmd_provision):
            with self.subTest(handler=handler.__name__), self.assertRaisesRegex(VMError, "boots a prepared disk image"):
                handler(argparse.Namespace(vm=self.vm_name, dry_run=True, video=None, spice_port=None, cloud_init=False))


if __name__ == "__main__":
    unittest.main()
