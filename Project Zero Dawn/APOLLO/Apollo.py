"""
APOLLO: archive of human knowledge and the Cradle curriculum.

Storage model: every domain is split into B blocks; each block is
erasure-coded into n shards, any k of which reconstruct it (Reed-Solomon
style, MDS code). If each shard survives a catastrophe independently with
probability p, a block is recoverable with

    P_rec = sum_{i=k}^{n} C(n, i) p^i (1 - p)^(n - i)

and the expected recovered fraction of the archive equals P_rec. With the
defaults n = 9, k = 4, p = 0.30 this gives P_rec ~ 0.27, close to the
"roughly a quarter survives" intuition, but here it is derived, not asserted.

Bit rot: integrity decays as exp(-lambda t) between scrubs; scrubbing
restores any block that still holds >= k healthy shards.

Education: module completion = base_rate * availability(category), where
availability is the record-weighted surviving fraction of that category.
The sabotage hook models Ted Faro's deletion of APOLLO.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from base import Context, Priority, Subsystem, SystemStatus, clamp

BIT_ROT_PER_YR = 2e-5
BASE_COMPLETION = 0.92


def p_recover(n: int, k: int, p: float) -> float:
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


@dataclass
class Domain:
    domain_id: str
    name: str
    category: str
    records: int
    blocks_ok: int
    blocks_total: int
    integrity: float = 1.0

    @property
    def available(self) -> float:
        return self.integrity * self.blocks_ok / self.blocks_total


@dataclass
class Module:
    module_id: str
    title: str
    category: str
    level: str
    hours: float


class APOLLO(Subsystem):
    NAME = "APOLLO"
    ROLE = "Phase 7: preserve human knowledge and educate the Cradle generations"

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        B = ctx.config.apollo_blocks_per_domain
        self.domains = [Domain(d, n, c, r, B, B) for d, n, c, r in (
            ("KD-01", "Mathematics and Physics", "SCIENCE", 4_200_000),
            ("KD-02", "Biology and Medicine", "SCIENCE", 8_100_000),
            ("KD-03", "Chemistry and Materials", "SCIENCE", 3_700_000),
            ("KD-04", "Astronomy and Cosmology", "SCIENCE", 1_900_000),
            ("KD-05", "Engineering and Architecture", "TECHNOLOGY", 6_500_000),
            ("KD-06", "Computing and AI", "TECHNOLOGY", 2_800_000),
            ("KD-07", "Agriculture and Ecology", "SCIENCE", 3_300_000),
            ("KD-08", "World History", "HISTORY", 14_000_000),
            ("KD-09", "Philosophy and Ethics", "PHILOSOPHY", 2_100_000),
            ("KD-10", "Literature", "ARTS", 9_800_000),
            ("KD-11", "Music and Performing Arts", "ARTS", 6_400_000),
            ("KD-12", "Visual Arts", "ARTS", 5_200_000),
            ("KD-13", "Languages and Linguistics", "LANGUAGE", 1_800_000),
            ("KD-14", "Economics and Governance", "HISTORY", 3_600_000),
            ("KD-15", "Religion and Mythology", "PHILOSOPHY", 4_700_000))]
        self.modules = [Module(*m) for m in (
            ("EM-01", "Survival and foraging", "SCIENCE", "CHILD", 120), ("EM-02", "Fire, shelter, water", "TECHNOLOGY", "CHILD", 80),
            ("EM-03", "Arithmetic and geometry", "SCIENCE", "CHILD", 200), ("EM-04", "Language and literacy", "LANGUAGE", "CHILD", 150),
            ("EM-05", "Ecology and animal behaviour", "SCIENCE", "TEEN", 180), ("EM-06", "Agriculture", "SCIENCE", "TEEN", 240),
            ("EM-07", "History of civilisation", "HISTORY", "TEEN", 300), ("EM-08", "Ethics and community", "PHILOSOPHY", "TEEN", 160),
            ("EM-09", "Medicine", "SCIENCE", "ADULT", 400), ("EM-10", "Metallurgy and tools", "TECHNOLOGY", "ADULT", 360),
            ("EM-11", "Writing and record keeping", "LANGUAGE", "ADULT", 280), ("EM-12", "Governance", "HISTORY", "ADULT", 220),
            ("EM-13", "Music and art", "ARTS", "TEEN", 150), ("EM-14", "The night sky", "SCIENCE", "TEEN", 90))]
        self.sabotaged = False
        self.perpetrator: Optional[str] = None
        self.students = 0
        self.completion: Dict[str, float] = {}
        self._last_scrub = 0.0

    # -- archive integrity ------------------------------------------------
    def scrub(self) -> None:
        dt = self.year - self._last_scrub
        self._last_scrub = self.year
        for d in self.domains:
            d.integrity *= math.exp(-BIT_ROT_PER_YR * dt)
            if d.blocks_ok > 0:          # redundancy intact -> repair to full integrity
                d.integrity = 1.0

    def sabotage(self, perpetrator: str = "Ted Faro") -> float:
        """Primary copies erased; each erasure-coded shard survives with probability p."""
        cfg = self.cfg
        n, k, p = cfg.apollo_shards_n, cfg.apollo_shards_k, cfg.apollo_shard_survival_p
        self.sabotaged, self.perpetrator = True, perpetrator
        for d in self.domains:
            d.blocks_ok = sum(1 for _ in range(d.blocks_total)
                              if sum(self.rng.random() < p for _ in range(n)) >= k)
        frac = self.available_fraction()
        self.log.warning("OMEGA-level purge by %s: %.1f %% of records survive (theory %.1f %%)",
                         perpetrator, 100 * frac, 100 * p_recover(n, k, p))
        self.set_status(SystemStatus.CORRUPTED, f"archive purged by {perpetrator}")
        self.emit("ARCHIVE_DELETED", Priority.CRITICAL, perpetrator=perpetrator,
                  recovered=round(frac, 4), theory=round(p_recover(n, k, p), 4))
        return frac

    def available_fraction(self, category: Optional[str] = None) -> float:
        ds = [d for d in self.domains if category is None or d.category == category]
        tot = sum(d.records for d in ds)
        return sum(d.records * d.available for d in ds) / tot if tot else 0.0

    # -- education --------------------------------------------------------
    def educate(self, students: int) -> None:
        self.students = students
        self.completion = {m.module_id: BASE_COMPLETION * self.available_fraction(m.category) for m in self.modules}
        self.emit("EDUCATION_DELIVERED", students=students,
                  mean_completion=round(sum(self.completion.values()) / len(self.completion), 3))

    def step(self, dt: float, env) -> None:
        self.scrub()
        self.educate(int(env.get("students", 0)))

    def health(self) -> float:
        return self.available_fraction()

    def _succeeded(self, h: float) -> bool:
        if self.sabotaged:
            self.set_status(SystemStatus.DEGRADED if h > 0.1 else SystemStatus.CRITICAL,
                            f"{100 * h:.0f} % of archive reconstructed")
        return h >= 0.5

    def _report(self):
        mean_c = sum(self.completion.values()) / len(self.completion) if self.completion else 0.0
        return {"available_fraction": round(self.available_fraction(), 4), "sabotaged": self.sabotaged,
                "perpetrator": self.perpetrator, "students": self.students,
                "mean_module_completion": round(mean_c, 3),
                "lost_domains": [d.name for d in self.domains if d.available < 0.1]}
