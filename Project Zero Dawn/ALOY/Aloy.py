"""
ALOY: event bus and message relay.

Two channels:
  * publish/subscribe events (typed, priority-ordered, re-entrancy safe)
  * point-to-point messages with per-recipient inboxes (read drains)

Dispatch uses a binary heap keyed on (-priority, sequence), so CRITICAL
events jump the queue while equal-priority events stay FIFO. Push and pop
are O(log n). Handlers that publish during dispatch are queued, not
recursed into, so stack depth stays constant.
"""

from __future__ import annotations

import heapq
import itertools
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional

from base import Context, Event, PhaseResult, Priority, Subsystem

Handler = Callable[[Event], None]


@dataclass
class Message:
    sender: str
    recipient: str
    subject: str
    body: str
    year: float
    priority: Priority = Priority.INFO

    def __str__(self) -> str:
        return f"T+{self.year:.1f} [{self.priority.name}] {self.sender} -> {self.recipient}: {self.subject}"


class Aloy(Subsystem):
    NAME = "ALOY"
    ROLE = "event bus and message relay between GAIA and her subordinate functions"

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self._heap: List[tuple] = []
        self._seq = itertools.count()
        self._subs: Dict[str, List[Handler]] = defaultdict(list)
        self._dispatching = False
        self._archive: Deque[Event] = deque(maxlen=ctx.config.archive_maxlen)
        self._inbox: Dict[str, Deque[Message]] = defaultdict(deque)
        self._endpoints: List[str] = []
        self.stats: Counter = Counter()

    def _on_boot(self) -> None:
        self.ctx.bus = self   # install as the global bus before announcing ONLINE

    # -- registry ---------------------------------------------------------
    def register(self, name: str) -> None:
        if name not in self._endpoints:
            self._endpoints.append(name)

    # -- pub/sub ----------------------------------------------------------
    def subscribe(self, kind: str, handler: Handler) -> Callable[[], None]:
        """kind='*' receives everything. Returns an unsubscribe callable."""
        self._subs[kind].append(handler)
        return lambda: self._subs[kind].remove(handler)

    def publish(self, event: Event) -> None:
        heapq.heappush(self._heap, (-int(event.priority), next(self._seq), event))
        if self._dispatching:
            return
        self._dispatching = True
        try:
            while self._heap:
                _, _, ev = heapq.heappop(self._heap)
                self._archive.append(ev)
                self.stats["events"] += 1
                self.stats[f"p{int(ev.priority)}"] += 1
                for handler in (*self._subs.get(ev.kind, ()), *self._subs.get("*", ())):
                    try:
                        handler(ev)
                    except Exception:   # a bad handler must never take the bus down
                        self.stats["handler_errors"] += 1
                        self.log.exception("handler failed on %s", ev.kind)
        finally:
            self._dispatching = False

    # -- messages ---------------------------------------------------------
    def send(self, sender: str, recipient: str, subject: str, body: str = "",
             priority: Priority = Priority.INFO) -> int:
        targets = self._endpoints if recipient == "ALL" else [recipient]
        delivered = 0
        for name in targets:
            if name == sender or name not in self._endpoints:
                if name not in self._endpoints:
                    self.stats["dropped"] += 1
                    self.log.warning("drop: unknown recipient %s", name)
                continue
            self._inbox[name].append(Message(sender, name, subject, body, self.year, priority))
            delivered += 1
        self.stats["messages"] += delivered
        return delivered

    def read(self, recipient: str) -> List[Message]:
        """Drain and return the inbox (fixes the old 'unread is always empty' bug)."""
        box = self._inbox[recipient]
        out = list(box)
        box.clear()
        return out

    def pending(self, recipient: str) -> int:
        return len(self._inbox[recipient])

    # -- queries ----------------------------------------------------------
    def history(self, kind: Optional[str] = None, min_priority: Priority = Priority.INFO) -> List[Event]:
        return [e for e in self._archive
                if (kind is None or e.kind == kind) and e.priority >= min_priority]

    def execute(self, dt, env) -> PhaseResult:
        return PhaseResult(self.NAME, True, self.health(), 0.0, self._report())

    def health(self) -> float:
        return 1.0 - self.stats["handler_errors"] / (self.stats["events"] + 1)

    def _report(self):
        return {"endpoints": len(self._endpoints), "events": self.stats["events"],
                "critical_events": self.stats["p3"], "messages": self.stats["messages"],
                "dropped": self.stats["dropped"], "handler_errors": self.stats["handler_errors"]}
