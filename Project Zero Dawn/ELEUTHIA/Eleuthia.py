"""
ELEUTHIA: human reintroduction through the Cradle network.

Genetics
  * Founders are drawn by stratified round-robin across ancestry cohorts
    with alternating sex and least-used-sample priority, maximising
    diversity instead of sampling with replacement.
  * Cohort diversity: Simpson index  D = 1 - sum p_i^2.
  * Effective population size (Wright):  Ne = 4 Nm Nf / (Nm + Nf).
  * Expected heterozygosity retained after g generations (drift only):
        H_g / H_0 = (1 - 1 / (2 Ne))^g

Cradles
  * Births per year ~ Binomial(wombs, p_gestation) until each Cradle
    reaches its child target.
  * Childhood survival per year ~ Binomial(n, 1 - mu), mu = 0.004.
  * Each birth-year cohort is released at age 18 in tribes of 40 to 80
    (age-structured, which fixes the old bug where release depended on the
    Cradle's activation date instead of the children's age).

Tribes: logistic growth with a weak Allee effect and demographic noise
    dN/dt = r N (1 - N/K) (N - A)/(N + A),   r = r0 (1 - 0.5 * threat)
    K = K0 * biosphere,  A = Allee threshold (~15 people)
Groups below A collapse; machine threat (HEPHAESTUS) suppresses growth.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

from base import Context, PhaseResult, Priority, Subsystem, SystemStatus, binomial, clamp

COHORTS = ["East Asian", "South Asian", "Sub-Saharan African", "European", "Middle Eastern",
           "Oceanian", "Indigenous American", "Southeast Asian", "Central Asian"]
RELEASE_AGE = 18
CHILD_MORTALITY = 0.004
GESTATION_P = 0.92
R0_TRIBE, K0_TRIBE, ALLEE = 0.025, 20_000, 15.0
AUTH_THRESHOLD = 0.70
GENERATION_YR = 25.0


@dataclass
class GeneticSample:
    sample_id: str
    cohort: str
    sex: str
    health: float
    uses: int = 0


@dataclass
class Cradle:
    cradle_id: str
    name: str
    location: str
    region: str
    wombs: int
    child_target: int
    active: bool = False
    births: int = 0
    released: int = 0
    cohorts: Dict[int, int] = field(default_factory=dict)   # birth_year -> living children

    @property
    def children(self) -> int:
        return sum(self.cohorts.values())


@dataclass
class Tribe:
    tribe_id: str
    cradle_id: str
    region: str
    founded: float
    population: float
    founders: int

    @property
    def extinct(self) -> bool:
        return self.population <= 0


class ELEUTHIA(Subsystem):
    NAME = "ELEUTHIA"
    ROLE = "Phase 6: gestate, raise and release humanity from the Cradles"

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        rng = ctx.rng
        self.library: Dict[str, Deque[GeneticSample]] = {}
        n = 1
        for c in COHORTS:
            q: List[GeneticSample] = []
            for _ in range(rng.randint(80, 150)):
                q.append(GeneticSample(f"GS-{n:05d}", c, rng.choice("MF"), rng.uniform(0.75, 1.0)))
                n += 1
            rng.shuffle(q)
            self.library[c] = deque(s for s in q if rng.random() > 0.05)   # ~5 % of samples non-viable
        self.cradles = [Cradle(*r) for r in (
            ("CR-01", "Cradle Theta", "Rocky Mountains", "North American Grasslands", 60, 600),
            ("CR-02", "Cradle Iota", "Swiss Alps", "European Temperate Forest", 45, 450),
            ("CR-03", "Cradle Kappa", "Ural Mountains", "Eurasian Steppe", 50, 500),
            ("CR-04", "Cradle Lambda", "Ethiopian Highlands", "East African Savanna", 55, 550),
            ("CR-05", "Cradle Mu", "Tibetan Plateau", "Central Asian Plateau", 50, 500),
            ("CR-06", "Cradle Nu", "Andes", "South American Rainforest", 40, 400),
            ("CR-07", "Cradle Xi", "Great Dividing Range", "Australian Scrubland", 35, 350))]
        self.tribes: List[Tribe] = []
        self.founders = defaultdict(int)       # sex -> count
        self.cohort_counts = defaultdict(int)
        self.authorized = False
        self._rr = 0
        self._next_sex = "F"

    # -- genetics ---------------------------------------------------------
    def _draw_sample(self) -> GeneticSample:
        """Round-robin over cohorts; within a cohort rotate so the least-used sample is next."""
        for _ in range(len(COHORTS)):
            q = self.library[COHORTS[self._rr % len(COHORTS)]]
            self._rr += 1
            if q:
                # prefer the requested sex within a short look-ahead window
                for i in range(min(6, len(q))):
                    if q[i].sex == self._next_sex:
                        q.rotate(-i)
                        break
                s = q.popleft()
                s.uses += 1
                q.append(s)
                self._next_sex = "M" if s.sex == "F" else "F"
                return s
        raise RuntimeError("genetic library exhausted")

    @property
    def effective_population(self) -> float:
        m, f = self.founders["M"], self.founders["F"]
        return 4 * m * f / (m + f) if m + f else 0.0

    def heterozygosity_retained(self, generations: float) -> float:
        ne = self.effective_population
        return (1 - 1 / (2 * ne)) ** generations if ne > 0.5 else 0.0

    @property
    def simpson_diversity(self) -> float:
        tot = sum(self.cohort_counts.values())
        return 1 - sum((c / tot) ** 2 for c in self.cohort_counts.values()) if tot else 0.0

    # -- authorisation ----------------------------------------------------
    def authorize(self, env) -> bool:
        bio = env.get("biosphere", 0.0)
        ok = bio >= AUTH_THRESHOLD
        if ok and not self.authorized:
            self.authorized = True
            self.emit("HUMAN_REINTRO_AUTHORIZED", biosphere=round(bio, 3))
        elif not ok:
            self.log.warning("authorization denied: biosphere %.3f < %.2f", bio, AUTH_THRESHOLD)
        return self.authorized

    # -- yearly cradle cycle ----------------------------------------------
    def _gestate(self, c: Cradle, year: int) -> None:
        room = c.child_target - c.births
        if room <= 0:
            return
        attempts = min(c.wombs, room)
        born = 0
        for _ in range(attempts):
            s = self._draw_sample()
            if self.rng.random() < GESTATION_P * s.health:
                born += 1
                self.cohort_counts[s.cohort] += 1
                self.founders[s.sex] += 1
        if born:
            c.cohorts[year] = c.cohorts.get(year, 0) + born
            c.births += born

    def _age_and_release(self, c: Cradle, year: int) -> None:
        for by in list(c.cohorts):
            c.cohorts[by] = binomial(self.rng, c.cohorts[by], 1 - CHILD_MORTALITY)
            if year - by >= RELEASE_AGE:
                n = c.cohorts.pop(by)
                while n >= 40:
                    size = min(n, self.rng.randint(40, 80))
                    if n - size < 40:
                        size = n
                    n -= size
                    self._found_tribe(c, size)
                if n:   # a cohort under 40 joins this Cradle's latest tribe (or seeds one)
                    mine = [t for t in self.tribes if t.cradle_id == c.cradle_id]
                    if mine:
                        mine[-1].population += n
                        c.released += n
                    else:
                        self._found_tribe(c, n)

    def _found_tribe(self, c: Cradle, size: int) -> None:
        t = Tribe(f"TR-{len(self.tribes) + 1:04d}", c.cradle_id, c.region, self.year, float(size), size)
        self.tribes.append(t)
        c.released += size
        self.emit("TRIBE_RELEASED", tribe=t.tribe_id, size=size, region=c.region)

    def _grow_tribes(self, env) -> None:
        r = R0_TRIBE * (1 - 0.5 * clamp(env.get("machine_threat", 0.0)))
        K = K0_TRIBE * clamp(env.get("biosphere", 0.8))
        for t in self.tribes:
            N = t.population
            if N <= 0:
                continue
            dN = r * N * (1 - N / K) * (N - ALLEE) / (N + ALLEE)
            N += dN + self.rng.gauss(0.0, 0.3 * math.sqrt(N))
            if N < ALLEE / 2:
                N = 0.0
                self.emit("TRIBE_EXTINCT", Priority.WARNING, tribe=t.tribe_id)
            t.population = N

    def step(self, dt: float, env) -> None:
        if not self.authorized:
            return
        start = int(self.year)
        for k in range(int(round(dt))):
            y = start + k
            for i, c in enumerate(self.cradles):
                if not c.active and y - start >= 0 and k >= 2 * i:   # staggered boot, 2 yr apart
                    c.active = True
                    self.emit("CRADLE_ACTIVE", cradle=c.name)
                if c.active:
                    self._gestate(c, y)
                    self._age_and_release(c, y + 1)
            self._grow_tribes(env)

    def execute(self, dt, env) -> PhaseResult:
        self.started = True
        if not self.authorize(env):
            self.set_status(SystemStatus.DEGRADED, "awaiting biosphere clearance")
            return PhaseResult(self.NAME, False, 0.0, dt, self._report())
        return super().execute(dt, env)

    def collapse(self) -> None:
        for t in self.tribes:
            t.population = 0.0
        for c in self.cradles:
            c.cohorts.clear()

    # -- metrics ----------------------------------------------------------
    def population(self) -> int:
        return int(sum(t.population for t in self.tribes))

    def children(self) -> int:
        return sum(c.children for c in self.cradles)

    def total_born(self) -> int:
        return sum(c.births for c in self.cradles)

    def health(self) -> float:
        if not self.tribes:
            return 0.0
        alive = sum(not t.extinct for t in self.tribes) / len(self.tribes)
        return clamp(0.5 * alive + 0.5 * min(1.0, self.population() / 5000))

    def _succeeded(self, h: float) -> bool:
        ok = self.population() >= 5000 and self.heterozygosity_retained(10) >= 0.99
        self.set_status(SystemStatus.ACTIVE if ok else SystemStatus.DEGRADED,
                        "self-sustaining human population" if ok else "population still fragile")
        return ok

    def _report(self):
        return {"authorized": self.authorized, "born": self.total_born(), "children_in_cradles": self.children(),
                "tribes": len(self.tribes), "tribes_alive": sum(not t.extinct for t in self.tribes),
                "population": self.population(), "Ne": round(self.effective_population, 1),
                "H_retained_10gen": round(self.heterozygosity_retained(10), 4),
                "simpson_D": round(self.simpson_diversity, 4)}
