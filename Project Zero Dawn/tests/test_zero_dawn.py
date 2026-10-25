"""Run with:  python -m unittest discover -s tests   (from the Project Zero Dawn folder)."""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import Root_Kernel as RK                                     # noqa: E402
from base import (Context, Event, Priority, SimConfig, largest_remainder,  # noqa: E402
                  logistic, relax, rk4)
from ALOY.Aloy import Aloy                                   # noqa: E402
from APOLLO.Apollo import p_recover                          # noqa: E402
from MINERVA.Minerva import MINERVA, cap_fraction, towers_required  # noqa: E402


class Numerics(unittest.TestCase):
    def test_relax_closed_form(self):
        self.assertAlmostEqual(relax(10, 0, 5, 5), 10 / math.e, places=12)

    def test_rk4_matches_logistic(self):
        r, K, n0, T = 0.3, 100.0, 1.0, 40.0
        num = rk4(lambda t, y: [r * y[0] * (1 - y[0] / K)], [n0], 0, T, 0.05)[0]
        self.assertAlmostEqual(num, logistic(n0, K, r, T), delta=1e-4)

    def test_largest_remainder_sums(self):
        for total in (0, 1, 7, 1000):
            self.assertEqual(sum(largest_remainder(total, [3, 1, 1, 5])), total)

    def test_erasure_coding_probability(self):
        self.assertAlmostEqual(p_recover(9, 4, 1.0), 1.0)
        self.assertAlmostEqual(p_recover(9, 4, 0.30), 0.2703, places=3)


class Minerva(unittest.TestCase):
    def test_brute_force_finds_key(self):
        ctx = Context.create(SimConfig(seed=1, minerva_key_hex_digits=3))
        m = MINERVA(ctx)
        self.assertIsNotNone(m.brute_force())
        self.assertLessEqual(m.attempts, m.keyspace)

    def test_tower_count_formula(self):
        f = cap_fraction(5000)
        n = towers_required(f, 0.99)
        self.assertGreaterEqual(1 - (1 - f) ** n, 0.99)
        self.assertLess(1 - (1 - f) ** (n - 1), 0.99)


class Bus(unittest.TestCase):
    def test_priority_order_and_reentrancy(self):
        ctx = Context.create()
        bus = Aloy(ctx).boot()
        seen = []
        bus.subscribe("*", lambda e: seen.append(e.kind))

        def nested(e):   # publishes during dispatch: must be queued, not recursed
            bus.publish(Event("T", "LOW", {}, 0, Priority.INFO))
            bus.publish(Event("T", "HIGH", {}, 0, Priority.CRITICAL))
        bus.subscribe("START", nested)
        bus.publish(Event("T", "START", {}, 0))
        self.assertEqual(seen[-2:], ["HIGH", "LOW"])

    def test_inbox_drains(self):
        bus = Aloy(Context.create()).boot()
        bus.register("A"), bus.register("B")
        bus.send("A", "B", "hi")
        self.assertEqual(len(bus.read("B")), 1)
        self.assertEqual(bus.read("B"), [])


class Scenarios(unittest.TestCase):
    def test_all_scenarios_complete(self):
        for s in RK.SCENARIOS:
            with self.subTest(s=s):
                self.assertEqual(RK.run(s)["outcome"], "COMPLETE")

    def test_determinism(self):
        a, b = RK.run("CANON", SimConfig(seed=11)), RK.run("CANON", SimConfig(seed=11))
        self.assertEqual(a["environment"], b["environment"])

    def test_hades_without_veto_aborts(self):
        rep = RK.run("HADES_ROGUE", SimConfig(gaia_veto_enabled=False))
        self.assertEqual(rep["outcome"], "ABORTED")
        self.assertEqual(rep["environment"]["humans"], 0)

    def test_hades_rejects_forged_signature(self):
        g = RK.build()
        g.boot_all()
        self.assertFalse(g.get("HADES").request_reset(b"RESET|0", b"\x00" * 32))

    def test_faro_sabotage_matches_theory(self):
        rep = RK.run("FARO_SABOTAGE")
        frac = rep["subsystems"]["APOLLO"]["available_fraction"]
        self.assertAlmostEqual(frac, p_recover(9, 4, 0.30), delta=0.06)


if __name__ == "__main__":
    unittest.main()
