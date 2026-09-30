"""OPNsense from its DVD: the config.xml of ours, the start hook and the profile checks."""
import base64
import unittest
import xml.etree.ElementTree as ET

from tests._common import ROOT
from vmctl import config, lifecycle, opnsense
from vmctl.errors import VMError


class OpnsenseTests(unittest.TestCase):
    def profile(self):
        import json
        return json.loads(json.dumps(config.load_tracked(ROOT / "vms" / "profiles")["opnsense"]))

    def test_the_tracked_profile_is_the_dvd_flow_on_bios_virtio(self):
        vm = self.profile()
        self.assertEqual(lifecycle.local_test_mode(vm)[0], "bootstrap-opnsense")
        opnsense.check_profile("opnsense", vm)
        for key, value in (("firmware", {"type": "efi"}), ("disk", {**vm["disk"], "interface": "ide"})):
            with self.subTest(key=key):
                broken = {**vm, key: value}
                with self.assertRaises(VMError):
                    opnsense.check_profile("opnsense", broken)

    def test_config_xml_has_one_wan_ssh_for_the_admin_user_and_the_wan_rule(self):
        vm = self.profile()
        vm["opnsense_config"]["username"] = "probe"
        root = ET.fromstring(opnsense.render_config_xml("opnsense", vm, ["ssh-ed25519 KEY probe"], password_hash="$2y$10$x"))
        self.assertEqual(root.findtext("interfaces/wan/if"), "vtnet0")
        self.assertEqual(root.findtext("interfaces/wan/ipaddr"), "dhcp")
        self.assertEqual(root.findtext("interfaces/wan/blockpriv"), "0")  # QEMU's user network is private
        self.assertIsNone(root.find("interfaces/lan"))
        self.assertEqual(root.findtext("system/ssh/enabled"), "enabled")
        self.assertEqual(root.findtext("system/sudo_allow_wheel"), "2")
        users = {u.findtext("name"): u for u in root.findall("system/user")}
        self.assertEqual(users["probe"].findtext("shell"), "/bin/sh")
        self.assertEqual(base64.b64decode(users["probe"].findtext("authorizedkeys")).decode(), "ssh-ed25519 KEY probe\n")
        self.assertIn("2000", [m.text for m in root.findall("system/group/member")])
        rule = root.find("filter/rule")
        self.assertEqual((rule.findtext("interface"), rule.findtext("destination/port")), ("wan", "22"))
        self.assertEqual(root.findtext("system/primaryconsole"), "serial")

    def test_the_hook_clones_installs_the_config_removes_itself_and_ends_token_then_poweroff(self):
        hook = opnsense.render_install_hook()
        order = ["gpart create -s gpt", "gpart bootcode -b /boot/pmbr -p /boot/gptboot -i 1", "newfs -U -L rootfs",
                 "cpdup -i0 -o -s0", f"cp {opnsense.CONFIG_STAGE} /mnt/conf/config.xml", f"rm -f /mnt{opnsense.HOOK_PATH}",
                 "run umount /mnt", opnsense.BOOTSTRAP_COMPLETE_TOKEN, "/sbin/shutdown -p now\nVMCTL_EOF", "/usr/sbin/daemon -f /bin/sh /tmp/vmctl-install.sh"]
        positions = [hook.index(piece) for piece in order]
        self.assertEqual(positions, sorted(positions), order)
        self.assertIn('failed() {', hook)
        self.assertNotIn("set -e", hook)  # FreeBSD sh has no ERR trap: run() names the failing step instead
        self.assertIn("[ -f /tmp/vmctl-install-started ] && exit 0", hook)
