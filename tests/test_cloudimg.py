"""Cloud images (bootstrap-cloudimg): profile checks, the seed, the overlay disk, what counts as data."""
import hashlib
import json
import os
import unittest
from unittest import mock

from tests._common import ROOT, BaseVmctlTestCase
from vmctl import cli, cloudimg, config, iso, lifecycle, runtime, vmstate
from vmctl.errors import VMError

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPROBEKEYPROBEKEYPROBEKEYPROBEKEYPROBE probe"


def tracked_profile():
    return json.loads(json.dumps(config.load_tracked(ROOT / "vms" / "profiles")["ubuntu-24.04-cloud"]))


class CloudImageProfileTests(unittest.TestCase):
    def test_the_profile_is_the_cloud_image_flow_and_passes_its_checks(self):
        vm = tracked_profile()
        self.assertEqual(lifecycle.local_test_mode(vm)[0], "bootstrap-cloudimg")
        cloudimg.check_profile("ubuntu-24.04-cloud", vm)
        self.assertTrue(vm["iso_url"].endswith(vm["iso"].rsplit("/", 1)[1]))
        self.assertIn("SHA256SUMS", vm["iso_sha256_url"])  # a moving release image, pinned by the vendor's file
        for broken, field in ((lambda v: v["disk"].__setitem__("format", "raw"), "disk.format"),
                              (lambda v: v["disk"].__setitem__("interface", "sata"), "disk.interface"),
                              (lambda v: v.__delitem__("ssh_provision"), "ssh_provision"),
                              (lambda v: v["cloudimg_config"].__setitem__("image_format", "vmdk"), "image_format"),
                              (lambda v: v["cloudimg_config"].__delitem__("password_hash"), "password_hash")):
            vm = tracked_profile()
            broken(vm)
            with self.subTest(field=field), self.assertRaises(VMError):
                cloudimg.check_profile("ubuntu-24.04-cloud", vm)

    def test_the_command_is_registered_in_the_unattended_group(self):
        groups = {name: cmds for name, _, cmds in cli.COMMAND_GROUPS}
        self.assertIn("bootstrap-cloudimg", groups["Install unattended"])
        self.assertIn(("cloudimg_config", "username"), config.USER_IDENTITY_FIELDS)
        self.assertEqual(config.locale_values({"language": "it_IT.UTF-8", "keyboard": "it", "timezone": "Europe/Rome"})["cloudimg_config"],
                         {"keyboard": "it", "locale": "it_IT.UTF-8", "timezone": "Europe/Rome"})


class CloudImageSeedTests(unittest.TestCase):
    def test_user_data_carries_user_key_sudo_packages_and_powers_off_after_the_script(self):
        vm = tracked_profile()
        vm["cloudimg_config"]["runcmd"] = ["echo one", ["touch", "/tmp/with space"]]
        vm["cloudimg_config"]["write_files"] = [{"path": "/etc/motd", "content": "hi\n"}]
        text = cloudimg.render_user_data("ubuntu-24.04-cloud", vm, [KEY])
        self.assertTrue(text.startswith("#cloud-config\n"))
        payload = json.loads(text.split("\n", 1)[1])
        user = payload["users"][0]
        self.assertEqual((user["name"], user["sudo"], user["lock_passwd"], user["ssh_authorized_keys"]),
                         ("lab", ["ALL=(ALL) NOPASSWD:ALL"], False, [KEY]))
        self.assertTrue(user["passwd"].startswith("$6$"))
        self.assertEqual((payload["hostname"], payload["fqdn"], payload["prefer_fqdn_over_hostname"]), ("ubuntu-24.04-cloud",) * 2 + (True,))
        self.assertIn("openssh-server", payload["packages"])
        self.assertEqual(payload["runcmd"], [["sh", cloudimg.PROVISION_SCRIPT]])
        self.assertEqual(payload["power_state"]["mode"], "poweroff")
        self.assertEqual((payload["timezone"], payload["locale"], payload["keyboard"]), ("UTC", "en_US.UTF-8", {"layout": "us"}))
        self.assertEqual([f["path"] for f in payload["write_files"]], ["/etc/motd", cloudimg.PROVISION_SCRIPT])
        script = payload["write_files"][-1]["content"]
        order = ["set -eu", cloudimg.BOOTSTRAP_FAILED_TOKEN, "serial-getty@ttyS0", "echo one", "touch '/tmp/with space'",
                 "touch /etc/cloud/cloud-init.disabled", "sync\n", cloudimg.BOOTSTRAP_COMPLETE_TOKEN]
        positions = [script.index(p) for p in order]
        self.assertEqual(positions, sorted(positions), order)
        self.assertTrue(script.rstrip().endswith(f'echo "{cloudimg.BOOTSTRAP_COMPLETE_TOKEN}" | tee /dev/ttyS0'))
        meta = json.loads(cloudimg.render_meta_data("ubuntu-24.04-cloud", vm))
        self.assertEqual(meta, {"instance-id": "vmctl-ubuntu-24.04-cloud", "local-hostname": "ubuntu-24.04-cloud"})



