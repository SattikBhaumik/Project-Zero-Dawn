"""
PROJECT ZERO DAWN: Base Infrastructure
Shared enums, base class, and event system for all GAIA subsystems.
"""

from __future__ import annotations
from enum import Enum, auto
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from GAIA.Gaia import GAIA

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(name)-12s]  %(message)s",
    datefmt="%H:%M:%S",
)

class SystemStatus(Enum):
    DORMANT      = auto()   # not yet booted
    INITIALIZING = auto()   # Boot sequence running
    ACTIVE       = auto()   # usual operation
    DEGRADED     = auto()   # Partial function loss (I hate this!)
    CRITICAL     = auto()   # unseen failure
    OFFLINE      = auto()   # Shut down
    AUTONOMOUS   = auto()   # Operating outside GAIA mandate (HEPHAESTUS)
    CORRUPTED    = auto()   # Data / logic integrity compromised (HADES, APOLLO)

class TerraformingPhase(Enum):
    STANDBY                 = auto()
    FARO_PURGE              = auto()   # MINERVA
    ATMOSPHERIC_RESTORATION = auto()   # AETHER
    OCEANIC_RESTORATION     = auto()   # POSEIDON
    FLORAL_REINTRODUCTION   = auto()   # DEMETER
    FAUNAL_REINTRODUCTION   = auto()   # ARTEMIS
    HUMAN_REINTRODUCTION    = auto()   # ELEUTHIA
    KNOWLEDGE_TRANSFER      = auto()   # APOLLO
    COMPLETE                = auto()


class MachineClass(Enum):
    """HEPHAESTUS-manufactured machine classifications."""
    RECON        = auto()   # Watchers, Striders: scouting and patrol
    GRAZER       = auto()   # Grazers: biomass conversion
    TRANSPORT    = auto()   # Broadheads, Pack carriers
    COMBAT       = auto()   # Sawtooths, Ravagers: defense overrides
    AERIAL       = auto()   # Glinthawks, Stormbirds
    AQUATIC      = auto()   # Snapmaws, Tideripper
    CONSTRUCTION = auto()   # Behemoths: terrain work
    APEX         = auto()   # Thunderjaw, Rockbreaker: apex chassis


# event system
@dataclass
class SubsystemEvent:
    sender:     str
    event_type: str
    payload:    Dict[str, Any] = field(default_factory=dict)
    timestamp:  datetime       = field(default_factory=datetime.now)
    priority:   int            = 1    # 1 = info, 2 = warning, 3 = critical
 
    def __str__(self) -> str:
        ts = self.timestamp.strftime("%H:%M:%S")
        return f"[{ts}] {self.sender} → {self.event_type}  {self.payload}"


# base subsystem
class GAIASubsystem:
    """
    Abstract base for all GAIA subordinate functions.

    Each subsystem:
    --> Has a lifecycle: DORMANT --> INITIALIZING --> ACTIVE --> OFFLINE
    --> Emits typed events upward to GAIA
    --> Exposes execute_primary_function() for the terraforming sequence
    """

    def __init__(self, name: str, description: str, role: str):
        self.name        = name
        self.description = description
        self.role        = role
        self.status      = SystemStatus.DORMANT
        self.uptime_start: Optional[datetime] = None
        self.event_log:   List[SubsystemEvent] = []
        self.message_log: List[str]            = []
        self.gaia_ref:    Optional["GAIA"]     = None   # injected by GAIA
        self._logger     = logging.getLogger(name)

    # Lifecycle
    def boot(self) -> "GAIASubsystem":
        self.status = SystemStatus.INITIALIZING
        self._log("Kernel initializing …")
        self.uptime_start = datetime.now()
        self._on_boot()
        self.status = SystemStatus.ACTIVE
        self._log(f"Online.  Role: {self.role}")
        return self

    def _on_boot(self):
        """Override for subsystem-specific boot logic."""
        pass

    def shutdown(self):
        self._log("Shutdown sequence initiated.")
        self.status = SystemStatus.OFFLINE
        self.uptime_start = None
        self._log("Offline.")

    # Logging & events
    def _log(self, message: str, level: str = "info"):
        entry = f"[{self.name}]  {message}"
        self.message_log.append(entry)
        getattr(self._logger, level)(message)

    def _emit(self, event_type: str, payload: dict = None, priority: int = 1):
        event = SubsystemEvent(
            sender     = self.name,
            event_type = event_type,
            payload    = payload or {},
            priority   = priority,
        )
        self.event_log.append(event)
        if self.gaia_ref and hasattr(self.gaia_ref, "receive_event"):
            self.gaia_ref.receive_event(event)

    # Interface
    def execute_primary_function(self):
        raise NotImplementedError(f"{self.name} must implement execute_primary_function()")

    def status_report(self) -> dict:
        uptime = str(datetime.now() - self.uptime_start) if self.uptime_start else "N/A"
        return {
            "subsystem":   self.name,
            "description": self.description,
            "role":        self.role,
            "status":      self.status.name,
            "uptime":      uptime,
            "events":      len(self.event_log),
            "log_lines":   len(self.message_log),
        }

    def __repr__(self) -> str:
        return f"<{self.name} | {self.status.name}>"
