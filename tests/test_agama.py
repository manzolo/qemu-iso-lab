"""openSUSE Leap 16 on Agama: the profile on the OEMDRV seed, its post scripts and the checks."""
import json
import unittest

from tests._common import ROOT
from vmctl import agama, config, lifecycle
from vmctl.errors import VMError


class AgamaTests(unittest.TestCase):
    def profile(self):
        return json.loads(json.dumps(config.load_tracked(ROOT / "vms" / "profiles")["opensuse-leap-16"]))

    def test_the_tracked_profile_is_the_agama_flow_on_virtio(self):
        vm = self.profile()
        self.assertEqual(lifecycle.local_test_mode(vm)[0], "bootstrap-agama")
        agama.check_profile("opensuse-leap-16", vm)
        with self.assertRaises(VMError):
            agama.check_profile("opensuse-leap-16", {**vm, "disk": {**vm["disk"], "interface": "ide"}})

    def test_the_seed_is_labelled_where_agama_looks_and_the_kernel_line_powers_off(self):
        self.assertEqual((agama.SEED_VOLUME_ID, agama.PROFILE_NAME), ("OEMDRV", "autoinst.json"))
        self.assertIn("inst.finish=poweroff", agama.KERNEL_APPEND)
        self.assertIn("console=ttyS0,115200", agama.KERNEL_APPEND)
        self.assertNotIn("inst.auto", agama.KERNEL_APPEND)  # found by label, nothing to point at

    def test_profile_names_product_disk_hashed_credentials_and_the_scripts_in_order(self):
        vm = self.profile()
        vm["agama_config"]["username"] = "probe"
        prof = json.loads(agama.render_profile("opensuse-leap-16", vm, ["ssh-ed25519 KEY probe"]))
        self.assertEqual(prof["product"]["id"], "openSUSE_Leap")
        self.assertEqual(prof["storage"]["drives"][0]["search"], "/dev/vda")
        self.assertEqual((prof["user"]["userName"], prof["user"]["hashedPassword"]), ("probe", True))
        self.assertTrue(prof["user"]["password"].startswith("$6$"))
        self.assertTrue(prof["root"]["hashedPassword"])
        configure, finish = prof["scripts"]["post"]
        self.assertTrue(configure["chroot"])
        self.assertIn("ssh-ed25519 KEY probe", configure["content"])
        self.assertIn("probe ALL=(ALL) NOPASSWD: ALL", configure["content"])
        self.assertIn('DISPLAYMANAGER_AUTOLOGIN=\\"probe\\"', json.dumps(configure["content"]))
        self.assertFalse(finish["chroot"])
        body = finish["content"]
        self.assertLess(body.index("sync"), body.index("blockdev --flushbufs"))
        self.assertLess(body.index("blockdev --flushbufs"), body.index(agama.BOOTSTRAP_COMPLETE_TOKEN))


if __name__ == "__main__":
    unittest.main()