class SegmentNetplanTests(unittest.TestCase):
    def test_a_runtime_segment_nic_with_an_address_gets_a_netplan_by_mac_in_the_seed(self):
        vm = json.loads(json.dumps(config.load_tracked(ROOT / "vms" / "profiles")["vpn-lab-server"]))
        files = cloudimg.segment_netplan(vm)
        self.assertEqual([f["path"] for f in files], ["/etc/netplan/60-vmctl-vpn-lan.yaml"])
        mac = cloudimg.qemu.network_specs(vm, "runtime")[1]["mac"]
        self.assertIn(f'macaddress: "{mac}"', files[0]["content"])
        self.assertIn("- 172.20.1.1/24", files[0]["content"])
        self.assertIn("set-name: vpn-lan", files[0]["content"])
        self.assertEqual(files[0]["permissions"], "0600")
        payload = json.loads(cloudimg.render_user_data("vpn-lab-server", vm, [KEY]).split("\n", 1)[1])
        self.assertEqual(payload["write_files"][0]["path"], "/etc/netplan/60-vmctl-vpn-lan.yaml")
        self.assertEqual(cloudimg.segment_netplan(tracked_profile()), [])  # no networks: nothing to write
        self.assertEqual(lifecycle.local_test_mode(vm)[0], "bootstrap-cloudimg")


class CloudImageIdentityTests(BaseVmctlTestCase):
    def test_the_identity_of_local_json_moves_the_guest_user(self):
        profiles = self.config_dir / "profiles"
        profiles.mkdir(parents=True, exist_ok=True)
        (profiles / "cloud.json").write_text((ROOT / "vms" / "profiles" / "cloud.json").read_text())
        local = {"identity": {"user": "probeuser", "password_hash": "$6$probe$hash", "realname": "Probe"}, "vms": {}}
        vm = config.load_config(local_profiles=local)["vms"]["ubuntu-24.04-cloud"]
        text = cloudimg.render_user_data("ubuntu-24.04-cloud", vm, [KEY])
        self.assertIn('"name": "probeuser"', text)
        self.assertIn('"passwd": "$6$probe$hash"', text)
        self.assertIn("id probeuser", json.dumps(vm["ssh_provision"]["post_install_run"]))
        self.assertNotIn("{{user}}", text)


