"""The catalog site: built from the tracked catalog only, self-contained, every profile on it."""
import json
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
