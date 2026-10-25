"""
Project Zero Dawn: Root Kernel.

Builds the GAIA network, wires scenario hooks and runs the sequence.

    python Root_Kernel.py                              # nominal run, seed 2064
    python Root_Kernel.py -s CANON                     # Faro sabotage + HADES + HEPHAESTUS
    python Root_Kernel.py -s HADES_ROGUE --no-veto     # let the countdown finish
    python Root_Kernel.py -s FARO_SABOTAGE --seed 7 --json out.json --csv timeline.csv
    python Root_Kernel.py --sweep 20                   # Monte Carlo over 20 seeds
"""

from __future__ import annotations

import argparse
import logging
import os
import statistics
import sys
from typing import Callable, Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from base import LOG_FORMAT, Context, Phase, SimConfig      # noqa: E402
from AETHER.Aether import AETHER                             # noqa: E402
from ALOY.Aloy import Aloy                                   # noqa: E402
from APOLLO.Apollo import APOLLO                             # noqa: E402
from ARTEMIS.Artemis import ARTEMIS                          # noqa: E402
from DEMETER.Demeter import DEMETER                          # noqa: E402
from ELEUTHIA.Eleuthia import ELEUTHIA                       # noqa: E402
from GAIA.Gaia import GAIA                                   # noqa: E402
from HADES.Hades import HADES                                # noqa: E402
from HEPHAESTUS.Hephaestus import HEPHAESTUS                 # noqa: E402
from MINERVA.Minerva import MINERVA                          # noqa: E402
from POSEIDON.Poseidon import POSEIDON                       # noqa: E402

SUBORDINATES = (HEPHAESTUS, MINERVA, AETHER, POSEIDON, DEMETER, ARTEMIS, ELEUTHIA, APOLLO, HADES)


# --------------------------------------------------------------------------
# Scenarios: (when, phase, action)
# --------------------------------------------------------------------------
def _faro(g: GAIA) -> None:
    g.get("APOLLO").sabotage("Ted Faro")


def _hades(g: GAIA) -> None:
    g.get("HADES").corrupt("unidentified external signal")


def _hephaestus(g: GAIA) -> None:
    heph = g.get("HEPHAESTUS")
    for rate in (0.12, 0.20, 0.25):        # rising salvage pressure from the new tribes
        heph.observe_salvage(rate)


SCENARIOS: Dict[str, List[tuple]] = {
    "NOMINAL": [],
    "FARO_SABOTAGE": [("after", Phase.MANUFACTURE, _faro)],
    "HADES_ROGUE": [("after", Phase.FAUNA, _hades)],
    "HEPHAESTUS_ROGUE": [("after", Phase.HUMANITY, _hephaestus)],
}
SCENARIOS["CANON"] = SCENARIOS["FARO_SABOTAGE"] + SCENARIOS["HEPHAESTUS_ROGUE"] + [("after", Phase.KNOWLEDGE, _hades)]


def build(scenario: str = "NOMINAL", config: SimConfig | None = None) -> GAIA:
    ctx = Context.create(config)
    aloy = Aloy(ctx)
    aloy.boot()                     # installs itself as ctx.bus before anything else emits
    gaia = GAIA(ctx)
    gaia.attach_bus(aloy)
    for cls in SUBORDINATES:
        gaia.register(cls(ctx))
    for when, phase, fn in SCENARIOS[scenario]:
        gaia.on(when, phase, fn)
    return gaia


def run(scenario: str = "NOMINAL", config: SimConfig | None = None, epilogue: float = 100.0) -> Dict:
    return build(scenario, config).run(epilogue_years=epilogue)


# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------
def _bar(x: float, width: int = 24) -> str:
    n = round(max(0.0, min(1.0, x)) * width)
    return "#" * n + "." * (width - n)


