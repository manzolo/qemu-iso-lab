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


class LabTestRunnerTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.profiles = self.root / "vms" / "profiles"
        self.profiles.mkdir(parents=True, exist_ok=True)
        for source in (ROOT / "vms" / "profiles").glob("*.json"):
            if source.name != "local.json":
                shutil.copy(source, self.profiles / source.name)
        (self.root / "bin").mkdir(exist_ok=True)
        (self.root / "bin" / "vmctl").write_text("#!/bin/sh\necho stub\n")
        self.lab = self.root / "vms" / "labs" / "netlab"
        (self.lab / "tests").mkdir(parents=True)
        (self.lab / "lab.json").write_text(json.dumps({"title": "Net", "members": ["pfsense-lab", "pihole-lab", "lubuntu-lab"],
                                                       "exercises": [{"title": "x"}]}))
        scripts = {
            "test_01_ok.sh": 'echo "  [PASS] one"; echo "  [PASS] two"; echo "vmctl=$VMCTL"; exit 0\n',
            "test_02_fail.sh": 'echo "  [PASS] one"; echo "  [FAIL] two (expected: x)"; exit 1\n',
            "test_03_crash.sh": 'echo "  [PASS] one"; exit 127\n',
        }
        for name, body in scripts.items():
            (self.lab / "tests" / name).write_text("#!/usr/bin/env bash\n" + body)

    def cfg(self):
        with mock.patch.object(state, "CONFIG_DIR", self.profiles.parent):
            return config.load_config()

    def test_scripts_run_in_order_and_their_outcome_is_told_from_their_exit_status(self):
        content = labs.load_content("netlab")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            results = labs.run_lab_tests(content)
        self.assertEqual([(r["script"], r["status"], r["passed"], r["failed"], r["exit"]) for r in results],
                         [("test_01_ok.sh", "passed", 2, 0, 0), ("test_02_fail.sh", "failed", 1, 1, 1), ("test_03_crash.sh", "error", 1, 0, 127)])
        self.assertIn(f"vmctl={self.root / 'bin' / 'vmctl'}", out.getvalue())
        self.assertEqual(labs.tests_summary(results), ("failed", "1/3 scripts passed, 4 checks passed, 1 failed; script error: test_03_crash.sh"))
        self.assertEqual(labs.tests_summary(results[:1]), ("passed", "1/1 scripts passed, 2 checks passed, 0 failed"))
        self.assertEqual(labs.run_lab_tests(content, dry_run=True)[0]["status"], "skipped")

    def test_group_test_starts_a_stopped_stack_waits_for_ssh_and_exits_1_on_a_failure(self):
        cfg = self.cfg()
        states = {name: {"running": False, "install": "verified"} for name in labs.group_members(cfg, "netlab")}
        states["pfsense-lab"]["running"] = True
        namespace = dict(action="test", group="netlab", labs=False, json=False, open=False, output=None, dry_run=False, timeout=3600, yes=False, lang=None)
        with mock.patch.object(lifecycle.config, "load_config", return_value=cfg), \
             mock.patch.object(lifecycle, "group_states", return_value=states), \
             mock.patch.object(lifecycle, "cmd_start") as start, \
             mock.patch.object(lifecycle.ssh, "wait_for_ssh") as wait, \
             contextlib.redirect_stdout(io.StringIO()):
            rc = lifecycle.cmd_group(argparse.Namespace(**namespace))
        self.assertEqual(rc, 1)
        self.assertEqual([call.args[0].vm for call in start.call_args_list], ["pihole-lab", "lubuntu-lab"])
        self.assertEqual(wait.call_count, 3)
        self.assertEqual(wait.call_args.args[1], 600)
        for script in ("test_02_fail.sh", "test_03_crash.sh"):
            (self.lab / "tests" / script).unlink()
        out = io.StringIO()
        with mock.patch.object(lifecycle.config, "load_config", return_value=cfg), \
             mock.patch.object(lifecycle, "group_states", return_value={n: {"running": True, "install": "verified"} for n in states}), \
             mock.patch.object(lifecycle, "cmd_start") as start, mock.patch.object(lifecycle.ssh, "wait_for_ssh"), \
             contextlib.redirect_stdout(out):
            rc = lifecycle.cmd_group(argparse.Namespace(**{**namespace, "json": True}))
        self.assertEqual(rc, 0)
        start.assert_not_called()
        payload = json.loads(out.getvalue())
        self.assertEqual((payload["status"], payload["scripts"][0]["script"]), ("passed", "test_01_ok.sh"))

    def test_check_vms_adds_a_lab_row_when_every_member_was_in_the_run(self):
        cfg = self.cfg()
        members = ["pfsense-lab", "pihole-lab", "lubuntu-lab"]
        args = argparse.Namespace(dry_run=False, timeout=60, _report_dir=None)
        results = [(name, "passed", "ok") for name in members]
        with mock.patch.object(lifecycle, "group_states", return_value={n: {"running": False} for n in members}), \
             mock.patch.object(lifecycle, "cmd_start") as start, mock.patch.object(lifecycle, "cmd_stop") as stop, \
             mock.patch.object(lifecycle.ssh, "wait_for_ssh"), mock.patch.object(lifecycle.report, "record_group_row") as record, \
             contextlib.redirect_stdout(io.StringIO()):
            lifecycle.run_lab_test_rows(cfg, members, results, args)
        self.assertEqual(results[-1][:2], ("lab-netlab", "failed"))
        self.assertEqual([c.args[0].vm for c in start.call_args_list], members)
        self.assertEqual([c.args[0].vm for c in stop.call_args_list], list(reversed(members)))
        self.assertEqual(record.call_args.args[:2], ("lab-netlab", "Lab netlab: 3 test script(s) over pfsense-lab + pihole-lab + lubuntu-lab"))
        # A member that did not pass: the row is a skip, nothing starts.
        results = [(name, "passed", "ok") for name in members[:-1]] + [("lubuntu-lab", "failed", "no")]
        with mock.patch.object(lifecycle, "cmd_start") as start, mock.patch.object(lifecycle.report, "record_group_row"), \
             contextlib.redirect_stdout(io.StringIO()):
            lifecycle.run_lab_test_rows(cfg, members, results, args)
        self.assertEqual(results[-1][:2], ("lab-netlab", "skipped"))
        start.assert_not_called()
        # Only some members in the run: no row at all.
        results = [("pihole-lab", "passed", "ok")]
        lifecycle.run_lab_test_rows(cfg, ["pihole-lab"], results, args)
        self.assertEqual(len(results), 1)


