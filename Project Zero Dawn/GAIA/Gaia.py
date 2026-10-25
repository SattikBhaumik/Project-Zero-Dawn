"""
GAIA Prime: orchestration, coupling and judgment.

Coupling
  GAIA builds an environment vector from subordinate health scores,
      env = {faro, atmosphere, hydrosphere, flora, fauna, fleet,
             machine_threat, humans, students, knowledge, biosphere}
  with biosphere = geometric mean(atmosphere, hydrosphere, flora, fauna),
  and hands it to every subsystem. Time integration is first-order operator
  splitting (Lie): the phase owner advances dt, then every already-started
  subsystem advances dt in the background against the same snapshot.

Judgment
  * Gates: a phase starts only when upstream metrics clear its thresholds;
    otherwise GAIA waits in gate_wait_years increments (bounded).
  * Extensions: a phase that ends below its success criterion is extended
    (bounded by max_phase_extensions) instead of being marked done; if an
    extension improves health by < 1e-3 the phase is declared stalled.
  * Veto: on HADES corruption GAIA purges HADES at the cost of her own
    executive layer, after which she can no longer extend phases.
  * Authorised reset: if HADES reports sustained failure, GAIA signs the
    reset with the OMEGA key (HMAC-SHA256).
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from base import (Context, Event, Phase, PhaseResult, Priority, Subsystem, SystemStatus,
                  geometric_mean)


@dataclass(frozen=True)
class PhaseSpec:
    phase: Phase
    subsystem: str
    base_years: float
    extension_years: float
    gate: Tuple[Tuple[str, float], ...] = ()


PLAN: Tuple[PhaseSpec, ...] = (
    PhaseSpec(Phase.MANUFACTURE, "HEPHAESTUS", 10, 5),
    PhaseSpec(Phase.FARO_PURGE, "MINERVA", 0, 10),
    PhaseSpec(Phase.ATMOSPHERE, "AETHER", 80, 20, (("faro", 0.99),)),
    PhaseSpec(Phase.HYDROSPHERE, "POSEIDON", 60, 20, (("atmosphere", 0.80),)),
    PhaseSpec(Phase.FLORA, "DEMETER", 100, 25, (("atmosphere", 0.85), ("hydrosphere", 0.70))),
    PhaseSpec(Phase.FAUNA, "ARTEMIS", 75, 25, (("flora", 0.50),)),
    PhaseSpec(Phase.HUMANITY, "ELEUTHIA", 60, 20, (("biosphere", 0.70),)),
    PhaseSpec(Phase.KNOWLEDGE, "APOLLO", 20, 10),
)


@dataclass
class PhaseRecord:
    phase: str
    subsystem: str
    start: float
    end: float
    success: bool
    health: float
    extensions: int
    gate_waits: int


class GAIA(Subsystem):
    NAME = "GAIA"
    ROLE = "core AI: orchestrates the Zero Dawn terraforming sequence"

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.subs: Dict[str, Subsystem] = {}
        self.aloy = None
        self._omega_key = ctx.rng.getrandbits(256).to_bytes(32, "big")
        self.started_subs: List[Subsystem] = []
        self.records: List[PhaseRecord] = []
        self.timeline: List[Dict[str, float]] = []
        self.phase: Phase = Phase.MANUFACTURE
        self.aborted = False
        self.executive_intact = True
        self.hooks: Dict[Tuple[str, Phase], List[Callable[["GAIA"], None]]] = {}

    # -- wiring -----------------------------------------------------------
    def attach_bus(self, aloy) -> None:
        self.aloy = aloy
        self.subs[aloy.NAME] = aloy
        aloy.register(self.NAME)
        aloy.register(aloy.NAME)
        aloy.subscribe("*", self._on_event)

    def register(self, sub: Subsystem) -> None:
        self.subs[sub.NAME] = sub
        if self.aloy:
            self.aloy.register(sub.NAME)
        if hasattr(sub, "install_key"):
            sub.install_key(self._omega_key)

    def get(self, name: str) -> Optional[Subsystem]:
        return self.subs.get(name)

    def on(self, when: str, phase: Phase, fn: Callable[["GAIA"], None]) -> None:
        self.hooks.setdefault((when, phase), []).append(fn)

    # -- event reactions --------------------------------------------------
    def _on_event(self, ev: Event) -> None:
        if ev.kind == "HADES_CORRUPTED":
            self._veto_hades()
        elif ev.kind == "HEPHAESTUS_AUTONOMOUS" and self.aloy:
            self.aloy.send(self.NAME, "ELEUTHIA", "MACHINE_HOSTILITY",
                           "HEPHAESTUS no longer answers to GAIA. Avoid machine-dense territory.",
                           Priority.WARNING)
        elif ev.kind == "EXTINCTION_EXECUTED":
            self._collapse()

    def _veto_hades(self) -> None:
        hades = self.get("HADES")
        if not hades or not self.cfg.gaia_veto_enabled:
            self.log.warning("HADES corrupted and no veto available: countdown is running")
            return
        if hades.purge(by=self.NAME):
            self.executive_intact = False
            self.set_status(SystemStatus.DEGRADED, "executive layer sacrificed to purge HADES")
            self.log.warning("veto complete; subordinates continue without phase extensions")

    def _collapse(self) -> None:
        self.aborted = True
        for s in self.subs.values():
            if hasattr(s, "collapse"):
                s.collapse()
        self.set_status(SystemStatus.CRITICAL, "biosphere reset executed")

    # -- environment & time ----------------------------------------------
    def environment(self) -> Dict[str, float]:
        g = lambda n, f=lambda s: s.health(): f(self.subs[n]) if n in self.subs else 0.0
        env = {"faro": g("MINERVA"), "atmosphere": g("AETHER"), "hydrosphere": g("POSEIDON"),
               "flora": g("DEMETER"), "fauna": g("ARTEMIS"), "knowledge": g("APOLLO"),
               "fleet": g("HEPHAESTUS"),
               "machine_threat": g("HEPHAESTUS", lambda s: s.machine_threat),
               "humans": g("ELEUTHIA", lambda s: s.population()),
               "students": g("ELEUTHIA", lambda s: s.total_born())}
        env["biosphere"] = geometric_mean([env["atmosphere"], env["hydrosphere"], env["flora"], env["fauna"]])
        return env

    def _advance(self, dt: float, owner: Optional[Subsystem]) -> None:
        if dt <= 0:
            return
        env = self.environment()
        for s in self.started_subs:
            if s is not owner and s.status.operational and not self.aborted:
                s.step(dt, env)
        self.ctx.clock.advance(dt)
        hades = self.get("HADES")
        if hades and hades.status.operational:
            hades.step(dt, self.environment())
            if hades.failure_sustained() and not hades.executed:
                self.request_reset("sustained biosphere failure")
        self.timeline.append({"year": round(self.year, 2), "phase": self.phase.name,
                              **{k: round(v, 4) for k, v in self.environment().items()}})

    def request_reset(self, reason: str) -> bool:
        hades = self.get("HADES")
        msg = f"RESET|{self.year:.1f}|{reason}".encode()
        sig = hmac.new(self._omega_key, msg, hashlib.sha256).digest()
        self.log.warning("requesting authorised reset: %s", reason)
        return bool(hades and hades.request_reset(msg, sig))

    # -- sequence ---------------------------------------------------------
    def boot_all(self) -> None:
        self.boot()
        for s in self.subs.values():   # HADES is stepped explicitly in _advance, never as a phase owner
            s.boot()

    def _gate_ok(self, spec: PhaseSpec, env) -> bool:
        return all(env.get(k, 0.0) >= v for k, v in spec.gate)

    def _run_phase(self, spec: PhaseSpec) -> PhaseRecord:
        self.phase = spec.phase
        sub = self.subs[spec.subsystem]
        start = self.year
        for fn in self.hooks.get(("before", spec.phase), []):
            fn(self)

        waits = 0
        while not self._gate_ok(spec, self.environment()) and waits < self.cfg.max_gate_waits and not self.aborted:
            waits += 1
            self._advance(self.cfg.gate_wait_years, None)
        if not self._gate_ok(spec, self.environment()):
            self.log.warning("%s gate not met after %d waits; proceeding degraded", spec.phase.name, waits)

        self.log.info("=== %s (%s) T+%.0f ===", spec.phase.name, spec.subsystem, self.year)
        if sub.status is SystemStatus.AUTONOMOUS:
            self.log.warning("%s is autonomous; phase runs outside GAIA control", sub.NAME)
        res = sub.execute(spec.base_years, self.environment())
        if sub not in self.started_subs:
            self.started_subs.append(sub)
        self._advance(res.years, sub)

        ext = 0
        limit = self.cfg.max_phase_extensions if self.executive_intact else 0
        while not res.success and ext < limit and not self.aborted:
            ext += 1
            prev = res.health
            res = sub.execute(spec.extension_years, self.environment())
            self._advance(res.years, sub)
            if res.health - prev < 1e-3:      # stall detection: more time will not help
                self.log.warning("%s stalled at health %.3f; accepting degraded outcome", spec.phase.name, res.health)
                break

        rec = PhaseRecord(spec.phase.name, spec.subsystem, round(start, 1), round(self.year, 1),
                          res.success, round(res.health, 4), ext, waits)
        self.records.append(rec)
        self.emit("PHASE_COMPLETE" if res.success else "PHASE_INCOMPLETE",
                  Priority.INFO if res.success else Priority.WARNING,
                  phase=spec.phase.name, health=rec.health, extensions=ext)
        for fn in self.hooks.get(("after", spec.phase), []):
            fn(self)
        return rec

    def run(self, epilogue_years: float = 100.0) -> Dict:
        self.boot_all()
        for spec in PLAN:
            if self.aborted:
                break
            if spec.subsystem in self.subs:
                self._run_phase(spec)
        if not self.aborted and epilogue_years > 0:
            self.phase = Phase.COMPLETE
            self.log.info("=== EPILOGUE: %d years of autonomous biosphere evolution ===", epilogue_years)
            for _ in range(int(epilogue_years // 25)):
                if self.aborted:
                    break
                self._advance(25.0, None)
        self.phase = Phase.ABORTED if self.aborted else Phase.COMPLETE
        self.emit("SEQUENCE_END", Priority.CRITICAL if self.aborted else Priority.INFO, outcome=self.phase.name)
        return self.final_report()

    # -- reporting --------------------------------------------------------
    def health(self) -> float:
        return 0.0 if self.aborted else self.environment()["biosphere"]

    def final_report(self) -> Dict:
        return {"outcome": self.phase.name, "year": round(self.year, 1),
                "executive_intact": self.executive_intact,
                "phases": [r.__dict__ for r in self.records],
                "environment": {k: round(v, 4) for k, v in self.environment().items()},
                "subsystems": {n: s.report() for n, s in self.subs.items()}}

    def export_json(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.final_report(), f, indent=2, default=str)

    def export_csv(self, path: str) -> None:
        if not self.timeline:
            return
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(self.timeline[0].keys()))
            w.writeheader()
            w.writerows(self.timeline)
