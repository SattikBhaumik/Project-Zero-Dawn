"""
PROJECT ZERO DAWN: Root Kernel
Entry point. 
Instantiates the entire GAIA system,
then executes the terraforming sequence.

Usage:
python Root_Kernel.py                       # case1: usual simulation run
python Root_Kernel.py FARO_SABOTAGE         # case0: Ted Faro wipes APOLLO
python Root_Kernel.py HADES_ROGUE           # case2: HADES corruption event
python Root_Kernel.py HEPHAESTUS_ROGUE      # case3: HEPHAESTUS goes autonomous
"""

import sys
import os

# all modules are sub-parts of this file
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from GAIA.Gaia               import GAIA
from ALOY.Aloy               import Aloy
from AETHER.Aether           import AETHER
from POSEIDON.Poseidon       import POSEIDON
from DEMETER.Demeter         import DEMETER
from ARTEMIS.Artemis         import ARTEMIS
from ELEUTHIA.Eleuthia       import ELEUTHIA
from APOLLO.Apollo           import APOLLO
from HEPHAESTUS.Hephaestus  import HEPHAESTUS
from HADES.Hades             import HADES
from MINERVA.Minerva         import MINERVA


BANNER = r"""
PROJECT ZERO DAWN
"When all seems lost, we plant the seeds of tomorrow."
Dr. Elisabet Sobeck
"""

# kernel bootstrap
def bootstrap(scenario: str = "NOMINAL") -> GAIA:
    """
    initialize the GAIA system
    --> return the GAIA instance.

    instantiation order:
    -> GAIA Prime: core orchestrator
    --> ALOY: communication bridge (must be ready before others register)
    ---> All other subsystems in logical dependency order
    """

    print(BANNER)
    print(f"ROOT KERNEL BOOT SEQUENCE.....Scenario: {scenario}")

    # core AI
    gaia = GAIA()
    gaia.boot()

    # communication bridge
    aloy = Aloy()
    aloy.boot()
    gaia.register_aloy(aloy)

    # support subsystems (needed early)
    hephaestus = HEPHAESTUS()
    hades      = HADES()
    minerva    = MINERVA()

    # terraforming subsystems
    aether   = AETHER()
    poseidon = POSEIDON()
    demeter  = DEMETER()
    artemis  = ARTEMIS()
    eleuthia = ELEUTHIA()
    apollo   = APOLLO()

    # register all with GAIA 
    for sub in [hephaestus, hades, minerva, aether, poseidon,
                demeter, artemis, eleuthia, apollo]:
        gaia.register(sub)

    # connect ALOY to key events for logging
    aloy.subscribe("PHASE_COMPLETE",     lambda m: print(f"\n  ✓  {m.body}\n"))
    aloy.subscribe("HADES_CORRUPTED",    lambda m: print(f"\n  ⚠  HADES CORRUPTED — {m.body}\n"))
    aloy.subscribe("ARCHIVE_DELETED",    lambda m: print(f"\n  ⚠  APOLLO SABOTAGED — {m.body}\n"))
    aloy.subscribe("HUMANS_RELEASED",    lambda m: print(f"\n  ✓  HUMANS RELEASED — {m.body}\n"))
    aloy.subscribe("EXTINCTION_ABORTED", lambda m: print(f"\n  ✓  EXTINCTION ABORTED — {m.body}\n"))

    print("\n  All subsystems registered. GAIA ready.\n")
    return gaia

# utility: print a subsystem summary table
def print_summary(gaia: GAIA):
    report = gaia.full_report()
    print("\n" + "═" * 70)
    print("  GAIA — SYSTEM SUMMARY")
    print("═" * 70)
    print(f"  Status:         {report['gaia_status']}")
    print(f"  Sim. Year:      {report['simulation_year']}")
    print(f"  Phases Done:    {report['phases_complete']}")
    print(f"  Events:         {report['events_received']}")
    print()
    print("  Subsystem Statuses:")
    for name, status in report["subsystems"].items():
        bar = "●" if status == "ACTIVE" else ("○" if status == "DORMANT" else "◌")
        print(f"    {bar}  {name:<14}  {status}")
    print()
    print("  Biosphere:")
    b = report["biosphere"]
    print(f"    Atmospheric:    {b['atmospheric_pct']*100:.1f}%")
    print(f"    Oceanic:        {b['oceanic_pct']*100:.1f}%")
    print(f"    Floral:         {b['floral_pct']*100:.1f}%")
    print(f"    Faunal:         {b['faunal_pct']*100:.1f}%")
    print(f"    Human Pop:      {b['human_pop']:,}")
    print("═" * 70)


# entry point
def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else "NOMINAL"
    valid_scenarios = {"NOMINAL", "FARO_SABOTAGE", "HADES_ROGUE", "HEPHAESTUS_ROGUE"}

    if scenario not in valid_scenarios:
        print(f"Unknown scenario '{scenario}'. Valid options: {valid_scenarios}")
        sys.exit(1)

    gaia = bootstrap(scenario)

    print("\n" + "=" * 70)
    print(f"  INITIATING TERRAFORMING SEQUENCE — {scenario}")
    print("=" * 70 + "\n")

    gaia.execute_terraforming_sequence(run_scenario=scenario)
    print_summary(gaia)

    return gaia


if __name__ == "__main__":
    main()
