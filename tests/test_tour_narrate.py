"""The narration filter: what the Italian voice is given never carries a full stop, a raw number,
a dotted version, a file name or a word with digits inside (XTTS read "24.04", "setup.sh" and
"microk8s" as invented words, 2026-10-05), and every cue ends with a soft mark."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "tour"))
import narrate  # noqa: E402


class SpokenTests(unittest.TestCase):
    def test_numbers_in_italian_words(self):
        self.assertEqual(narrate.number_it("0"), "zero")
        self.assertEqual(narrate.number_it("8"), "otto")
        self.assertEqual(narrate.number_it("21"), "ventuno")
        self.assertEqual(narrate.number_it("28"), "ventotto")
        self.assertEqual(narrate.number_it("45"), "quarantacinque")
        self.assertEqual(narrate.number_it("100"), "cento")
        self.assertEqual(narrate.number_it("180"), "centottanta")
        self.assertEqual(narrate.number_it("256"), "duecentocinquantasei")
        self.assertEqual(narrate.number_it("2222"), "duemiladuecentoventidue")
        self.assertEqual(narrate.number_it("04"), "zero quattro")
        self.assertEqual(narrate.number_it("30080"), "tre zero zero otto zero")

    def test_versions_file_names_and_full_stops(self):
        self.assertEqual(narrate.spoken("Ubuntu 24.04 in 3 minuti.", "it"),
                         "Ubuntu ventiquattro punto zero quattro in tre minuti,")
        self.assertEqual(narrate.spoken("WSL 3.0.1 e setup.sh, poi local.json.", "it"),
                         "WSL tre punto zero punto uno e setup punto esse acca, poi local punto jason,")
        self.assertEqual(narrate.spoken("Si clona il repository… e si lancia vmctl.", "it"),
                         "Si clona il repository, e si lancia vm control,")
        self.assertEqual(narrate.spoken('Dice "pronto." (davvero.)', "it"), 'Dice "pronto," (davvero,),')
        self.assertEqual(narrate.spoken("Apri la console. Poi Open console.", "it"), "Apri la consolle, Poi Open console,")
        self.assertEqual(narrate.spoken("Ed ecco il lab: tutto il catalogo!", "it"), "Ed ecco il lab: tutto il catalogo!")
        self.assertEqual(narrate.spoken("Prossimo passo:", "it"), "Prossimo passo:")
        for cue in ("Prima VM in 24.04.", "Tre nodi, 8 vCPU, porta 2222."):
            text = narrate.spoken(cue, "it")
            self.assertNotRegex(text, r"\d")
            self.assertNotIn(".", text)

    def test_words_with_digits_inside(self):
        self.assertEqual(narrate.mixed_token_it("MicroK8s"), "Micro kappa otto esse")
        self.assertEqual(narrate.mixed_token_it("k8s"), "kappa otto esse")
        self.assertEqual(narrate.mixed_token_it("ext4"), "ext quattro")
        self.assertEqual(narrate.mixed_token_it("x86_64"), "ics ottantasei sessantaquattro")
        self.assertEqual(narrate.mixed_token_it("ttyS0"), "tty esse zero")
        self.assertEqual(narrate.mixed_token_it("md0"), "emme di zero")
        self.assertEqual(narrate.mixed_token_it("WSL2"), "WSL due")
        self.assertEqual(narrate.spoken("MicroK8s su k8s-lab-main, disco vda1 in ext4.", "it"),
                         "micro kappa otto esse su kappa otto esse-lab-main, disco vda uno in ext quattro,")
        self.assertEqual(narrate.spoken("lo snap di microk8s", "it"), "lo snap di micro kappa otto esse,")

    def test_english_keeps_its_own_table(self):
        self.assertEqual(narrate.spoken("Ubuntu 24.04 with vmctl and 2 VMs.", "en"), "Ubuntu 24.04 with vm control and 2 V Ms,")


if __name__ == "__main__":
    unittest.main()
