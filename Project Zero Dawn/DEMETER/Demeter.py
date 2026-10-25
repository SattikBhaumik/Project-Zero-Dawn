"""
DEMETER: floral reintroduction by ecological succession.

Seed viability in storage decays exponentially (a first-order simplification
of the Ellis-Roberts viability equation):
    v(t) = v0 * exp(-lambda * t),   lambda = ln 2 / (c * t_half)
t_half is the conventional (-18 C) seed-bank half-life; c = 5 models the
Zero Dawn vaults' cryogenic (liquid-nitrogen) storage. Viability sets the
size of the founding cohort, A0 = 2e-3 * A_target * v, and sowings with
v < 0.05 fail outright.

Per biome b, yearly integration (soil and cover are coupled, so no closed form):
    soil formation   dS/dt = (k_w + k_c * A/K + k_p * P) * (1 - S)   (k_w: abiotic weathering)
    vegetated area   dA/dt = r * A * (1 - A / K)
    growth rate      r = r0 * (0.3 + 0.7 S) * min(1, n_est / 2)
    carrying cap.    K = A_target * sqrt(min(atmosphere, hydrosphere))
where P is the summed viability of established pioneer species (capped at 1).

Succession gate: a functional role can only be seeded once soil passes
its threshold (pioneers 0, ground cover 0.2, understory 0.4, keystone 0.5,
canopy 0.6). This is the Clements-style sequence, enforced as physics rather
than as a hard-coded loop order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from base import Context, Subsystem, SystemStatus, clamp

SOIL_GATE = {"PIONEER": 0.0, "GROUND_COVER": 0.2, "UNDERSTORY": 0.4, "KEYSTONE": 0.5, "CANOPY": 0.6}
K_WEATHER, K_COVER, K_PIONEER, R0 = 0.002, 0.03, 0.02, 0.16
CRYO_FACTOR = 5.0
MIN_VIABILITY = 0.05
PRE_PLAGUE_STORAGE_YEARS = 50.0


@dataclass
class FloralSpecies:
    sid: str
    scientific: str
    common: str
    biome: str
    role: str
    half_life_yr: float
    seeded: bool = False
    established: bool = False
    viability: float = 0.0


@dataclass
class SeedVault:
    vault_id: str
    location: str
    holdings: List[str] = field(default_factory=list)
    intact: bool = True


@dataclass
class Biome:
    key: str
    name: str
    target_km2: float
    area_km2: float = 0.0
    soil: float = 0.05

    @property
    def coverage(self) -> float:
        return clamp(self.area_km2 / self.target_km2)


class DEMETER(Subsystem):
    NAME = "DEMETER"
    ROLE = "Phase 4: reintroduce flora through soil-gated ecological succession"

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.biomes: Dict[str, Biome] = {b.key: b for b in (
            Biome("TROPICAL", "Tropical rainforest", 17e6), Biome("TEMPERATE", "Temperate deciduous", 11e6),
            Biome("BOREAL", "Boreal forest", 17e6), Biome("GRASSLAND", "Grassland / savanna", 24e6),
            Biome("DESERT", "Desert scrub", 33e6), Biome("TUNDRA", "Tundra", 11e6),
            Biome("WETLAND", "Wetlands", 9e6))}
        self.species: List[FloralSpecies] = [FloralSpecies(*r) for r in (
            ("SP-01", "Polytrichum commune", "Common haircap moss", "BOREAL", "PIONEER", 600),
            ("SP-02", "Sphagnum fuscum", "Rusty peat moss", "WETLAND", "PIONEER", 500),
            ("SP-03", "Cecropia obtusifolia", "Trumpet tree", "TROPICAL", "PIONEER", 250),
            ("SP-04", "Dryas octopetala", "Mountain avens", "TUNDRA", "PIONEER", 400),
            ("SP-05", "Larrea tridentata", "Creosote bush", "DESERT", "PIONEER", 450),
            ("SP-06", "Pteridium aquilinum", "Bracken fern", "TEMPERATE", "PIONEER", 350),
            ("SP-07", "Andropogon gerardii", "Big bluestem", "GRASSLAND", "PIONEER", 300),
            ("SP-08", "Poa pratensis", "Kentucky bluegrass", "TEMPERATE", "GROUND_COVER", 300),
            ("SP-09", "Trifolium repens", "White clover (N-fixer)", "GRASSLAND", "GROUND_COVER", 400),
            ("SP-10", "Vaccinium myrtillus", "Bilberry", "BOREAL", "GROUND_COVER", 250),
            ("SP-11", "Phragmites australis", "Common reed", "WETLAND", "GROUND_COVER", 300),
            ("SP-12", "Alnus glutinosa", "Black alder (N-fixer)", "TEMPERATE", "UNDERSTORY", 200),
            ("SP-13", "Salix arctica", "Arctic willow", "TUNDRA", "UNDERSTORY", 200),
            ("SP-14", "Acacia tortilis", "Umbrella thorn", "GRASSLAND", "UNDERSTORY", 350),
            ("SP-15", "Opuntia ficus-indica", "Prickly pear", "DESERT", "KEYSTONE", 300),
            ("SP-16", "Ficus benghalensis", "Banyan (fig keystone)", "TROPICAL", "KEYSTONE", 150),
            ("SP-17", "Quercus robur", "English oak", "TEMPERATE", "CANOPY", 120),
            ("SP-18", "Picea abies", "Norway spruce", "BOREAL", "CANOPY", 220),
            ("SP-19", "Shorea robusta", "Sal tree", "TROPICAL", "CANOPY", 80),
            ("SP-20", "Taxodium distichum", "Bald cypress", "WETLAND", "CANOPY", 180),
        )]
        locs = ["Svalbard Global Seed Vault, Norway", "Vavilov Institute, Russia",
                "USDA Germplasm System, USA", "Millennium Seed Bank, Kew, UK",
                "Zero Dawn Supplemental Vault, Greenland"]
        self.vaults = [SeedVault(f"SV-{i + 1:02d}", loc) for i, loc in enumerate(locs)]
        # every accession is duplicated in two vaults (redundancy), plus a random third copy
        for i, sp in enumerate(self.species):
            for j in {i % 5, (i + 1) % 5, ctx.rng.randrange(5)}:
                self.vaults[j].holdings.append(sp.sid)
        self.seeding_log: List[str] = []

    # -- genetic retrieval ------------------------------------------------
    def viability(self, sp: FloralSpecies) -> float:
        if not any(v.intact and sp.sid in v.holdings for v in self.vaults):
            return 0.0
        t = PRE_PLAGUE_STORAGE_YEARS + self.year
        return math.exp(-math.log(2) / (CRYO_FACTOR * sp.half_life_yr) * t)

    def _try_seed(self, sp: FloralSpecies, biome: Biome) -> None:
        sp.seeded = True
        sp.viability = self.viability(sp) * self.rng.uniform(0.85, 1.0)
        sp.established = sp.viability >= MIN_VIABILITY
        if sp.established:
            biome.area_km2 += 2e-3 * biome.target_km2 * sp.viability
        self.seeding_log.append(f"T+{self.year:.0f} {sp.role:<12} {sp.common:<26} v={sp.viability:.2f} "
                                f"{'ESTABLISHED' if sp.established else 'FAILED'}")
        if not sp.established:
            self.emit("SEEDING_FAILED", species=sp.common, viability=round(sp.viability, 3))

    # -- dynamics ---------------------------------------------------------
    def step(self, dt: float, env) -> None:
        suit = math.sqrt(clamp(min(env.get("atmosphere", 1.0), env.get("hydrosphere", 1.0))))
        years = int(round(dt))
        for _ in range(years):
            for b in self.biomes.values():
                local = [s for s in self.species if s.biome == b.key]
                for sp in local:
                    if not sp.seeded and b.soil >= SOIL_GATE[sp.role]:
                        self._try_seed(sp, b)
                est = [s for s in local if s.established]
                pioneers = min(1.0, sum(s.viability for s in est if s.role == "PIONEER"))
                K = b.target_km2 * suit
                cover = b.area_km2 / K if K else 0.0
                b.soil = clamp(b.soil + (K_WEATHER + K_COVER * cover + K_PIONEER * pioneers) * (1 - b.soil))
                if b.area_km2 > 0 and K > 0:
                    r = R0 * (0.3 + 0.7 * b.soil) * min(1.0, len(est) / 2)
                    b.area_km2 = max(0.0, b.area_km2 + r * b.area_km2 * (1 - b.area_km2 / K))
        self.emit("BIOME_UPDATE", coverage=round(self.health(), 4))

    def collapse(self) -> None:
        for b in self.biomes.values():
            b.area_km2 = 0.0

    def health(self) -> float:
        tot = sum(b.target_km2 for b in self.biomes.values())
        return sum(min(b.area_km2, b.target_km2) for b in self.biomes.values()) / tot

    def _succeeded(self, h: float) -> bool:
        ok = h >= 0.6
        self.set_status(SystemStatus.ACTIVE if ok else SystemStatus.DEGRADED,
                        "flora above faunal threshold" if ok else "flora below 60 %")
        return ok

    def _report(self):
        return {"species_seeded": sum(s.seeded for s in self.species),
                "species_established": sum(s.established for s in self.species),
                "biomes": {b.name: {"coverage": round(b.coverage, 3), "soil": round(b.soil, 3)}
                           for b in self.biomes.values()}}
