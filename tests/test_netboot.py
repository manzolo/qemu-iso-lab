"""Network boot (bootstrap-netboot, vmctl/netboot.py): the bootable NIC, the profile checks, the
answers vmctl hands the lab's PXE server and the dry run of the flow. No QEMU, no guest."""
import argparse
import json
import unittest
from unittest import mock

from tests._common import ROOT, BaseVmctlTestCase
from vmctl import cli, config, iso, lifecycle, netboot, preseed, qemu
from vmctl.errors import VMError

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPROBEKEYPROBEKEYPROBEKEYPROBEKEYPROBE probe"


def tracked() -> dict:
    return {"vms": json.loads(json.dumps(config.load_tracked(ROOT / "vms" / "profiles")))}


class BootNicTests(unittest.TestCase):
    def vm(self, networks):
        return {"disk": {"path": "artifacts/x/disk.qcow2"}, "networks": networks}

    def test_only_the_boot_nic_gets_a_bootindex_and_only_when_asked(self):
        vm = self.vm([{"id": "lan", "type": "segment", "name": "pxe-lan", "boot": True},
                      {"id": "nat", "type": "user", "ssh": True}])
        devices = [a for a in qemu.network_args(vm, "install", nic_bootindex=1) if a.startswith("virtio-net-pci")]
        self.assertTrue(devices[0].endswith(",bootindex=1"), devices)
        self.assertNotIn("bootindex", devices[1])
        self.assertFalse(any("bootindex" in a for a in qemu.network_args(vm, "install")))  # vmctl start: the disk boots

    def test_two_boot_nics_are_refused(self):
        vm = self.vm([{"type": "segment", "name": "a", "boot": True}, {"type": "segment", "name": "b", "boot": True}])
        with self.assertRaises(VMError):
            qemu.network_specs(vm, "install")

    def test_a_profile_without_networks_has_no_boot_nic(self):
        spec, = qemu.network_specs({"disk": {"path": "d.qcow2"}}, "install")
        self.assertFalse(spec["boot"])


class NetbootProfileTests(unittest.TestCase):
    def test_the_pxe_lab_client_is_the_netboot_flow_and_passes_its_checks(self):
        cfg = tracked()
        vm = cfg["vms"]["pxe-lab-client"]
        self.assertEqual(lifecycle.local_test_mode(vm)[0], "bootstrap-netboot")
        nb = netboot.check_profile(cfg, "pxe-lab-client", vm)
        self.assertEqual(nb["server"], "pxe-lab-server")
        self.assertEqual(netboot.boot_nic(vm)["name"], "pxe-lan")
        self.assertNotIn("iso", vm)

    def test_the_checks_name_what_is_wrong(self):
        for broken, why in ((lambda v: v["netboot_config"].__setitem__("server", "no-such-vm"), "server"),
                            (lambda v: v["netboot_config"].__setitem__("server", "pxe-lab-client"), "itself"),
                            (lambda v: v.__delitem__("preseed_config"), "preseed"),
                            (lambda v: v["networks"][0].__setitem__("name", "other-lan"), "segment the server is not on"),
                            (lambda v: v["networks"][0].__setitem__("boot", False), "no boot NIC"),
                            (lambda v: v["netboot_config"].__setitem__("payload_dir", "../etc"), "payload_dir"),
                            (lambda v: v["netboot_config"].__setitem__("payload_url", "ftp://x/"), "payload_url")):
            cfg = tracked()
            broken(cfg["vms"]["pxe-lab-client"])
            with self.subTest(why=why), self.assertRaises(VMError):
                netboot.check_profile(cfg, "pxe-lab-client", cfg["vms"]["pxe-lab-client"])

    def test_the_boot_nic_must_be_on_a_segment_not_slirp(self):
        cfg = tracked()
        vm = cfg["vms"]["pxe-lab-client"]
        vm["networks"] = [{"id": "nat", "type": "user", "ssh": True, "boot": True}]
        with self.assertRaises(VMError):
            netboot.boot_nic(vm)

    def test_the_profile_has_no_medium_to_fetch(self):
        vm = tracked()["vms"]["pxe-lab-client"]
        self.assertFalse(iso.has_medium(vm))
        self.assertEqual(iso.iso_source_kind(vm), "network")
        self.assertEqual(lifecycle.iso_status(vm), "-")
        with self.assertRaises(VMError):
            iso.ensure_iso(vm, dry_run=True)

    def test_the_command_is_registered_in_the_unattended_group(self):
        groups = {name: cmds for name, _, cmds in cli.COMMAND_GROUPS}
        self.assertIn("bootstrap-netboot", groups["Install unattended"])