class CloudImageDiskTests(BaseVmctlTestCase):
    def fake_qcow2(self, path, backing: bool):
        header = bytearray(b"QFI\xfb" + (3).to_bytes(4, "big"))
        header += (0x1000 if backing else 0).to_bytes(8, "big")
        header += b"\0" * 56
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(header))

    def test_an_overlay_counts_as_a_disk_with_data_even_when_small(self):
        base = runtime.vm_artifact_base("cloudvm")
        self.fake_qcow2(base / "disk.qcow2", backing=False)
        self.assertFalse(vmstate.artifact_disk_has_data("cloudvm"))
        self.assertFalse(vmstate.image_facts(base / "disk.qcow2", "qcow2")["has_data"])
        self.fake_qcow2(base / "disk.qcow2", backing=True)
        self.assertTrue(vmstate.qcow2_has_backing_file(base / "disk.qcow2"))
        self.assertTrue(vmstate.artifact_disk_has_data("cloudvm"))
        with mock.patch.object(runtime, "image_info", return_value={"virtual-size": 10}):
            self.assertTrue(vmstate.image_facts(base / "disk.qcow2", "qcow2")["has_data"])
        (base / "other.raw").write_bytes(b"\0" * 32)
        self.assertFalse(vmstate.qcow2_has_backing_file(base / "other.raw"))

    def test_the_base_image_is_kept_by_content_and_the_disk_is_an_overlay_on_it(self):
        vm = tracked_profile()
        source = self.root / "isos" / "ubuntu-24.04-minimal-cloudimg-amd64.img"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"cloud image bytes")
        digest = hashlib.sha256(b"cloud image bytes").hexdigest()
        backing = cloudimg.backing_image(vm, source)
        self.assertEqual(backing, self.root / "isos" / ".cloudimg" / f"{digest}.qcow2")
        self.assertEqual(os.stat(backing).st_ino, os.stat(source).st_ino)  # a hard link, no second copy
        self.assertEqual(cloudimg.backing_image(vm, source, dry_run=True), backing)  # already kept: nothing to do
        source.unlink()  # ensure_iso replacing a stale cache leaves the base of every overlay in place
        self.assertEqual(backing.read_bytes(), b"cloud image bytes")
        with mock.patch.object(runtime, "run") as run:
            disk = cloudimg.create_overlay(vm, backing)
        self.assertEqual(disk, runtime.resolve_path(vm["disk"]["path"]))
        self.assertEqual(run.call_args.args[0], ["qemu-img", "create", "-f", "qcow2", "-b", str(backing), "-F", "qcow2", str(disk), "10G"])

    def test_ensure_vm_disk_builds_the_overlay_for_a_cloud_image_profile(self):
        vm = tracked_profile()
        source = self.root / "isos" / "ubuntu-24.04-minimal-cloudimg-amd64.img"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"img")
        with mock.patch.object(iso, "ensure_iso", return_value=source), mock.patch.object(runtime, "run") as run, \
                mock.patch("shutil.which", return_value="/usr/bin/qemu-img"):
            lifecycle.ensure_vm_disk(vm, vm_name="ubuntu-24.04-cloud")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:5], ["qemu-img", "create", "-f", "qcow2", "-b"])
        self.assertTrue(cmd[5].endswith(f"/isos/.cloudimg/{hashlib.sha256(b'img').hexdigest()}.qcow2"))
        self.assertEqual(cmd[-1], "10G")

    def test_the_bootstrap_dry_run_boots_the_overlay_with_the_seed_and_no_installer(self):
        profiles = self.config_dir / "profiles"
        profiles.mkdir(parents=True, exist_ok=True)
        (profiles / "cloud.json").write_text((ROOT / "vms" / "profiles" / "cloud.json").read_text())
        commands = []

        def fake_run_and_expect(cmd, **kwargs):
            commands.append((cmd, kwargs))

        with mock.patch.object(lifecycle.qemu, "run_and_expect", side_effect=fake_run_and_expect), \
                mock.patch.object(lifecycle, "start_installed_vm_headless"), mock.patch.object(lifecycle, "run_post_install"), \
                mock.patch.object(cloudimg, "resolve_ssh_pubkey", return_value=[KEY]), \
                mock.patch("shutil.which", return_value="/usr/bin/tool"):
            import argparse
            rc = lifecycle.cmd_bootstrap_cloudimg(argparse.Namespace(vm="ubuntu-24.04-cloud", dry_run=True, timeout=600))
        self.assertEqual(rc, 0)
        (cmd, kwargs), = commands
        self.assertEqual(kwargs["expected_text"], cloudimg.BOOTSTRAP_COMPLETE_TOKEN)
        self.assertEqual(kwargs["exit_grace_sec"], cloudimg.SHUTDOWN_GRACE_SEC)
        self.assertNotIn("-kernel", cmd)
        self.assertNotIn("-cdrom", cmd)
        self.assertIn("-no-reboot", cmd)
        seed = [a for a in cmd if "cloudimg/seed.iso" in a]
        self.assertEqual(len(seed), 1, cmd)
        self.assertIn("format=raw,if=virtio,readonly=on", seed[0])


class PublishedSumsTests(unittest.TestCase):
    def test_a_sums_file_with_several_entries_gives_the_hash_of_this_image(self):
        ours, other = "a" * 64, "b" * 64
        text = f"{other} *ubuntu-24.04-minimal-cloudimg-amd64.manifest\n{ours} *ubuntu-24.04-minimal-cloudimg-amd64.img\n"
        vm = {"iso": "isos/ubuntu-24.04-minimal-cloudimg-amd64.img", "iso_sha256_url": "https://example.test/SHA256SUMS"}
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = text.encode()
        with mock.patch("urllib.request.urlopen", return_value=response):
            self.assertEqual(iso.published_sha256(vm), ours)
        # openSUSE's one-line .sha256 keeps working: the first hash when no name matches.
        response.__enter__.return_value.read.return_value = f"{other}  openSUSE-Tumbleweed-NET-x86_64-Current.iso\n".encode()
        with mock.patch("urllib.request.urlopen", return_value=response):
            self.assertEqual(iso.published_sha256({"iso": "isos/x.iso", "iso_sha256_url": "https://example.test/x.sha256"}), other)