def print_report(rep: Dict, scenario: str) -> None:
    print("\n" + "=" * 78)
    print(f" PROJECT ZERO DAWN | scenario {scenario} | outcome {rep['outcome']} | T+{rep['year']:.0f} yr")
    print("=" * 78)
    print(f" {'phase':<12}{'owner':<12}{'start':>7}{'end':>7}{'ext':>5}{'wait':>5}  {'health':>7}  ok")
    for p in rep["phases"]:
        print(f" {p['phase']:<12}{p['subsystem']:<12}{p['start']:>7.0f}{p['end']:>7.0f}"
              f"{p['extensions']:>5}{p['gate_waits']:>5}  {p['health']:>7.3f}  {'Y' if p['success'] else 'N'}")
    print("\n Environment")
    for k in ("faro", "atmosphere", "hydrosphere", "flora", "fauna", "biosphere", "knowledge", "fleet", "machine_threat"):
        v = rep["environment"][k]
        print(f"   {k:<15} {_bar(v)} {v:6.3f}")
    print(f"   {'humans':<15} {int(rep['environment']['humans']):,}")
    s = rep["subsystems"]
    print("\n Highlights")
    m = s["MINERVA"]
    print(f"   MINERVA   key after {m['attempts']:,} of {m['keyspace']:,} tries "
          f"(E={m['expected_attempts']:,.0f}), {m['towers']} towers, coverage {m['coverage_mc']:.3f}")
    a = s["AETHER"]
    print(f"   AETHER    O2 {a['o2_pct']} %, CO2 {a['co2_ppm']} ppm, toxin {a['toxin']}, T {a['temp_c']} C")
    e = s["ELEUTHIA"]
    print(f"   ELEUTHIA  {e['population']:,} people in {e['tribes_alive']}/{e['tribes']} tribes, "
          f"Ne={e['Ne']}, H10={e['H_retained_10gen']}, Simpson D={e['simpson_D']}")
    ap = s["APOLLO"]
    print(f"   APOLLO    archive {100 * ap['available_fraction']:.1f} % available, sabotaged={ap['sabotaged']}")
    h = s["HEPHAESTUS"]
    print(f"   HEPHAEST. autonomous={h['autonomous']}, threat {h['machine_threat']}, built {h['units_built']:,}")
    hd = s["HADES"]
    print(f"   HADES     status {hd['status']}, corrupted={hd['corrupted']}, executed={hd['executed']}, purged={hd['purged']}")
    print("=" * 78)


def sweep(scenario: str, n: int, base: SimConfig) -> None:
    """Monte Carlo over seeds: outcome probability and completion-year statistics."""
    years, humans, ok = [], [], 0
    for seed in range(n):
        cfg = SimConfig(**{**base.__dict__, "seed": seed})
        rep = run(scenario, cfg)
        ok += rep["outcome"] == "COMPLETE"
        years.append(rep["year"])
        humans.append(rep["environment"]["humans"])
    p = ok / n
    se = (p * (1 - p) / n) ** 0.5
    print(f"\n{scenario}: P(complete) = {p:.2f} +/- {se:.2f} (binomial SE, n={n})")
    print(f"  end year  mean {statistics.mean(years):.0f}, sd {statistics.pstdev(years):.0f}")
    print(f"  humans    median {statistics.median(humans):,.0f}, min {min(humans):,.0f}, max {max(humans):,.0f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Project Zero Dawn terraforming simulator")
    ap.add_argument("-s", "--scenario", default="NOMINAL", choices=sorted(SCENARIOS))
    ap.add_argument("--seed", type=int, default=SimConfig.seed)
    ap.add_argument("--no-veto", action="store_true", help="GAIA cannot purge a corrupted HADES")
    ap.add_argument("--key-digits", type=int, default=SimConfig.minerva_key_hex_digits)
    ap.add_argument("--epilogue", type=float, default=100.0, help="years simulated after the last phase")
    ap.add_argument("--json", help="write the final report as JSON")
    ap.add_argument("--csv", help="write the environment timeline as CSV")
    ap.add_argument("--sweep", type=int, default=0, help="Monte Carlo over N seeds")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format=LOG_FORMAT, datefmt="%H:%M:%S")
    cfg = SimConfig(seed=args.seed, gaia_veto_enabled=not args.no_veto,
                    minerva_key_hex_digits=args.key_digits)
    if args.sweep:
        logging.getLogger().setLevel(logging.ERROR)
        sweep(args.scenario, args.sweep, cfg)
        return 0

    gaia = build(args.scenario, cfg)
    rep = gaia.run(epilogue_years=args.epilogue)
    print_report(rep, args.scenario)
    if args.json:
        gaia.export_json(args.json)
    if args.csv:
        gaia.export_csv(args.csv)
    return 0 if rep["outcome"] == "COMPLETE" else 2


if __name__ == "__main__":
    sys.exit(main())