class GuidePageTests(BaseVmctlTestCase):
    def test_markdown_is_escaped_and_only_http_links_are_links(self):
        out = labs.render_markdown("# T <b>\n\nA <script>x</script> [ok](https://e.org) [rel](../x.md) **b** `<i>`\n\n"
                                   "| a | b |\n|---|---|\n| 1 | `2` |\n\n- one\n- two\n\n```\n<x> & y\n```\n\n> note")
        self.assertIn("<h1>T &lt;b&gt;</h1>", out)
        self.assertIn("A &lt;script&gt;x&lt;/script&gt;", out)
        self.assertIn('<a href="https://e.org" target="_blank" rel="noopener">ok</a>', out)
        self.assertIn(" rel <b>b</b> <code>&lt;i&gt;</code>", out)
        self.assertIn("<th>a</th><th>b</th></tr></thead><tbody><tr><td>1</td><td><code>2</code></td>", out)
        self.assertIn("<ul><li>one</li><li>two</li></ul>", out)
        self.assertIn("<pre>&lt;x&gt; &amp; y</pre>", out)
        self.assertIn("<blockquote>note</blockquote>", out)
        self.assertNotIn("javascript:", labs.render_markdown("[x](javascript:alert(1))"))

    def test_every_tracked_guide_renders_and_the_page_links_the_other_language(self):
        shutil.copytree(ROOT / "vms" / "labs", self.root / "vms" / "labs")
        for group in labs.content_groups():
            content = labs.load_content(group)
            for lang in content["guides"]:
                page = labs.guide_page(content, lang, "TOK")
                with self.subTest(group=group, lang=lang):
                    self.assertIn("<h1>", page)
                    self.assertNotIn("```", page)
                    other = "it" if lang == "en" else "en"
                    self.assertIn(f'href="?lang={other}&amp;token=TOK"', page)
        from vmctl import webui
        self.assertIsNone(webui.lab_guide_page("../etc", "en"))
        self.assertIsNone(webui.lab_guide_page("no-such-lab", "en"))
        self.assertIn(b'<html lang="it">', webui.lab_guide_page("netlab", "it"))
        self.assertIn(b'<html lang="en">', webui.lab_guide_page("netlab", "fr"))  # unknown language: English
