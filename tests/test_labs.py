from _common import *
from vmctl import config, labs, lifecycle, state


class LabsTests(BaseVmctlTestCase):
    def tracked_config(self):
        # Tracked profiles only, copied into the temp config dir; never the host's local.json.
        target = self.root / "vms/profiles"
        target.mkdir(parents=True, exist_ok=True)
        for source in (ROOT / "vms/profiles").glob("*.json"):
            if source.name != "local.json":
                shutil.copy(source, target / source.name)
        with mock.patch.object(state, "CONFIG_DIR", target.parent):
            return config.load_config()

    def test_only_groups_whose_members_all_share_a_segment_are_labs(self):
        cfg = self.tracked_config()
        self.assertEqual(labs.lab_groups(cfg), ["netlab", "proxmox-lab"])

    def test_start_order_puts_infrastructure_then_services_first(self):
        cfg = self.tracked_config()
        self.assertEqual(labs.model(cfg, "netlab")["start_order"], ["pfsense-lab", "pihole-lab", "lubuntu-lab"])
        self.assertEqual(labs.model(cfg, "proxmox-lab")["start_order"],
                         ["proxmox-ve", "proxmox-ve-node2", "proxmox-ve-node3", "proxmox-lab-client"])

    def test_model_reads_addresses_forwards_and_segments_from_the_profiles(self):
        cfg = self.tracked_config()
        lab = labs.model(cfg, "proxmox-lab", {"proxmox-ve": {"running": True, "install": "verified"}})
        pve = lab["members"][0]
        self.assertTrue(pve["running"])
        self.assertEqual(pve["disks"], 2)
        nat, lan = pve["nics"]
        self.assertEqual({f["host_port"] for f in nat["forwards"]}, {2276, 8006})
        self.assertEqual((lan["segment"], lan["address"]), ("pve-lan", "10.10.10.2/24"))
        self.assertEqual(lab["segments"], [{"name": "pve-lan", "subnet": "10.10.10.0/24",
                                            "members": ["proxmox-ve", "proxmox-ve-node2", "proxmox-ve-node3",
                                                        "proxmox-lab-client"]}])
        netlab = labs.model(cfg, "netlab")
        router = netlab["members"][0]
        self.assertEqual(router["nics"][1]["address"], "192.168.0.1/24")
        described = [f["what"] for f in router["nics"][0]["forwards"] if f["what"]]
        self.assertTrue(any("pihole-lab" in text for text in described))

    def test_runbook_is_ordered_and_its_checks_are_read_only(self):
        cfg = self.tracked_config()
        runbook = labs.model(cfg, "proxmox-lab")["runbook"]
        self.assertEqual([phase["title"] for phase in runbook],
                         ["Install", "Lab network", "ZFS pool (mirror)", "LXC containers", "Cluster pve-lab",
                          "From the client", "Run the stack"])
        writes = ("pvecm create", "pvecm add", "pct set", "pct migrate", "zpool offline", "zpool scrub",
                  "pve-community.sh", "vmctl group install", ">>")
        for phase in runbook:
            for block in phase["blocks"]:
                self.assertIn(block["kind"], ("do", "check", "try"))
                if block["kind"] == "check":
                    for command in block["commands"]:
                        self.assertFalse(any(word in command for word in writes), command)
                if block["where"] not in ("host",) and not block["where"].startswith("answer.toml"):
                    self.assertTrue(block["ssh"].startswith(f"ssh -i artifacts/{block['where']}/ssh/"), block)
        pool = next(phase for phase in runbook if phase["title"].startswith("ZFS"))
        self.assertEqual({b["where"] for b in pool["blocks"] if b["kind"] == "check"},
                         {"proxmox-ve", "proxmox-ve-node2", "proxmox-ve-node3"})
        drill = next(b for b in pool["blocks"] if b["kind"] == "try")
        self.assertLess(next(i for i, c in enumerate(drill["commands"]) if "zpool offline" in c),
                        next(i for i, c in enumerate(drill["commands"]) if "zpool online" in c))
        self.assertEqual([phase["title"] for phase in labs.model(cfg, "netlab")["runbook"]],
                         ["Install", "Lab network", "Run the stack"])

    def test_map_is_self_contained_and_escapes_profile_text(self):
        cfg = self.tracked_config()
        lab = labs.model(cfg, "proxmox-lab")
        lab["members"][0]["label"] = "<script>alert(1)</script>"
        page = labs.render_html(lab)
        self.assertIn("<svg", page)
        self.assertNotIn("<script>", page)
        self.assertNotIn("http://cdn", page)
        self.assertIn('href="https://127.0.0.1:8006/"', page)
        self.assertIn("segment pve-lan", page)

    def test_group_up_skips_members_without_a_disk_and_starts_in_order(self):
        cfg = self.tracked_config()
        states = {name: {"running": False, "install": "verified"} for name in labs.group_members(cfg, "proxmox-lab")}
        states["proxmox-lab-client"] = {"running": False, "install": "no disk"}
        with mock.patch.object(lifecycle.config, "load_config", return_value=cfg), \
             mock.patch.object(lifecycle, "group_states", return_value=states), \
             mock.patch.object(lifecycle, "cmd_start") as start:
            lifecycle.cmd_group(argparse.Namespace(action="up", group="proxmox-lab", labs=False, json=False,
                                                   open=False, output=None, dry_run=False))
        self.assertEqual([call.args[0].vm for call in start.call_args_list],
                         ["proxmox-ve", "proxmox-ve-node2", "proxmox-ve-node3"])  # the client has no disk
        self.assertTrue(start.call_args.args[0].headless and start.call_args.args[0].background)


