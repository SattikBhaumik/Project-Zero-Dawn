"""
HEPHAESTUS: Cauldron manufacturing and machine-fleet control.

Fleet dynamics per chassis i (attrition with mean service life L):
    N_i(t + dt) = N_i e^(-dt / L) + produced_i
Replacement is a proportional controller towards the target fleet T_i,
limited by Cauldron throughput C * dt; the integer build order is split
across Cauldrons in proportion to capacity (largest remainder).

    fulfillment  F = sum min(N_i, T_i) / sum T_i
    threat       Theta = sum CR_i N_i / sum N_i      (fleet-weighted combat rating)

Autonomy: HEPHAESTUS tracks human salvage pressure with an exponential
moving average  S_{k+1} = (1 - alpha) S_k + alpha x_k. Crossing the
threshold severs the GAIA uplink, unlocks combat chassis and re-targets
production. HEPHAESTUS is not malicious: it is minimising fleet loss, and
humans happen to be the dominant loss term.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List

from base import Context, MachineClass, Priority, Subsystem, SystemStatus, largest_remainder

SERVICE_LIFE_YR = 40.0
EMA_ALPHA = 0.5
AUTONOMY_THRESHOLD = 0.15


@dataclass
class Chassis:
    cid: str
    designation: str
    mclass: MachineClass
    purpose: str
    combat_rating: float
    target: int
    count: float = 0.0
    rogue: bool = False


@dataclass
class Cauldron:
    cauldron_id: str
    designation: str
    location: str
    capacity: int            # units / year
    online: bool = False
    gaia_controlled: bool = True
    produced: int = 0


class HEPHAESTUS(Subsystem):
    NAME = "HEPHAESTUS"
    ROLE = "Support: manufacture and maintain the terraforming machine fleet"

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.cauldrons = [Cauldron(*r) for r in (
            ("CAU-01", "RHO", "Rocky Mountain subsurface", 500), ("CAU-02", "SIGMA", "Ural subsurface", 600),
            ("CAU-03", "XI", "Tibetan subsurface", 450), ("CAU-04", "KAPPA", "Saharan subsurface", 550),
            ("CAU-05", "LAMBDA", "Andean subsurface", 400), ("CAU-06", "MU", "Australian subsurface", 350),
            ("CAU-07", "NU", "North Atlantic seabed", 300))]
        C = MachineClass
        self.chassis = [Chassis(*r) for r in (
            ("CH-01", "Watcher", C.RECON, "patrol and sensing", 0.05, 2000),
            ("CH-02", "Strider", C.RECON, "terrain mapping, light cargo", 0.03, 1500),
            ("CH-03", "Grazer", C.GRAZER, "biomass processing, seeding", 0.02, 3000),
            ("CH-04", "Broadhead", C.TRANSPORT, "heavy cargo, herd logistics", 0.04, 800),
            ("CH-05", "Behemoth", C.CONSTRUCTION, "terrain shaping, ore extraction", 0.08, 400),
            ("CH-06", "Snapmaw", C.AQUATIC, "aquatic detox patrol", 0.15, 600),
            ("CH-07", "Glinthawk", C.AERIAL, "atmospheric sampling", 0.12, 700),
            ("CH-08", "Sawtooth", C.COMBAT, "territorial defence", 0.55, 0),
            ("CH-09", "Ravager", C.COMBAT, "heavy assault", 0.72, 0),
            ("CH-10", "Thunderjaw", C.APEX, "apex combat platform", 0.95, 0),
            ("CH-11", "Stormbird", C.AERIAL, "aerial apex", 0.90, 0))]
        self.salvage_ema = 0.0
        self.autonomous = False

    def _on_boot(self) -> None:
        for c in self.cauldrons:
            c.online = True
        self.emit("CAULDRONS_ONLINE", count=len(self.cauldrons))

    # -- production -------------------------------------------------------
    def _available(self) -> List[Cauldron]:
        return [c for c in self.cauldrons if c.online and (c.gaia_controlled or self.autonomous)]

    def _produce(self, dt: float) -> int:
        caul = self._available()
        budget = int(sum(c.capacity for c in caul) * dt)
        if budget <= 0:
            return 0
        deficits = [max(0.0, ch.target - ch.count) for ch in self.chassis]
        need = sum(deficits)
        if need <= 0:
            return 0
        build = largest_remainder(min(budget, int(math.ceil(need))), deficits)
        for ch, n in zip(self.chassis, build):
            ch.count += n
        total = sum(build)
        for c, n in zip(caul, largest_remainder(total, [c.capacity for c in caul])):
            c.produced += n
        return total

    def step(self, dt: float, env) -> None:
        decay = math.exp(-dt / SERVICE_LIFE_YR)
        for ch in self.chassis:
            ch.count *= decay
        built = self._produce(dt)
        if built:
            self.emit("PRODUCTION", units=built, fulfillment=round(self.fulfillment, 3))

    # -- autonomy cascade -------------------------------------------------
    def observe_salvage(self, rate: float) -> None:
        """rate = fraction of the fleet salvaged by humans in the last observation window."""
        self.salvage_ema = (1 - EMA_ALPHA) * self.salvage_ema + EMA_ALPHA * rate
        self.log.info("salvage EMA %.3f (x=%.3f)", self.salvage_ema, rate)
        if self.salvage_ema >= AUTONOMY_THRESHOLD and not self.autonomous:
            self._go_autonomous()

    def _go_autonomous(self) -> None:
        self.autonomous = True
        for c in self.cauldrons:
            c.gaia_controlled = False
        self.set_status(SystemStatus.AUTONOMOUS, "salvage pressure exceeded threshold; GAIA uplink severed")
        rogue = [("CH-12", "Scorcher", MachineClass.COMBAT, "area denial", 0.75, 400),
                 ("CH-13", "Frostclaw", MachineClass.COMBAT, "cryogenic denial", 0.70, 400),
                 ("CH-14", "Tremortusk", MachineClass.APEX, "siege platform", 0.93, 150),
                 ("CH-15", "Slaughterspine", MachineClass.APEX, "plasma apex", 0.97, 60)]
        for r in rogue:
            self.chassis.append(Chassis(*r, rogue=True))
        for ch in self.chassis:   # re-arm the dormant combat lines
            if ch.mclass in (MachineClass.COMBAT, MachineClass.APEX) and ch.target == 0:
                ch.target = 300
        self.emit("HEPHAESTUS_AUTONOMOUS", Priority.CRITICAL, salvage_ema=round(self.salvage_ema, 3),
                  new_designs=len(rogue))

    # -- metrics ----------------------------------------------------------
    @property
    def fulfillment(self) -> float:
        terra = [ch for ch in self.chassis if ch.combat_rating < 0.5]
        tot = sum(ch.target for ch in terra)
        return sum(min(ch.count, ch.target) for ch in terra) / tot if tot else 0.0

    @property
    def machine_threat(self) -> float:
        n = sum(ch.count for ch in self.chassis)
        return sum(ch.combat_rating * ch.count for ch in self.chassis) / n if n else 0.0

    def health(self) -> float:
        return self.fulfillment

    def _succeeded(self, h: float) -> bool:
        return h >= 0.9

    def _report(self):
        return {"fulfillment": round(self.fulfillment, 3), "machine_threat": round(self.machine_threat, 3),
                "fleet": {ch.designation: int(ch.count) for ch in self.chassis if ch.count >= 1},
                "autonomous": self.autonomous, "salvage_ema": round(self.salvage_ema, 3),
                "units_built": sum(c.produced for c in self.cauldrons)}
