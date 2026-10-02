"""vms/labs/<lab>/: the lab content loaded, validated and drawn into the runbook and the map."""
import argparse
import contextlib
import io
import json
import shutil
import subprocess
from unittest import mock

from tests._common import ROOT, BaseVmctlTestCase
from vmctl import config, labs, lifecycle, state
from vmctl.errors import VMError


class LabContentTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        profiles = self.root / "vms" / "profiles"
        profiles.mkdir(parents=True, exist_ok=True)
        for source in (ROOT / "vms" / "profiles").glob("*.json"):
            if source.name != "local.json":
                shutil.copy(source, profiles / source.name)
        shutil.copytree(ROOT / "vms" / "labs", self.root / "vms" / "labs")
        self.profiles = profiles

    def cfg(self):
        with mock.patch.object(state, "CONFIG_DIR", self.profiles.parent):
            return config.load_config()

    def write_lab(self, group, document):
        directory = self.root / "vms" / "labs" / group
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "lab.json").write_text(json.dumps(document))
        return directory

    def test_every_tracked_lab_directory_matches_its_group_and_its_scripts_are_sound(self):
        cfg = self.cfg()
        tracked = labs.content_groups()
        self.assertIn("netlab", tracked)
        for group in tracked:
            content = labs.load_content(group)
            self.assertEqual(set(content["members"]), set(labs.group_members(cfg, group)), group)
            self.assertEqual(sorted(content["guides"]), ["en", "it"], f"{group}: both guides, kept in sync")
            self.assertTrue(content["exercises"], f"{group}: at least one exercise")
            for script in content["tests"]:
                path = ROOT / content["dir"] / "tests" / script
                self.assertTrue(path.stat().st_mode & 0o100, f"{path} is not executable")
                text = path.read_text()
                self.assertIn('source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"', text)
                self.assertIn("report_results", text)
                subprocess.run(["bash", "-n", str(path)], check=True)
            self.assertIn(group, labs.lab_groups(cfg))

    def test_the_exercises_join_the_runbook_before_the_stack_and_reach_the_map(self):
        lab = labs.model(self.cfg(), "netlab")
        titles = [phase["title"] for phase in lab["runbook"]]
        self.assertEqual(titles[-3:], ["DNS and DHCP from Pi-hole", "Through the firewall", "Run the stack"])
        dns = lab["runbook"][-3]
        self.assertEqual([b["where"] for b in dns["blocks"]], ["pihole-lab", "lubuntu-lab", "pihole-lab"])
        self.assertTrue(dns["blocks"][0]["ssh"].startswith("ssh -i artifacts/pihole-lab/ssh/"))
        self.assertEqual(lab["runbook"][-2]["blocks"][-1]["ssh"], "")  # the host block
        page = labs.render_html(lab)
        self.assertIn("<h1>Network lab: pfSense, Pi-hole and a client</h1>", page)
        self.assertIn("vms/labs/netlab/guide.it.md", page)
        self.assertIn("2 test script(s)", page)
        self.assertIn("pihole-FTL --config dhcp.active", page)

    def test_a_lab_with_content_is_a_lab_even_without_a_segment(self):
        (self.profiles / "solo.json").write_text(json.dumps({"vms": {"solo-server": {
            "name": "Solo", "meta": {"family": "debian", "status": "manual", "manual": "todo", "role": "server", "groups": ["solo-lab"]},
            "iso": "isos/solo.iso", "iso_help": "none", "disk": {"path": "artifacts/solo-server/disk.qcow2", "size": "4G", "format": "qcow2", "interface": "virtio"},
            "firmware": {"type": "bios"}, "video": {"default": "std", "variants": {"std": ["-vga", "std"]}}, "memory_mb": 512, "cpus": 1,
            "ssh_provision": {"user": "lab", "ssh_host_port": 2998}}}}))
        self.write_lab("solo-lab", {"title": "Solo", "members": ["solo-server"],
                                    "exercises": [{"title": "Look", "blocks": [{"kind": "check", "where": "solo-server", "commands": ["uptime"]}]}]})
        cfg = self.cfg()
        self.assertIn("solo-lab", labs.lab_groups(cfg))
        lab = labs.model(cfg, "solo-lab")
        self.assertEqual(lab["segments"], [])
        self.assertEqual([phase["title"] for phase in lab["runbook"]][-2:], ["Look", "Run the stack"])

    def test_validation_names_the_bad_field(self):
        for document, message in (
            ({"members": ["pfsense-lab"]}, "'title' is required"),
            ({"title": "x", "members": []}, "'members' must be"),
            ({"title": "x", "members": ["pfsense-lab"], "exercises": [{"text": "no title"}]}, "needs a title"),
            ({"title": "x", "members": ["pfsense-lab"], "exercises": [{"title": "t", "blocks": [{"kind": "wipe", "commands": ["x"]}]}]}, "'kind' must be"),
            ({"title": "x", "members": ["pfsense-lab"], "exercises": [{"title": "t", "blocks": [{"where": "elsewhere", "commands": ["x"]}]}]}, "'where' must be host or a member"),
            ({"title": "x", "members": ["pfsense-lab"], "exercises": [{"title": "t", "blocks": [{"where": "host", "commands": []}]}]}, "'commands' must be"),
        ):
            self.write_lab("bad-lab", document)
            with self.subTest(message=message), self.assertRaisesRegex(VMError, message):
                labs.load_content("bad-lab")
        (self.root / "vms" / "labs" / "bad-lab" / "lab.json").write_text("{not json")
        with self.assertRaisesRegex(VMError, "bad-lab/lab.json"):
            labs.load_content("bad-lab")
        self.assertIsNone(labs.load_content("no-such-lab"))

    def test_members_of_lab_json_must_agree_with_the_profiles(self):
        self.write_lab("netlab", {"title": "x", "members": ["pfsense-lab", "pihole-lab"]})
        with self.assertRaisesRegex(VMError, "must agree"):
            labs.model(self.cfg(), "netlab")

    def test_group_guide_prints_the_requested_language_and_list_carries_the_title(self):
        cfg = self.cfg()
        with mock.patch.object(config, "load_config", return_value=cfg), mock.patch.object(lifecycle, "group_states", return_value={}):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = lifecycle.cmd_group(argparse.Namespace(action="guide", group="netlab", lang="it", json=False, dry_run=False))
            self.assertEqual(rc, 0)
            self.assertTrue(out.getvalue().startswith("# Lab di rete"))
            with self.assertRaisesRegex(VMError, "no guide"):
                lifecycle.cmd_group(argparse.Namespace(action="guide", group="proxmox-lab", lang=None, json=False, dry_run=False))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                lifecycle.cmd_group(argparse.Namespace(action="list", group=None, labs=True, json=True, dry_run=False))
            entries = {entry["group"]: entry for entry in json.loads(out.getvalue())}
            self.assertEqual((entries["netlab"]["title"], entries["netlab"]["tests"], entries["netlab"]["guides"]),
                             ("Network lab: pfSense, Pi-hole and a client", 2, ["en", "it"]))
            self.assertNotIn("title", entries["proxmox-lab"])