class GroupInstallTests(BaseVmctlTestCase):
    tracked_config = LabsTests.tracked_config

    def run_group(self, action, states, **extra):
        cfg = self.tracked_config()
        # Members a test does not mention are installed and stopped.
        states = {name: {"running": False, "install": "verified"}
                  for name in labs.group_members(cfg, "proxmox-lab")} | states
        namespace = dict(action=action, group="proxmox-lab", labs=False, json=False, open=False, output=None,
                         dry_run=False, timeout=60, yes=False)
        namespace.update(extra)
        with mock.patch.object(lifecycle.config, "load_config", return_value=cfg), \
             mock.patch.object(lifecycle, "group_states", return_value=states), \
             mock.patch.object(lifecycle, "run_local_test_vm", return_value=("passed", "ok")) as install, \
             mock.patch.object(lifecycle, "clean_vm") as clean, \
             mock.patch.object(lifecycle, "cmd_stop") as stop, \
             mock.patch.object(lifecycle, "cmd_start") as start, \
             mock.patch.object(lifecycle.pvecluster, "form") as form, \
             mock.patch.object(lifecycle.runtime, "confirm_default_no", return_value=extra.get("_answer", False)) as ask:
            try:
                lifecycle.cmd_group(argparse.Namespace(**{k: v for k, v in namespace.items() if not k.startswith("_")}))
                error = None
            except lifecycle.VMError as exc:
                error = exc
        # The cross-VM step reaches the guests over SSH: never in a unit test (CI has no lab).
        self.last_form = form
        return error, install, clean, stop, start, ask

    def test_install_keeps_installed_members_and_installs_the_rest_in_start_order(self):
        states = {"proxmox-ve": {"running": True, "install": "verified"},
                  "proxmox-lab-client": {"running": False, "install": "no disk"}}
        error, install, clean, stop, start, ask = self.run_group("install", states)
        self.assertIsNone(error)
        self.assertEqual([call.args[0] for call in install.call_args_list], ["proxmox-lab-client"])
        clean.assert_not_called()
        ask.assert_not_called()  # nothing to delete: no question
        self.assertEqual(stop.call_args_list[0].args[0].vm, "proxmox-ve")  # restart in the runtime phase
        self.last_form.assert_called_once()  # then the cluster, once the stack is up

    def test_install_asks_before_redoing_an_unfinished_member(self):
        states = {"proxmox-ve": {"running": False, "install": "incomplete"},
                  "proxmox-lab-client": {"running": False, "install": "verified"}}
        error, install, clean, *_ = self.run_group("install", states)
        self.assertIsNotNone(error)
        install.assert_not_called()
        clean.assert_not_called()
        error, install, clean, *_ = self.run_group("install", states, yes=True)
        self.assertIsNone(error)
        self.assertEqual([call.args[0] for call in clean.call_args_list], ["proxmox-ve"])
        self.assertEqual([call.args[0] for call in install.call_args_list], ["proxmox-ve"])

    def test_clean_asks_then_stops_and_cleans_in_reverse_order(self):
        states = {"proxmox-ve": {"running": True, "install": "verified"},
                  "proxmox-lab-client": {"running": False, "install": "verified"}}
        error, _, clean, *_ = self.run_group("clean", states)
        self.assertIsNotNone(error)
        clean.assert_not_called()
        error, _, clean, stop, *_ = self.run_group("clean", states, _answer=True)
        self.assertIsNone(error)
        self.assertEqual([call.args[0] for call in clean.call_args_list],
                         ["proxmox-lab-client", "proxmox-ve-node3", "proxmox-ve-node2", "proxmox-ve"])
        self.assertEqual([call.args[0].vm for call in stop.call_args_list], ["proxmox-ve"])
