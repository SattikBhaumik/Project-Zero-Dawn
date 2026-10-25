"""
AETHER: atmospheric restoration and weather control.

Each atmospheric variable relaxes towards its pre-Plague value:
    dX/dt = -(X - X*) / tau_eff
    X(t)  = X* + (X0 - X*) exp(-t / tau_eff)                  (exact, O(1))

Processor fleets shorten the timescale with Michaelis-Menten saturation:
    tau_eff = tau0 / (1 + n / n_half)
Wet deposition (rain-out) additionally scrubs toxins:
    tau_tox = tau_eff / (1 + beta * f_precip),  f_precip = active rain cells / all cells

Stochasticity: the remaining deviation is multiplied by a lognormal factor
exp(N(0, sigma^2)) per step, so noise shrinks as the system converges.

Health = geometric mean of per-variable normalised progress, so a single
lethal variable (say O2 still at 12 %) cannot be averaged away.

Breathability (human-safe, conservative):
    O2 >= 19.5 %   CO2 <= 5000 ppm   toxin <= 0.02   5 <= T <= 25 C
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Dict, List

from base import Context, PhaseResult, Priority, Subsystem, SystemStatus, geometric_mean, progress, relax

VARS = ("o2_pct", "co2_ppm", "ch4_ppb", "toxin", "temp_c", "pressure_hpa")


@dataclass(frozen=True)
class AtmosphereState:
    year: float
    o2_pct: float
    co2_ppm: float
    ch4_ppb: float
    toxin: float
    temp_c: float
    pressure_hpa: float


TARGET = AtmosphereState(0.0, 20.95, 420.0, 1900.0, 0.0, 14.0, 1013.25)
POST_PLAGUE = AtmosphereState(0.0, 3.2, 85_000.0, 42_000.0, 0.97, 62.0, 1280.0)
TAU0: Dict[str, float] = {"o2_pct": 140.0, "co2_ppm": 160.0, "ch4_ppb": 12.0,
                          "toxin": 90.0, "temp_c": 110.0, "pressure_hpa": 60.0}
N_HALF = 500.0
BETA_WET = 0.6
SIGMA = 0.03
SAMPLE_EVERY = 5.0


@dataclass
class WeatherCell:
    cell_id: str
    region: str
    kind: str
    intensity: float = 0.5
    active: bool = False


class AETHER(Subsystem):
    NAME = "AETHER"
    ROLE = "Phase 2: detoxify the atmosphere and regulate weather"

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.state = POST_PLAGUE
        self.history: List[AtmosphereState] = [POST_PLAGUE]
        self.processors = 0
        self.cells = [WeatherCell(*c) for c in (
            ("WC-01", "North Atlantic", "PRECIPITATION"), ("WC-02", "Pacific Equatorial", "HIGH_PRESSURE"),
            ("WC-03", "Arctic", "POLAR_VORTEX"), ("WC-04", "Sahara", "THERMAL_LOW"),
            ("WC-05", "Indian Ocean", "MONSOON"), ("WC-06", "Siberian Plateau", "HIGH_PRESSURE"),
            ("WC-07", "Amazon Basin", "PRECIPITATION"), ("WC-08", "Southern Ocean", "WESTERLIES"))]
        self._breathable_announced = False

    # -- controls ---------------------------------------------------------
    def activate_weather(self) -> None:
        for c in self.cells:
            c.active = True
        self.emit("WEATHER_CONTROL_ACTIVE", cells=len(self.cells))

    def induce_precipitation(self, region: str, intensity: float = 0.7) -> bool:
        for c in self.cells:
            if c.region == region:
                c.kind, c.intensity, c.active = "PRECIPITATION", min(1.0, intensity), True
                return True
        return False

    @property
    def precip_fraction(self) -> float:
        wet = sum(c.intensity for c in self.cells if c.active and c.kind in ("PRECIPITATION", "MONSOON"))
        return wet / len(self.cells)

    def tau(self, var: str) -> float:
        t = TAU0[var] / (1.0 + self.processors / N_HALF)
        if var == "toxin":
            t /= 1.0 + BETA_WET * self.precip_fraction
        return t

    # -- dynamics ---------------------------------------------------------
    def _advance(self, s: AtmosphereState, dt: float) -> AtmosphereState:
        new = {}
        for v in VARS:
            x = relax(getattr(s, v), getattr(TARGET, v), dt, self.tau(v))
            dev = (x - getattr(TARGET, v)) * math.exp(self.rng.gauss(0.0, SIGMA))
            new[v] = getattr(TARGET, v) + dev
        new["toxin"] = max(0.0, new["toxin"])
        return replace(s, year=s.year + dt, **new)

    def step(self, dt: float, env) -> None:
        # processors are built by HEPHAESTUS: fleet health scales the deployable count
        self.processors = int(1200 * env.get("fleet", 1.0))
        t, s = 0.0, self.state
        while t < dt - 1e-9:
            h = min(SAMPLE_EVERY, dt - t)
            s = self._advance(s, h)
            self.history.append(s)
            t += h
        self.state = s
        if self.breathable and not self._breathable_announced:
            self._breathable_announced = True
            self.emit("ATMOSPHERE_BREATHABLE", o2=round(s.o2_pct, 2), toxin=round(s.toxin, 4))

    def _setup(self, env) -> None:
        self.activate_weather()
        self.induce_precipitation("Sahara", 0.5)   # greening the largest dust source accelerates rain-out

    @property
    def breathable(self) -> bool:
        s = self.state
        return s.o2_pct >= 19.5 and s.co2_ppm <= 5000 and s.toxin <= 0.02 and 5.0 <= s.temp_c <= 25.0

    def health(self) -> float:
        return geometric_mean(progress(getattr(POST_PLAGUE, v), getattr(self.state, v), getattr(TARGET, v))
                              for v in VARS)

    def _succeeded(self, h: float) -> bool:
        ok = self.breathable
        if not ok:
            self.set_status(SystemStatus.DEGRADED, "atmosphere not yet breathable")
        else:
            self.set_status(SystemStatus.ACTIVE, "breathable")
        return ok

    def _report(self):
        s = self.state
        return {"o2_pct": round(s.o2_pct, 2), "co2_ppm": round(s.co2_ppm), "ch4_ppb": round(s.ch4_ppb),
                "toxin": round(s.toxin, 4), "temp_c": round(s.temp_c, 2), "pressure_hpa": round(s.pressure_hpa, 1),
                "processors": self.processors, "tau_toxin_yr": round(self.tau("toxin"), 1),
                "breathable": self.breathable, "samples": len(self.history)}
