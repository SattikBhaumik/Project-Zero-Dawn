"""
POSEIDON: hydrosphere detoxification and ocean circulation.

Per water body i (ocean, river, lake, aquifer):
  * toxin, dissolved O2 and salinity relax exponentially towards targets.
  * pH is NOT relaxed directly. pH is logarithmic, so the quantity that
    actually decays is the hydrogen-ion activity:
        [H+] = 10^(-pH),  [H+](t) = [H+]* + ([H+]0 - [H+]*) e^(-t/tau_pH),  pH = -log10[H+]
    Far from target this gives pH(t) ~ pH0 + t / (tau_pH ln 10): a LINEAR
    climb of 1/(2.303 tau_pH) pH units per year. Lifting a body from pH 3 to
    6.5 therefore costs ~8 e-folds, which is why processors dose alkalinity
    (tau_pH = tau / 3) rather than waiting for natural buffering.
  * Dissolved oxygen target is coupled to remaining toxin load:
        DO* = DO_sat * (1 - 0.8 * toxin)

Processor allocation (n_total platforms) uses weights proportional to
sqrt(V_i), with a 4x priority multiplier for freshwater bodies that DEMETER
and ELEUTHIA depend on, apportioned with the largest-remainder method.
Acceleration per body:
    tau_i = tau_nat,i / (1 + n_i / (k * sqrt(V_i)))

Circulation: each current's transport (Sverdrups, 1 Sv = 1e6 m^3/s)
relaxes from a stalled 10 % of nominal back to nominal.

Health = log-volume-weighted mean of body health (so a river is not
drowned out by the Pacific), times 0.85, plus 0.15 * circulation recovery.
Habitable body: toxin <= 0.05, 6.5 <= pH <= 8.5, DO >= 5 mg/L.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List

from base import Context, PhaseResult, Subsystem, SystemStatus, clamp, largest_remainder, progress, relax

K_ACCEL = 0.5
FRESH_PRIORITY = 4.0


@dataclass
class WaterBody:
    body_id: str
    name: str
    kind: str              # OCEAN | RIVER | LAKE | AQUIFER
    volume_km3: float
    tau_nat: float         # natural flushing / residence timescale, years
    toxin: float
    ph: float
    do_mg_l: float
    salinity_ppt: float
    processors: int = 0
    ph0: float = 0.0

    def __post_init__(self):
        self.ph0 = self.ph

    @property
    def fresh(self) -> bool:
        return self.kind != "OCEAN"

    @property
    def ph_target(self) -> float:
        return 8.1 if self.kind == "OCEAN" else 7.4

    @property
    def sal_target(self) -> float:
        return 35.0 if self.kind == "OCEAN" else (0.5 if self.kind == "AQUIFER" else 0.2)

    @property
    def do_sat(self) -> float:
        return 7.0 if self.kind == "OCEAN" else 8.5

    @property
    def habitable(self) -> bool:
        return self.toxin <= 0.05 and 6.5 <= self.ph <= 8.5 and self.do_mg_l >= 5.0

    def health(self) -> float:
        return (progress(1.0, self.toxin, 0.0)
                + progress(self.ph0, self.ph, self.ph_target)
                + clamp(self.do_mg_l / 5.0)) / 3.0


@dataclass
class Current:
    name: str
    nominal_sv: float
    sv: float = 0.0
    tau: float = 25.0

    def __post_init__(self):
        self.sv = 0.1 * self.nominal_sv


class POSEIDON(Subsystem):
    NAME = "POSEIDON"
    ROLE = "Phase 3: detoxify oceans, rivers, lakes and aquifers; restart circulation"

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        rows = [
            ("WB-01", "Pacific Ocean", "OCEAN", 710_000, 250, 1.00, 3.2, 0.5, 12.0),
            ("WB-02", "Atlantic Ocean", "OCEAN", 310_000, 220, 1.00, 3.4, 0.5, 11.0),
            ("WB-03", "Indian Ocean", "OCEAN", 264_000, 220, 1.00, 3.3, 0.4, 11.5),
            ("WB-04", "Arctic Ocean", "OCEAN", 18_750, 120, 0.90, 3.8, 1.2, 8.0),
            ("WB-05", "Southern Ocean", "OCEAN", 71_800, 150, 0.90, 3.7, 1.0, 9.0),
            ("WB-06", "Amazon River Basin", "RIVER", 7.0, 8, 1.00, 2.9, 0.2, 0.0),
            ("WB-07", "Congo River Basin", "RIVER", 4.0, 8, 1.00, 3.1, 0.2, 0.0),
            ("WB-08", "Lake Baikal", "LAKE", 23.6, 120, 0.80, 4.2, 1.5, 0.0),
            ("WB-09", "Caspian Sea", "LAKE", 78.2, 150, 0.90, 3.5, 0.8, 5.0),
            ("WB-10", "Global Aquifer Network", "AQUIFER", 10_600, 600, 1.00, 2.8, 0.1, 0.0),
        ]
        self.bodies: List[WaterBody] = [WaterBody(*r) for r in rows]
        self.currents = [Current("Thermohaline conveyor (AMOC)", 17), Current("Gulf Stream", 30),
                         Current("Kuroshio", 40), Current("Antarctic Circumpolar", 130),
                         Current("Equatorial Counter", 15)]
        self.n_total = 0
        self._weights = [math.log10(b.volume_km3 + 1) + 2 for b in self.bodies]

    def allocate(self, n_total: int) -> None:
        self.n_total = n_total
        w = [math.sqrt(b.volume_km3) * (FRESH_PRIORITY if b.fresh else 1.0) for b in self.bodies]
        for b, n in zip(self.bodies, largest_remainder(n_total, w)):
            b.processors = n

    def _evolve_body(self, b: WaterBody, dt: float) -> None:
        accel = 1.0 + b.processors / (K_ACCEL * math.sqrt(b.volume_km3))
        tau = b.tau_nat / accel * math.exp(self.rng.gauss(0.0, 0.05))
        b.toxin = max(0.0, relax(b.toxin, 0.0, dt, tau))
        h = relax(10 ** -b.ph, 10 ** -b.ph_target, dt, tau / 3)
        b.ph = -math.log10(h)
        b.do_mg_l = relax(b.do_mg_l, b.do_sat * (1 - 0.8 * b.toxin), dt, tau / 2)
        b.salinity_ppt = relax(b.salinity_ppt, b.sal_target, dt, b.tau_nat)

    def _setup(self, env) -> None:
        self.emit("CIRCULATION_RESTART", currents=len(self.currents))

    def step(self, dt: float, env) -> None:
        self.allocate(int(2500 * env.get("fleet", 1.0)))
        for b in self.bodies:
            self._evolve_body(b, dt)
        for c in self.currents:
            c.sv = relax(c.sv, c.nominal_sv, dt, c.tau)
        hab = sum(b.habitable for b in self.bodies)
        self.emit("HYDROSPHERE_UPDATE", habitable=hab, total=len(self.bodies))

    @property
    def circulation(self) -> float:
        return sum(c.sv for c in self.currents) / sum(c.nominal_sv for c in self.currents)

    def health(self) -> float:
        body = sum(w * b.health() for w, b in zip(self._weights, self.bodies)) / sum(self._weights)
        return 0.85 * body + 0.15 * self.circulation

    def _succeeded(self, h: float) -> bool:
        fresh_ok = all(b.habitable for b in self.bodies if b.kind in ("RIVER", "LAKE"))
        ok = h >= 0.80 and fresh_ok
        self.set_status(SystemStatus.ACTIVE if ok else SystemStatus.DEGRADED,
                        "hydrosphere habitable" if ok else "hydrosphere recovering")
        return ok

    def _report(self):
        return {"habitable": [b.name for b in self.bodies if b.habitable],
                "habitable_count": sum(b.habitable for b in self.bodies),
                "processors": self.n_total, "circulation": round(self.circulation, 3),
                "worst": min(self.bodies, key=lambda b: b.health()).name}
