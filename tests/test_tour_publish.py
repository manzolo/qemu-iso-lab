"""tools/tour/publish.py: the order of tour.json (the tour, the labs along a learning path, the courses)."""
import importlib.util
import io
import unittest
from contextlib import redirect_stdout

from tests._common import ROOT


def load_publish():
    spec = importlib.util.spec_from_file_location("tour_publish", ROOT / "tools" / "tour" / "publish.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # main() runs only as a script
    return module


class TourOrderTests(unittest.TestCase):
    def test_tour_then_labs_along_the_path_then_courses(self):
        publish = load_publish()
        clips = {
            "lab-zfs": {"series": "labs", "lab": "zfs-lab"},
            "course-zfs-2": {"series": "courses", "lab": "zfs-lab", "order": 2},
            "07-map": {"series": "tour"},
            "lab-git": {"series": "labs", "lab": "git-lab", "order": 1},
            "lab-new": {"series": "labs", "lab": "new-lab"},
            "lab-docker": {"series": "labs", "lab": "docker-lab"},
            "course-zfs-1": {"series": "courses", "lab": "zfs-lab", "order": 1},
            "lab-git-basics": {"series": "labs", "lab": "git-lab", "order": 0},
            "05-lab": {"series": "tour", "lab": "vpn-lab"},
            "lab-proxmox": {"series": "labs", "lab": "proxmox-lab"},
        }
        out = io.StringIO()
        with redirect_stdout(out):
            order = publish.ordered(clips)
        self.assertEqual(order, ["05-lab", "07-map", "lab-git-basics", "lab-git", "lab-zfs", "lab-docker",
                                 "lab-proxmox", "lab-new", "course-zfs-1", "course-zfs-2"])
        self.assertEqual([clips[k]["track"] for k in order],
                         [None, None, "basics", "basics", "storage", "services", "virtualization", "other",
                          "zfs-course", "zfs-course"])
        self.assertIn("new-lab is in no track", out.getvalue())  # a new lab is not lost silently

    def test_every_lab_with_content_has_a_track(self):
        publish = load_publish()
        placed = {lab for _, labs in publish.LAB_TRACKS for lab in labs}
        labs = {d.name for d in (ROOT / "vms" / "labs").iterdir() if (d / "lab.json").is_file()}
        self.assertEqual(labs - placed, set())


if __name__ == "__main__":
    unittest.main()
