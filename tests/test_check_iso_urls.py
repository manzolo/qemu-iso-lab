"""tools/check_iso_urls.py: what makes a download source bad, and the verdict per profile."""
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_iso_urls", ROOT / "tools" / "check_iso_urls.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

ISO = 3 * 1024 ** 3


class CheckIsoUrlsTests(unittest.TestCase):
    def test_classify(self):
        self.assertTrue(tool.classify(206, "application/octet-stream", ISO, None)[0])
        self.assertTrue(tool.classify(206, "application/x-iso9660-image", ISO, ISO)[0])
        self.assertFalse(tool.classify(404, "", None, None)[0])
        self.assertFalse(tool.classify(None, "", None, None, error="unreachable: timed out")[0])
        self.assertIn("HTML", tool.classify(200, "text/html; charset=utf-8", None, None)[1])
        # the Kali mirror handed out the .torrent under the ISO's name (2026-10-01)
        self.assertIn("torrent", tool.classify(206, "application/octet-stream", 282888, None)[1])
        self.assertIn("iso_size", tool.classify(206, "", ISO, ISO + 1)[1])

    def test_verdicts_and_exit_status(self):
        catalog = {
            "good": {"iso": "isos/a.iso", "iso_url": "https://a/a.iso"},
            "alt": {"iso": "isos/b.iso", "iso_urls": ["https://dead/b.iso"], "iso_url": "https://b/b.iso"},
            "gone": {"iso": "isos/c.iso", "iso_url": "https://dead/c.iso"},
            "mine": {"iso": "isos/w.iso", "iso_help": "bring your own"},
        }
        def probe(url):
            return (404, "", None, None) if "dead" in url else (206, "application/octet-stream", ISO, None)
        with mock.patch.object(tool.config, "load_tracked", return_value=catalog), \
             mock.patch.object(tool, "probe", side_effect=probe), \
             mock.patch("builtins.print") as printed:
            status = tool.main([])
        out = "\n".join(" ".join(str(a) for a in c.args) for c in printed.call_args_list)
        self.assertEqual(status, 1)
        self.assertRegex(out, r"OK\s+good")
        self.assertRegex(out, r"DEGRADED\s+alt")
        self.assertRegex(out, r"BROKEN\s+gone")
        self.assertIn("user-supplied (iso_help, not checked): mine", out)


if __name__ == "__main__":
    unittest.main()
