"""Offline detector test: replays fake chat, no network, no video.

    python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from clipper.rt.detector import DetectorParams, SpikeDetector  # noqa: E402
from clipper.rt.replay import replay  # noqa: E402
from fake_chat import EXPECTED_FIRE_WINDOWS, generate  # noqa: E402


class TestSpikeDetector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = replay(generate())
        for e in cls.events:
            print(f"  fired: {e.channel} t={e.t:.0f} z={e.z} rate={e.rate} base={e.baseline} "
                  f"chatters={e.unique_chatters} top={e.top_tokens[:3]}")

    def test_fires_on_each_real_burst_exactly_once(self):
        fires = [e.t for e in self.events if e.channel == "campaignguy"]
        for lo, hi in EXPECTED_FIRE_WINDOWS:
            hits = [t for t in fires if lo <= t <= hi]
            self.assertEqual(len(hits), 1, f"expected one fire in {lo}-{hi}s, got {hits} (all: {fires})")

    def test_no_false_positives(self):
        fires = [e.t for e in self.events if e.channel == "campaignguy"]
        stray = [t for t in fires if not any(lo <= t <= hi for lo, hi in EXPECTED_FIRE_WINDOWS)]
        self.assertEqual(stray, [], "fired outside a real burst (cooldown or spammer cap broken?)")

    def test_slow_organic_growth_does_not_fire(self):
        self.assertEqual([e for e in self.events if e.channel == "slowclimb"], [])

    def test_many_random_chats(self):
        """Not tuned to one lucky seed: 30 different random chats, every burst caught, no stray fires."""
        misses, strays = 0, 0
        for seed in range(100, 130):
            ev = replay(generate(seed))
            good = [e for e in ev if e.channel == "campaignguy"
                    and any(lo <= e.t <= hi for lo, hi in EXPECTED_FIRE_WINDOWS)]
            misses += len(EXPECTED_FIRE_WINDOWS) - len(good)
            strays += len(ev) - len(good)
        self.assertEqual((misses, strays), (0, 0))

    def test_keyword_weighting(self):
        d = SpikeDetector("x")
        self.assertEqual(d.weigh("nice drive"), 1.0)
        self.assertGreater(d.weigh("KEKW"), 1.0)
        self.assertGreater(d.weigh("CLIP IT ?????"), d.weigh("KEKW"))
        self.assertLessEqual(d.weigh("KEKW LUL CLIP IT OMEGALUL ????? !!!"), d.p.max_msg_weight)

    def test_warmup_blocks_early_fire(self):
        d = SpikeDetector("x", DetectorParams(warmup_s=60))
        for s in range(30):
            for i in range(30):
                d.feed(s + i / 30, f"u{i}", "KEKW CLIP IT")
            self.assertIsNone(d.tick(s + 1))

    def test_event_payload(self):
        e = next(e for e in self.events if e.channel == "campaignguy")
        self.assertGreaterEqual(e.unique_chatters, 5)
        self.assertGreater(e.rate, e.baseline * 2)
        self.assertTrue(any(tok in ("KEKW", "CLIP", "LMAO", "IT") for tok, _ in e.top_tokens))
        self.assertTrue(0 < e.strength() <= 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
