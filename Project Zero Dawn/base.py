"""
Project Zero Dawn: shared kernel primitives.

Everything a subsystem needs lives here:
  * lifecycle states and event priorities
  * the immutable Event record carried on the ALOY bus
  * the simulation Context (config, seeded RNG, clock, bus)
  * the Subsystem base class (lifecycle, logging, events, reporting)
  * a small numerics toolkit (relaxation, logistic growth, RK4, sampling)

Design rules
  1. One seeded random.Random per run. Same seed, same universe.
  2. Subsystems never import each other. They talk through the bus and
     read the shared environment vector GAIA hands them.
  3. Closed-form solutions are used wherever the ODE allows it (O(1) per
     step); RK4 / Euler only where the system is genuinely coupled.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from enum import Enum, IntEnum, auto
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence

LOG_FORMAT = "%(asctime)s [%(name)-10s] %(levelname)-7s %(message)s"


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------
class SystemStatus(Enum):
    DORMANT = auto()
    INITIALIZING = auto()
    ACTIVE = auto()
    DEGRADED = auto()
    CRITICAL = auto()
    OFFLINE = auto()
    AUTONOMOUS = auto()   # running outside GAIA's mandate (HEPHAESTUS)
    CORRUPTED = auto()    # logic or data integrity compromised (HADES, APOLLO)

    @property
    def operational(self) -> bool:
        return self not in (SystemStatus.DORMANT, SystemStatus.INITIALIZING, SystemStatus.OFFLINE)


class Priority(IntEnum):
    INFO = 1
    WARNING = 2
    CRITICAL = 3


class Phase(Enum):
    MANUFACTURE = auto()   # HEPHAESTUS
    FARO_PURGE = auto()    # MINERVA
    ATMOSPHERE = auto()    # AETHER
    HYDROSPHERE = auto()   # POSEIDON
    FLORA = auto()         # DEMETER
    FAUNA = auto()         # ARTEMIS
    HUMANITY = auto()      # ELEUTHIA
    KNOWLEDGE = auto()     # APOLLO
    COMPLETE = auto()
    ABORTED = auto()


class MachineClass(Enum):
    RECON = auto()
    GRAZER = auto()
    TRANSPORT = auto()
    COMBAT = auto()
    AERIAL = auto()
    AQUATIC = auto()
    CONSTRUCTION = auto()
    APEX = auto()


# --------------------------------------------------------------------------
# Events, config, context
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Event:
    sender: str
    kind: str
    payload: Mapping[str, Any]
    year: float
    priority: Priority = Priority.INFO

    def __str__(self) -> str:
        return f"T+{self.year:7.1f}  {self.priority.name:<8} {self.sender}:{self.kind} {dict(self.payload)}"


class Bus(Protocol):
    def publish(self, event: Event) -> None: ...


@dataclass
class SimConfig:
    seed: int = 2064
    # MINERVA
    minerva_key_hex_digits: int = 4          # keyspace N = 16**digits
    minerva_worst_case_years: float = 180.0  # years to exhaust the full keyspace
    minerva_coverage_target: float = 0.99
    minerva_tower_range_km: float = 5000.0
    minerva_mc_samples: int = 4000
    # GAIA phase control
    max_phase_extensions: int = 8
    gate_wait_years: float = 20.0
    max_gate_waits: int = 10
    # APOLLO erasure coding
    apollo_shards_n: int = 9
    apollo_shards_k: int = 4
    apollo_blocks_per_domain: int = 64
    apollo_shard_survival_p: float = 0.30
    # HADES
    hades_failure_threshold: float = 0.20
    hades_failure_window: int = 3
    hades_rogue_countdown_years: float = 15.0
    gaia_veto_enabled: bool = True
    # ALOY
    archive_maxlen: int = 20_000


@dataclass
class SimClock:
    year: float = 0.0

    def advance(self, dt: float) -> float:
        if dt < 0:
            raise ValueError("time only flows forward")
        self.year += dt
        return self.year


@dataclass
class Context:
    config: SimConfig
    rng: random.Random
    clock: SimClock = field(default_factory=SimClock)
    bus: Optional[Bus] = None

    @classmethod
    def create(cls, config: Optional[SimConfig] = None) -> "Context":
        cfg = config or SimConfig()
        return cls(config=cfg, rng=random.Random(cfg.seed))


@dataclass
class PhaseResult:
    subsystem: str
    success: bool
    health: float
    years: float
    detail: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Subsystem base class
# --------------------------------------------------------------------------
class Subsystem:
    """
    Lifecycle: DORMANT -> INITIALIZING -> ACTIVE (-> DEGRADED/CRITICAL/...)

    Contract for subclasses
      execute(dt, env)  first call runs phase setup, every call advances dt years
      step(dt, env)     background evolution once the phase has started
      health()          scalar in [0, 1]; GAIA builds the environment from these
      _report()         subsystem-specific metrics
    """

    NAME = "SUBSYSTEM"
    ROLE = ""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.status = SystemStatus.DORMANT
        self.log = logging.getLogger(self.NAME)
        self.boot_year: Optional[float] = None
        self.started = False

    # -- convenience ----------------------------------------------------
    @property
    def name(self) -> str:
        return self.NAME

    @property
    def rng(self) -> random.Random:
        return self.ctx.rng

    @property
    def cfg(self) -> SimConfig:
        return self.ctx.config

    @property
    def year(self) -> float:
        return self.ctx.clock.year

    # -- lifecycle ------------------------------------------------------
    def boot(self) -> "Subsystem":
        if self.status is not SystemStatus.DORMANT:
            return self
        self.status = SystemStatus.INITIALIZING
        self._on_boot()
        if self.status is SystemStatus.INITIALIZING:
            self.status = SystemStatus.ACTIVE
        self.boot_year = self.year
        self.log.info("online | %s", self.ROLE)
        self.emit("ONLINE")
        return self

    def _on_boot(self) -> None:
        pass

    def shutdown(self, reason: str = "") -> None:
        self.set_status(SystemStatus.OFFLINE, reason or "shutdown")

    def set_status(self, new: SystemStatus, reason: str = "") -> None:
        if new is self.status:
            return
        level = logging.WARNING if new in (SystemStatus.CRITICAL, SystemStatus.CORRUPTED,
                                           SystemStatus.AUTONOMOUS, SystemStatus.OFFLINE) else logging.INFO
        self.log.log(level, "status %s -> %s %s", self.status.name, new.name, f"({reason})" if reason else "")
        old, self.status = self.status, new
        self.emit("STATUS_CHANGE", Priority.WARNING if level == logging.WARNING else Priority.INFO,
                  old=old.name, new=new.name, reason=reason)

    # -- interface ------------------------------------------------------
    def execute(self, dt: float, env: Mapping[str, float]) -> PhaseResult:
        if not self.started:
            self.started = True
            self._setup(env)
        self.step(dt, env)
        h = self.health()
        return PhaseResult(self.NAME, self._succeeded(h), h, dt, self._report())

    def _setup(self, env: Mapping[str, float]) -> None:
        pass

    def step(self, dt: float, env: Mapping[str, float]) -> None:
        pass

    def _succeeded(self, h: float) -> bool:
        return h >= 0.9

    def health(self) -> float:
        return 1.0

    # -- events & reporting --------------------------------------------
    def emit(self, kind: str, priority: Priority = Priority.INFO, **payload: Any) -> None:
        bus = self.ctx.bus
        if bus is not None:
            bus.publish(Event(self.NAME, kind, payload, self.year, priority))

    def report(self) -> Dict[str, Any]:
        return {"subsystem": self.NAME, "status": self.status.name,
                "health": round(self.health(), 4), **self._report()}

    def _report(self) -> Dict[str, Any]:
        return {}

    def __repr__(self) -> str:
        return f"<{self.NAME} {self.status.name} h={self.health():.3f}>"


# --------------------------------------------------------------------------
# Numerics toolkit
# --------------------------------------------------------------------------
def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def relax(x0: float, target: float, dt: float, tau: float) -> float:
    """First-order relaxation dx/dt = -(x - x*)/tau  =>  x(t) = x* + (x0 - x*) e^(-t/tau)."""
    if tau <= 0:
        return target
    return target + (x0 - target) * math.exp(-dt / tau)


def logistic(n0: float, K: float, r: float, t: float) -> float:
    """Closed-form logistic: N(t) = K / (1 + ((K - N0)/N0) e^(-r t))."""
    if n0 <= 0 or K <= 0:
        return 0.0
    return K / (1.0 + (K - n0) / n0 * math.exp(-r * t))


def progress(x0: float, x: float, target: float) -> float:
    """Normalised progress from x0 towards target, clipped to [0, 1]."""
    span = abs(x0 - target)
    return 1.0 if span == 0 else clamp(1.0 - abs(x - target) / span)


def geometric_mean(values: Iterable[float], eps: float = 1e-9) -> float:
    vals = list(values)
    if not vals:
        return 0.0
    return math.exp(sum(math.log(max(v, eps)) for v in vals) / len(vals))


def rk4(f: Callable[[float, Sequence[float]], Sequence[float]],
        y0: Sequence[float], t0: float, t1: float, h: float) -> List[float]:
    """Classical 4th-order Runge-Kutta, local error O(h^5), global O(h^4)."""
    y, t = list(y0), t0
    while t < t1 - 1e-12:
        hh = min(h, t1 - t)
        k1 = f(t, y)
        k2 = f(t + hh / 2, [yi + hh / 2 * ki for yi, ki in zip(y, k1)])
        k3 = f(t + hh / 2, [yi + hh / 2 * ki for yi, ki in zip(y, k2)])
        k4 = f(t + hh, [yi + hh * ki for yi, ki in zip(y, k3)])
        y = [yi + hh / 6 * (a + 2 * b + 2 * c + d) for yi, a, b, c, d in zip(y, k1, k2, k3, k4)]
        t += hh
    return y


def binomial(rng: random.Random, n: int, p: float) -> int:
    """Exact for small n, normal approximation (with continuity) when n p (1-p) > 25."""
    if n <= 0 or p <= 0:
        return 0
    if p >= 1:
        return n
    var = n * p * (1 - p)
    if var > 25:
        return int(clamp(round(rng.gauss(n * p, math.sqrt(var))), 0, n))
    return sum(1 for _ in range(n) if rng.random() < p)


def poisson(rng: random.Random, lam: float) -> int:
    """Knuth's multiplication method for lam < 30, normal approximation above."""
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def largest_remainder(total: int, weights: Sequence[float]) -> List[int]:
    """Hamilton apportionment: integer split of `total` proportional to weights, sums exactly."""
    w = sum(weights)
    if total <= 0 or w <= 0:
        return [0] * len(weights)
    quotas = [total * wi / w for wi in weights]
    floors = [int(q) for q in quotas]
    left = total - sum(floors)
    order = sorted(range(len(weights)), key=lambda i: quotas[i] - floors[i], reverse=True)
    for i in order[:left]:
        floors[i] += 1
    return floors
