"""
ARTEMIS: faunal reintroduction and trophic-web stabilisation.

Guild dynamics (non-dimensional biomass, fractions of herbivore capacity),
a three-level food chain with logistic prey, integrated by RK4:

    dH/dt = r H (1 - H/K) - a1 H M - a2 H A
    dM/dt = e1 a1 H M - a3 M A - mM M
    dA/dt = e2 (a2 H + a3 M) A - mA A
    K     = 0.6 + 0.4 * flora   (flora = DEMETER health)

H herbivores (incl. pollinators/detritivores), M mesopredators/omnivores,
A apex predators; a = attack rates, e = assimilation efficiencies,
m = mortality. Guilds are released in order H -> M -> A, each only after
its prey guild crosses an establishment threshold (predators onto an empty
landscape just starve).

Trophic score uses Lindeman's ~10 % transfer rule:
    s_pair = exp(-| log10( (upper/lower) / 0.1 ) |)
    trophic = geometric mean over the (M/H, A/M) pairs

Species census: N_s = K_s * G / G_ref (guild level relative to its
reference equilibrium); a species is viable when N_s >= MVP (minimum
viable population, the ~50/500 rule scaled by body size).

Biobank material degrades as integrity(t) = exp(-t / 2000 yr); every
species is stored in at least two banks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List

from base import Context, Subsystem, SystemStatus, clamp, geometric_mean, rk4

PARAMS = dict(r=0.5, a1=3.32, a2=0.1, a3=1.5, e1=0.105, e2=0.2, mM=0.2, mA=0.03)
G_REF = {"H": 0.6, "M": 0.06, "A": 0.006}
RELEASE = {"H": 0.05, "M": 0.01, "A": 0.002}
GATE = {"M": ("H", 0.3), "A": ("M", 0.02)}
BANK_TAU = 2000.0


@dataclass
class FaunalSpecies:
    sid: str
    scientific: str
    common: str
    guild: str            # H | M | A
    mvp: int
    k_species: int = 0
    population: int = 0
    released: bool = False

    @property
    def viable(self) -> bool:
        return self.population >= self.mvp


@dataclass
class BioBank:
    bank_id: str
    location: str
    kind: str
    holdings: List[str] = field(default_factory=list)

    def integrity(self, t: float) -> float:
        return math.exp(-t / BANK_TAU)


class ARTEMIS(Subsystem):
    NAME = "ARTEMIS"
    ROLE = "Phase 5: reintroduce fauna in trophic order and stabilise the food web"

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.species: List[FaunalSpecies] = [FaunalSpecies(*r) for r in (
            ("FA-01", "Apis mellifera", "Honeybee", "H", 50_000),
            ("FA-02", "Lumbricus terrestris", "Earthworm", "H", 200_000),
            ("FA-03", "Bison bison", "American bison", "H", 5_000),
            ("FA-04", "Equus ferus", "Wild horse", "H", 3_000),
            ("FA-05", "Rangifer tarandus", "Reindeer", "H", 5_000),
            ("FA-06", "Elephas maximus", "Asian elephant", "H", 1_500),
            ("FA-07", "Chelonia mydas", "Green sea turtle", "H", 5_000),
            ("FA-08", "Oncorhynchus mykiss", "Rainbow trout", "M", 10_000),
            ("FA-09", "Rana temporaria", "Common frog", "M", 20_000),
            ("FA-10", "Grus grus", "Common crane", "M", 3_000),
            ("FA-11", "Vulpes vulpes", "Red fox", "M", 2_000),
            ("FA-12", "Salmo salar", "Atlantic salmon", "M", 10_000),
            ("FA-13", "Canis lupus", "Grey wolf", "A", 500),
            ("FA-14", "Panthera leo", "Lion", "A", 500),
            ("FA-15", "Aquila chrysaetos", "Golden eagle", "A", 500),
            ("FA-16", "Ursus arctos", "Brown bear", "A", 500),
            ("FA-17", "Balaenoptera musculus", "Blue whale", "A", 500),
        )]
        for sp in self.species:
            sp.k_species = int(sp.mvp * ctx.rng.uniform(3.0, 8.0))
        self.banks = [BioBank(*r) for r in (
            ("BB-01", "Frozen Ark, Nottingham", "CRYOGENIC"), ("BB-02", "Frozen Zoo, San Diego", "LIVE_CULTURE"),
            ("BB-03", "Pleistocene Park, Siberia", "CRYOGENIC"), ("BB-04", "Zero Dawn Marine Bank, Azores", "EMBRYO"),
            ("BB-05", "Zero Dawn Invertebrate Archive, Kenya", "CRYOGENIC"))]
        for i, sp in enumerate(self.species):
            for j in {i % 5, (i + 2) % 5}:
                self.banks[j].holdings.append(sp.sid)
        self.G = {"H": 0.0, "M": 0.0, "A": 0.0}
        self.released_guilds: List[str] = []

    # -- release ----------------------------------------------------------
    def _retrieve(self, sp: FaunalSpecies) -> bool:
        t = 50.0 + self.year
        return any(sp.sid in b.holdings and b.integrity(t) >= 0.6 for b in self.banks)

    def _release(self, guild: str) -> None:
        n = 0
        for sp in self.species:
            if sp.guild == guild and self._retrieve(sp):
                sp.released = True
                n += 1
        if n:
            self.G[guild] = RELEASE[guild]
            self.released_guilds.append(guild)
            self.emit("GUILD_RELEASED", guild=guild, species=n)

    def _gates(self) -> None:
        if "H" not in self.released_guilds:
            self._release("H")
        for g, (prey, thr) in GATE.items():
            if g not in self.released_guilds and self.G[prey] >= thr:
                self._release(g)

    # -- dynamics ---------------------------------------------------------
    def _rhs(self, K: float):
        p = PARAMS

        def f(_t, y):
            H, M, A = (max(v, 0.0) for v in y)
            dH = p["r"] * H * (1 - H / K) - p["a1"] * H * M - p["a2"] * H * A
            dM = p["e1"] * p["a1"] * H * M - p["a3"] * M * A - p["mM"] * M
            dA = p["e2"] * (p["a2"] * H + p["a3"] * M) * A - p["mA"] * A
            return [dH, dM, dA]
        return f

    def step(self, dt: float, env) -> None:
        K = 0.6 + 0.4 * clamp(env.get("flora", 1.0))   # habitat beyond plants (detritus, marine) buffers K
        t = 0.0
        while t < dt - 1e-9:
            h = min(5.0, dt - t)          # re-check release gates every 5 simulated years
            self._gates()
            y = rk4(self._rhs(K), [self.G["H"], self.G["M"], self.G["A"]], 0.0, h, 0.1)
            self.G = {k: max(0.0, v) for k, v in zip("HMA", y)}
            t += h
        for sp in self.species:
            sp.population = int(sp.k_species * clamp(self.G[sp.guild] / G_REF[sp.guild], 0, 1.5)) if sp.released else 0

    def collapse(self) -> None:
        self.G = {"H": 0.0, "M": 0.0, "A": 0.0}
        for sp in self.species:
            sp.population = 0

    def trophic_score(self) -> float:
        H, M, A = self.G["H"], self.G["M"], self.G["A"]
        if min(H, M, A) <= 1e-6:
            return 0.0
        return geometric_mean(math.exp(-abs(math.log10((u / l) / 0.1))) for u, l in ((M, H), (A, M)))

    def viable_fraction(self) -> float:
        return sum(sp.viable for sp in self.species) / len(self.species)

    def health(self) -> float:
        return 0.6 * self.viable_fraction() + 0.4 * self.trophic_score()

    def _succeeded(self, h: float) -> bool:
        ok = self.viable_fraction() >= 0.75 and self.trophic_score() >= 0.5
        self.set_status(SystemStatus.ACTIVE if ok else SystemStatus.DEGRADED,
                        "food web stable" if ok else "food web unstable")
        return ok

    def _report(self):
        return {"guilds": {k: round(v, 4) for k, v in self.G.items()},
                "viable": sum(sp.viable for sp in self.species), "species": len(self.species),
                "trophic_score": round(self.trophic_score(), 3),
                "critical": [sp.common for sp in self.species if sp.released and not sp.viable]}
