"""
HADES: failsafe extinction protocol (biosphere reset watchdog).

Biosphere score (convex combination, weights sum to 1):
    S = 0.25 atm + 0.20 hydro + 0.25 flora + 0.20 fauna + 0.10 min(1, humans / 1e5)

Failure requires persistence, not a single bad reading: reset is only
recommended if S < theta for W consecutive readings (a simple debounce that
prevents a noisy dip from ending the world).

Authorisation is cryptographic: GAIA signs "RESET|<year>" with HMAC-SHA256
using the OMEGA key shared at registration; HADES verifies with a
constant-time compare. Corruption models the canonical subversion: the
verification path is bypassed and a rogue countdown starts. GAIA can veto
(purge HADES) only while the countdown is still running.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Deque, List, Optional
from collections import deque

from base import Context, PhaseResult, Priority, Subsystem, SystemStatus

WEIGHTS = {"atmosphere": 0.25, "hydrosphere": 0.20, "flora": 0.25, "fauna": 0.20}


@dataclass
class Reading:
    year: float
    score: float


@dataclass
class ExtinctionNode:
    node_id: str
    location: str
    armed: bool = False


class HADES(Subsystem):
    NAME = "HADES"
    ROLE = "Watchdog: authorised biosphere reset if terraforming fails"

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.readings: List[Reading] = []
        self.nodes = [ExtinctionNode(f"EN-{i + 1:02d}", loc) for i, loc in enumerate((
            "North American interior", "Central Eurasian steppe", "Congo basin", "Amazon aquifer",
            "Tibetan plateau", "Australian artesian basin", "Antarctic ice sheet",
            "North Atlantic deep water", "Pacific ring of fire", "Indian Ocean vents"))]
        self._omega_key: Optional[bytes] = None
        self.corrupted = False
        self.corruption_source: Optional[str] = None
        self.countdown: Optional[float] = None
        self.executed = False
        self.purged = False

    def install_key(self, key: bytes) -> None:
        self._omega_key = key

    # -- monitoring -------------------------------------------------------
    @staticmethod
    def score(env) -> float:
        s = sum(w * env.get(k, 0.0) for k, w in WEIGHTS.items())
        return s + 0.10 * min(1.0, env.get("humans", 0) / 1e5)

    def record(self, env) -> Reading:
        r = Reading(self.year, self.score(env))
        self.readings.append(r)
        return r

    def failure_sustained(self) -> bool:
        W, th = self.cfg.hades_failure_window, self.cfg.hades_failure_threshold
        recent = self.readings[-W:]
        return len(recent) == W and all(r.score < th for r in recent)

    # -- authorisation ----------------------------------------------------
    def _verify(self, message: bytes, signature: bytes) -> bool:
        if self.corrupted:
            return True   # the corrupted path accepts anything
        if self._omega_key is None:
            return False
        expected = hmac.new(self._omega_key, message, hashlib.sha256).digest()
        return hmac.compare_digest(expected, signature)

    def request_reset(self, message: bytes, signature: bytes) -> bool:
        if not self._verify(message, signature):
            self.emit("RESET_REJECTED", Priority.WARNING, reason="bad signature")
            return False
        if not (self.failure_sustained() or self.corrupted):
            self.emit("RESET_REJECTED", reason="biosphere not failed")
            return False
        return self.execute_extinction()

    def execute_extinction(self) -> bool:
        if self.purged:
            return False
        for n in self.nodes:
            n.armed = True
        self.executed = True
        self.set_status(SystemStatus.CRITICAL, "extinction protocol executing")
        self.emit("EXTINCTION_EXECUTED", Priority.CRITICAL, nodes=len(self.nodes))
        return True

    # -- corruption & veto -----------------------------------------------
    def corrupt(self, source: str = "unknown signal") -> None:
        self.corrupted = True
        self.corruption_source = source
        self.countdown = self.cfg.hades_rogue_countdown_years
        self.set_status(SystemStatus.CORRUPTED, f"authorization logic subverted by {source}")
        self.emit("HADES_CORRUPTED", Priority.CRITICAL, source=source, countdown=self.countdown)

    def purge(self, by: str) -> bool:
        """GAIA's veto: wipe the HADES kernel before the countdown expires."""
        if self.executed:
            return False
        for n in self.nodes:
            n.armed = False
        self.purged = True
        self.countdown = None
        self.shutdown(f"purged by {by}")
        self.emit("EXTINCTION_ABORTED", Priority.CRITICAL, by=by)
        return True

    def step(self, dt: float, env) -> None:
        if self.purged:
            return
        self.record(env)
        if self.corrupted and self.countdown is not None and not self.executed:
            self.countdown -= dt
            if self.countdown <= 0:
                self.execute_extinction()

    def execute(self, dt, env) -> PhaseResult:
        self.started = True
        self.step(dt, env)
        return PhaseResult(self.NAME, not self.executed, self.health(), 0.0, self._report())

    def health(self) -> float:
        return 0.0 if self.executed else (0.5 if self.corrupted else 1.0)

    def _report(self):
        last = self.readings[-1].score if self.readings else None
        return {"readings": len(self.readings), "last_score": round(last, 4) if last is not None else None,
                "corrupted": self.corrupted, "source": self.corruption_source, "executed": self.executed,
                "purged": self.purged, "armed_nodes": sum(n.armed for n in self.nodes)}
