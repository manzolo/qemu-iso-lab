"""The catalog site: built from the tracked catalog only, self-contained, every profile on it."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import build_catalog_site
from vmctl import profile_versions

ROOT = Path(__file__).resolve().parents[1]


class CatalogSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tempdir.name) / "site"
        cls.data = build_catalog_site.build(ROOT, cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def test_every_tracked_profile_is_on_the_page_with_its_version_and_commands(self):
        names = {p["name"] for p in self.data["profiles"]}
        self.assertEqual(names, set(profile_versions.tracked_entries(ROOT)))
        html = (self.out / "index.html").read_text(encoding="utf-8")
        catalog = json.loads((self.out / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual({p["name"] for p in catalog["profiles"]}, names)
        for profile in self.data["profiles"]:
            with self.subTest(profile=profile["name"]):
                self.assertIn(profile["name"], html)
                self.assertRegex(profile["version"] or "", r"^\d+\.\d+\.\d+$")
                self.assertTrue(profile["history"], "the lock has a history line for every profile")
                self.assertTrue(profile["commands"] and all(c.startswith("vmctl ") for c in profile["commands"]))
                self.assertIn(profile["medium"], ("public", "manual", "image", "none"))
                if profile["status"] == "unattended":
                    self.assertTrue(profile["flow"].startswith("bootstrap-"), f"{profile['name']} is unattended without a flow")
        self.assertEqual(self.data["counts"]["profiles"], len(names))

    def test_iso_link_status_reaches_the_cards(self):
        # the Pages workflow runs tools/check_iso_urls.py --json and hands it to the build
        links = Path(self.tempdir.name) / "links.json"
        links.write_text(json.dumps({"profiles": [
            {"vm": "debian-server", "verdict": "BROKEN", "sources": [{"url": "https://x/a.iso", "good": False, "reason": "HTTP 404"}]},
            {"vm": "kali", "verdict": "OK", "sources": [{"url": "https://x/k.iso", "good": True, "reason": "3000 MiB"}]},
        ], "user_supplied": []}))
        out = Path(self.tempdir.name) / "site-links"
        data = build_catalog_site.build(ROOT, out, links=links)
        by_name = {p["name"]: p for p in data["profiles"]}
        self.assertEqual(by_name["debian-server"]["link"], {"verdict": "BROKEN", "why": "https://x/a.iso: HTTP 404"})
        self.assertEqual(by_name["kali"]["link"]["verdict"], "OK")
        self.assertIsNone(by_name["alpine-ci"]["link"])
        self.assertIsNotNone(data["links_checked"])
        page = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("ISO link broken", page)
        self.assertIn('id="links-chip"', page)
        self.assertIsNone(self.data["links_checked"])  # without --links there is no badge at all

    def test_the_tour_page_comes_with_its_clips_and_only_then(self):
        media = Path(self.tempdir.name) / "media"
        (media / "tour").mkdir(parents=True)
        clip = {"id": "01-x", "title": {"en": "1 · X", "it": "1 · X it"}, "duration": 12.0, "video": "01-x.mp4",
                "poster": "01-x.jpg", "subtitles": {"en": "01-x.en.vtt", "it": "01-x.it.vtt"}}
        voiced = {**clip, "id": "02-y", "video": {"it": "02-y.it.mp4", "en": "02-y.en.mp4"}, "series": "labs", "lab": "lvm-lab", "steps": [{"t": 3.5, "cmd": "sudo pvs"}]}  # narrated: one file per language; a lab lesson with its commands
        for name in ("01-x.mp4", "01-x.jpg", "01-x.en.vtt", "01-x.it.vtt", "02-y.it.mp4", "02-y.en.mp4"):
            (media / "tour" / name).write_bytes(b"x")
        (media / "tour" / "tour.json").write_text(json.dumps({"clips": [clip, voiced]}))
        out = Path(self.tempdir.name) / "site-tour"
        build_catalog_site.build(ROOT, out, media=media)
        tour = (out / "tour.html").read_text(encoding="utf-8")
        self.assertIn('"video": "tour/01-x.mp4"', tour)
        self.assertIn('"it": "tour/01-x.it.vtt"', tour)
        self.assertTrue((out / "tour" / "01-x.en.vtt").is_file())
        self.assertIn('"video": {"it": "tour/02-y.it.mp4", "en": "tour/02-y.en.mp4"}', tour)
        self.assertTrue((out / "tour" / "02-y.en.mp4").is_file())
        # The lesson reaches the lab's card in the catalog, and only that lab's.
        labs = {lab["group"]: lab for lab in json.loads((out / "catalog.json").read_text(encoding="utf-8"))["labs"]}
        self.assertEqual(labs["lvm-lab"]["clip"], "tour.html#02-y")
        self.assertIsNone(labs["vpn-lab"]["clip"])
        self.assertIn("Watch the lesson", (out / "labs" / "lvm-lab" / "guide.en.html").read_text(encoding="utf-8"))
        self.assertIn('"guides": {"en": "labs/lvm-lab/guide.en.html"', tour)  # the lesson links its guide
        self.assertIn('"steps": [{"t": 3.5, "cmd": "sudo pvs"}]', tour)  # and its commands, for the player's panel
        self.assertIn('href="tour.html"', (out / "index.html").read_text(encoding="utf-8"))
        bare = Path(self.tempdir.name) / "site-bare"
        build_catalog_site.build(ROOT, bare, media=Path(self.tempdir.name) / "no-media")
        self.assertFalse((bare / "tour.html").exists())
        self.assertNotIn("tour.html", (bare / "index.html").read_text(encoding="utf-8"))

    def test_the_labs_are_on_the_page_with_members_tests_and_guides(self):
        labs = {lab["group"]: lab for lab in self.data["labs"]}
        self.assertEqual(set(labs) >= {"netlab", "vpn-lab", "lvm-lab", "proxmox-lab"}, True)
        lvm = labs["lvm-lab"]
        self.assertEqual([m["name"] for m in lvm["members"]], ["lvm-lab-server"])
        self.assertEqual(lvm["tests"], 7)
        self.assertGreater(lvm["exercises"], 0)
        self.assertEqual(set(lvm["guides"]), {"en", "it"})
        # The guides are pages of the site, rendered from the tracked markdown, the source a link away.
        self.assertEqual(lvm["guides"]["en"], "labs/lvm-lab/guide.en.html")
        guide = (self.out / "labs" / "lvm-lab" / "guide.it.html").read_text(encoding="utf-8")
        self.assertIn("<h1>", guide)
        self.assertIn("lvcreate", guide)
        self.assertIn('href="guide.en.html">EN</a>', guide)
        self.assertTrue(lvm["source"].endswith("/tree/main/vms/labs/lvm-lab"))
        self.assertTrue(labs["proxmox-lab"]["cluster"])
        self.assertEqual(labs["proxmox-lab"]["tests"], 0)  # a lab by its segment, without content: no tests, no guides
        page = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn('<section id="labs"', page)
        self.assertIn("function labCard", page)

    def test_the_page_is_self_contained_and_ships_the_dashboard_icons(self):
        html = (self.out / "index.html").read_text(encoding="utf-8")
        for asset in ("icons.js", ".nojekyll", "catalog.json"):
            self.assertTrue((self.out / asset).exists(), asset)
        self.assertEqual((self.out / "icons.js").read_bytes(), (ROOT / "vmctl" / "web" / "icons.js").read_bytes())
        self.assertNotIn("http://", html.split("<script id=\"data\"")[0].replace("http://www.w3.org", ""))
        self.assertNotIn("cdn.", html)
        self.assertIn('<script src="icons.js">', html)
        self.assertIn('window.ICON_SPRITE = ""', html)  # the sprite is inlined: icons work from file:// too
        self.assertIn('<symbol id="arch"', html)
        self.assertNotIn("</script>", json.dumps(self.data).replace("</", "<\\/"))  # the embedded JSON cannot close its tag

    def test_commands_follow_the_kind_of_profile(self):
        by_name = {p["name"]: p for p in self.data["profiles"]}
        self.assertEqual(by_name["debian-server"]["commands"], ["vmctl bootstrap-preseed debian-server", "vmctl start debian-server"])
        self.assertEqual(by_name["windows-11"]["medium"], "manual")
        self.assertEqual(by_name["serenityos"]["medium"], "image")
        self.assertEqual(by_name["serenityos"]["commands"][0], "vmctl prep serenityos")
        self.assertTrue(by_name["pfsense-lab"]["lab"])
        self.assertEqual(by_name["ubuntu-26.04"]["family_label"], "Debian, Ubuntu and flavours")

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise browser search")
    def test_search_matches_os_releases_and_ranks_the_requested_profile_first(self):
        # Execute the page's actual search against the tracked catalog, including notes
        # and family labels that previously made "ubuntu 26" match old Debian profiles.
        search = "function searchProfiles" + build_catalog_site.PAGE.split("function searchProfiles", 1)[1].split("function visible", 1)[0]
        queries = ["ubuntu 26", "UBUNTU-26.04", "ubuntu", "debian 7", "fedora kde", "arch", "", "no-such-os-zzzzz"]
        script = search + "\nconst profiles = " + json.dumps(self.data["profiles"]) + ";\n"
        script += "console.log(JSON.stringify(" + json.dumps(queries) + ".map(q => searchProfiles(profiles, q).map(p => p.name))));"
        result = subprocess.run(["node"], input=script, capture_output=True, text=True, check=True, timeout=60)  # a busy CI runner took more than 10 s (v0.14.0 on Python 3.13)
        release, exact, ubuntu, debian, desktop, arch, all_profiles, empty = json.loads(result.stdout)
        self.assertEqual(release[0], "ubuntu-26.04")
        by_name = {p["name"]: p for p in self.data["profiles"]}
        self.assertTrue(all("ubuntu" in name and "26.04" in by_name[name]["label"] for name in release), release)
        self.assertIn("kubuntu-26.04", release)
        self.assertNotIn("ubuntu-24.04", release)
        self.assertEqual(exact[0], "ubuntu-26.04")
        self.assertTrue(ubuntu)
        self.assertFalse(any(name.startswith("debian") for name in ubuntu))
        self.assertIn("debian-7", debian)
        self.assertTrue(desktop)
        self.assertTrue(all("fedora" in name for name in desktop))
        self.assertTrue(arch)
        self.assertIn("arch-2026", arch)
        self.assertFalse(any(name.startswith("debian") for name in arch))
        self.assertEqual(len(all_profiles), len(self.data["profiles"]))
        self.assertEqual(empty, [])
