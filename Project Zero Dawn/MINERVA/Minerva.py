"""
MINERVA: Faro Plague countermeasure and GAIA security layer.

1. Brute force (real, not faked): the deactivation key is a hex string of
   length L; MINERVA only knows its salted SHA-256 digest and must search the
   keyspace N = 16^L. For a key placed uniformly at random,
       E[attempts] = (N + 1) / 2,
   and phase duration maps linearly onto lore time:
       t = (attempts / N) * T_max.
   Salt is pre-hashed once and copied per candidate (hashlib .copy()),
   which avoids re-absorbing the salt block on every attempt.

2. Broadcast coverage. A tower of ground range d covers a spherical cap
   with angular radius theta = d / R_earth, i.e. area fraction
       f = (1 - cos theta) / 2.
   For n randomly placed towers (independence approximation)
       C(n) = 1 - (1 - f)^n   =>   n_req = ceil( ln(1 - C*) / ln(1 - f) ).
   The actual coverage of the placed network is then checked by Monte Carlo
   on uniform points over the sphere (dot product against tower unit vectors,
   covered iff max(u . v) >= cos theta).

3. Firewall: intrusion probes ~ Poisson(lambda * dt).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from base import Context, PhaseResult, Priority, Subsystem, SystemStatus, poisson

EARTH_RADIUS_KM = 6371.0


def cap_fraction(range_km: float) -> float:
    theta = min(range_km / EARTH_RADIUS_KM, math.pi)
    return (1.0 - math.cos(theta)) / 2.0


def towers_required(f: float, target: float) -> int:
    if f >= 1.0:
        return 1
    return math.ceil(math.log(1.0 - target) / math.log(1.0 - f))


def _unit(lat_deg: float, lon_deg: float) -> Tuple[float, float, float]:
    la, lo = math.radians(lat_deg), math.radians(lon_deg)
    return (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))


@dataclass
class TransmissionTower:
    tower_id: str
    lat: float
    lon: float
    range_km: float
    online: bool = True
    broadcasts: int = 0

    @property
    def unit(self) -> Tuple[float, float, float]:
        return _unit(self.lat, self.lon)


class MINERVA(Subsystem):
    NAME = "MINERVA"
    ROLE = "Phase 1: crack Faro deactivation codes, broadcast shutdown, guard and cloak GAIA"

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        cfg = ctx.config
        self.digits = cfg.minerva_key_hex_digits
        self.keyspace = 16 ** self.digits
        self._salt = ctx.rng.getrandbits(128).to_bytes(16, "big")
        secret = ctx.rng.randrange(self.keyspace)          # discarded: only the digest is kept
        self._target = hashlib.sha256(self._salt + self._fmt(secret)).digest()
        self.code: Optional[str] = None
        self.attempts = 0
        self.crack_years = 0.0
        self.towers: List[TransmissionTower] = []
        self.coverage_mc = 0.0
        self.coverage_analytic = 0.0
        self.plague_silenced = False
        self.firewall_active = False
        self.gaia_cloaked = False
        self.probes_blocked = 0

    def _fmt(self, i: int) -> bytes:
        return format(i, f"0{self.digits}x").encode()

    def _on_boot(self) -> None:
        self.firewall_active = True
        self.gaia_cloaked = True
        self.log.info("firewall up, GAIA Prime location masked, keyspace N=%d", self.keyspace)

    # -- 1. brute force ---------------------------------------------------
    def brute_force(self) -> Optional[str]:
        base = hashlib.sha256(self._salt)
        target, fmt = self._target, self._fmt
        for i in range(self.keyspace):
            h = base.copy()
            h.update(fmt(i))
            if h.digest() == target:
                self.attempts = i + 1
                self.code = fmt(i).decode()
                break
        else:
            self.attempts = self.keyspace
            self.log.error("keyspace exhausted without a match")
            self.emit("CODE_SEARCH_FAILED", Priority.CRITICAL, attempts=self.attempts)
            return None
        self.crack_years = self.attempts / self.keyspace * self.cfg.minerva_worst_case_years
        expected = (self.keyspace + 1) / 2
        self.log.info("key found after %d attempts (E=%.0f, ratio %.2f) -> %.1f yr",
                      self.attempts, expected, self.attempts / expected, self.crack_years)
        self.emit("CODE_FOUND", attempts=self.attempts, years=round(self.crack_years, 2))
        return self.code

    # -- 2. tower network ---------------------------------------------------
    def _place_tower(self) -> None:
        u = self.rng.random()
        lat = math.degrees(math.asin(2 * u - 1))   # uniform on sphere, not uniform in latitude
        lon = 360.0 * self.rng.random() - 180.0
        self.towers.append(TransmissionTower(f"MINERVA-{len(self.towers) + 1:03d}", lat, lon,
                                             self.cfg.minerva_tower_range_km))

    def _monte_carlo_coverage(self) -> float:
        cos_t = math.cos(min(self.cfg.minerva_tower_range_km / EARTH_RADIUS_KM, math.pi))
        units = [t.unit for t in self.towers if t.online]
        if not units:
            return 0.0
        n, hit = self.cfg.minerva_mc_samples, 0
        for _ in range(n):
            z = 2 * self.rng.random() - 1
            phi = 2 * math.pi * self.rng.random()
            s = math.sqrt(1 - z * z)
            x, y = s * math.cos(phi), s * math.sin(phi)
            for ux, uy, uz in units:
                if ux * x + uy * y + uz * z >= cos_t:
                    hit += 1
                    break
        return hit / n

    def build_network(self) -> None:
        f = cap_fraction(self.cfg.minerva_tower_range_km)
        target = self.cfg.minerva_coverage_target
        n_req = towers_required(f, target)
        self.log.info("cap fraction f=%.4f -> n_req=%d towers for C*=%.2f", f, n_req, target)
        while len(self.towers) < n_req:
            self._place_tower()
        self.coverage_mc = self._monte_carlo_coverage()
        # random placement leaves holes; top up until the measured coverage meets the target
        while self.coverage_mc < target and len(self.towers) < 4 * n_req:
            for _ in range(max(1, n_req // 10)):
                self._place_tower()
            self.coverage_mc = self._monte_carlo_coverage()
        self.coverage_analytic = 1 - (1 - f) ** len(self.towers)
        self.log.info("%d towers | coverage analytic=%.4f monte-carlo=%.4f",
                      len(self.towers), self.coverage_analytic, self.coverage_mc)
        self.emit("TOWERS_ONLINE", towers=len(self.towers), coverage=round(self.coverage_mc, 4))

    def broadcast(self) -> bool:
        if self.code is None:
            return False
        for t in self.towers:
            if t.online:
                t.broadcasts += 1
        self.plague_silenced = self.coverage_mc >= self.cfg.minerva_coverage_target
        kind, pr = ("PLAGUE_SILENCED", Priority.INFO) if self.plague_silenced else ("BROADCAST_PARTIAL", Priority.WARNING)
        self.emit(kind, pr, coverage=round(self.coverage_mc, 4))
        return self.plague_silenced

    # -- 3. firewall ------------------------------------------------------
    def firewall_scan(self, dt: float, lam_per_decade: float = 2.0) -> int:
        probes = poisson(self.rng, lam_per_decade * dt / 10.0)
        self.probes_blocked += probes
        if probes:
            self.emit("INTRUSION_BLOCKED", Priority.WARNING, probes=probes)
        return probes

    # -- interface --------------------------------------------------------
    def execute(self, dt, env) -> PhaseResult:
        if not self.started:
            self.started = True
            self.brute_force()
            self.build_network()
            self.broadcast()
        self.firewall_scan(self.crack_years or dt)
        if not self.plague_silenced:
            self.set_status(SystemStatus.DEGRADED, "Faro swarm still active")
        h = self.health()
        return PhaseResult(self.NAME, self.plague_silenced, h, self.crack_years, self._report())

    def step(self, dt, env) -> None:
        self.firewall_scan(dt)

    def health(self) -> float:
        return self.coverage_mc if self.code else 0.0

    def _report(self):
        return {"keyspace": self.keyspace, "attempts": self.attempts,
                "expected_attempts": (self.keyspace + 1) / 2, "crack_years": round(self.crack_years, 2),
                "towers": len(self.towers), "coverage_analytic": round(self.coverage_analytic, 4),
                "coverage_mc": round(self.coverage_mc, 4), "plague_silenced": self.plague_silenced,
                "probes_blocked": self.probes_blocked}