class NetbootPayloadTests(unittest.TestCase):
    def test_the_preseed_fetches_its_late_script_from_the_server_and_the_script_ends_with_the_token(self):
        vm = tracked()["vms"]["pxe-lab-client"]
        with mock.patch.object(preseed, "_resolve_ssh_pubkey", return_value=KEY):
            files = netboot.payloads("pxe-lab-client", vm)
        self.assertEqual(set(files), set(netboot.PAYLOADS))
        self.assertIn("d-i preseed/late_command string wget -q -O /target/tmp/late_command.sh http://172.20.9.1/debian/late_command.sh;",
                      files["preseed.cfg"])
        self.assertNotIn("cp /late_command.sh", files["preseed.cfg"])
        late = files["late_command.sh"]
        self.assertIn(KEY, late)
        # BIBBIA: sync and flush before the token, the guest powers itself off afterwards.
        self.assertLess(late.index("sync"), late.index(netboot.BOOTSTRAP_COMPLETE_TOKEN))
        self.assertIn("allow-hotplug", late)  # every NIC on DHCP: the host's SSH comes over the second one

    def test_the_answers_reach_the_web_root_whole_and_as_root(self):
        server = tracked()["vms"]["pxe-lab-server"]
        with mock.patch("vmctl.ssh.resolve_ssh_private_key", return_value=None):
            cmd = netboot.upload_command(server, "/srv/pxe/www/debian", "preseed.cfg", dry_run=True)
        remote = cmd[-1]
        self.assertTrue(remote.startswith("sudo sh -lc "), remote)
        self.assertIn("preseed.cfg.vmctl-tmp", remote)
        self.assertIn("mv -f", remote)
        self.assertIn("chmod 644", remote)


class NetbootDryRunTests(BaseVmctlTestCase):
    def test_the_dry_run_boots_the_netboot_nic_first_and_leaves_the_disk_as_every_boot_has_it(self):
        profiles = self.config_dir / "profiles"
        profiles.mkdir(parents=True, exist_ok=True)
        for name in ("pxe-lab.json", "cloud.json"):
            (profiles / name).write_text((ROOT / "vms" / "profiles" / name).read_text())
        commands = []
        uploads = []

        def fake_run(cmd, **kwargs):
            if kwargs.get("stdin_text") is not None:
                uploads.append(kwargs["stdin_text"])

        with mock.patch.object(lifecycle.qemu, "run_and_expect", side_effect=lambda cmd, **kw: commands.append((cmd, kw))), \
                mock.patch.object(lifecycle.runtime, "run", side_effect=fake_run), \
                mock.patch.object(lifecycle.runtime, "run_background", return_value=None), \
                mock.patch.object(lifecycle, "run_post_install"), \
                mock.patch.object(lifecycle, "cmd_start") as start, \
                mock.patch.object(preseed, "_resolve_ssh_pubkey", return_value=KEY), \
                mock.patch("vmctl.ssh.resolve_ssh_private_key", return_value=None), \
                mock.patch("shutil.which", return_value="/usr/bin/tool"):
            rc = lifecycle.cmd_bootstrap_netboot(argparse.Namespace(vm="pxe-lab-client", dry_run=True, timeout=600))
        self.assertEqual(rc, 0)
        start.assert_not_called()  # a dry run never starts the server
        self.assertEqual(len(uploads), 2)
        (cmd, kwargs), = commands
        self.assertEqual(kwargs["expected_text"], netboot.BOOTSTRAP_COMPLETE_TOKEN)
        line = " ".join(cmd)
        # The disk keeps if=virtio: a bootindex there moves every NIC one PCI slot and renames them.
        self.assertRegex(line, r"-drive file=\S+disk\.qcow2,format=qcow2,if=virtio")
        self.assertRegex(line, r"netdev=lan,mac=[0-9a-f:]+,bootindex=1")
        self.assertEqual(line.count("bootindex"), 1)
        self.assertIn("-no-reboot", cmd)
        for absent in ("-kernel", "-cdrom", "-initrd"):
            self.assertNotIn(absent, cmd)


if __name__ == "__main__":
    unittest.main()
