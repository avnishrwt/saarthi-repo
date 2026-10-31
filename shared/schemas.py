"""
RAHAT shared data contract (hour-0 file).

Every module imports field names from here. If you need a new field, change it
HERE first and tell the team; never invent a field name inside your own module.
Mirrors PRD section 9 (Message, Incident, Team, Assignment, VulnerableResident,
AuditLog) plus the Extraction produced by the Extractor agent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- enums
class Channel(str, Enum):
    sms = "sms"
    whatsapp = "whatsapp"
    voice = "voice"            # voice note, transcribed to text
    missed_call = "missed_call"
    ivr = "ivr"                # IVR callback answer ("press 1 for rescue")
    web = "web"
    helpline = "helpline"      # helpline / 112 transcript
    volunteer = "volunteer"    # ASHA worker / volunteer reporting for someone
    proactive = "proactive"    # system-generated: registered resident did not answer


class MessageStatus(str, Enum):
    new = "new"          # stored, not processed yet
    merged = "merged"    # attached to an incident
    review = "review"    # needs a human (no text, failed processing, outlier)
    info = "info"        # not a help request (e.g. "kab tak barish rukegi?")


class IncidentStatus(str, Enum):
    open = "open"
    assigned = "assigned"
    resolved = "resolved"
    false = "false"


class TeamType(str, Enum):
    boat = "boat"
    medical = "medical"
    relief = "relief"
    search_rescue = "search_rescue"
    fire = "fire"


class TeamStatus(str, Enum):
    free = "free"
    busy = "busy"
    returning = "returning"


class AssignmentStatus(str, Enum):
    recommended = "recommended"
    approved = "approved"
    en_route = "en_route"
    on_scene = "on_scene"   # added: team tapped "Reached"
    done = "done"
    rejected = "rejected"


class Need(str, Enum):
    rescue = "rescue"
    medical = "medical"
    food = "food"
    water = "water"
    medicine = "medicine"
    shelter = "shelter"
    evacuation = "evacuation"
    welfare_check = "welfare_check"  # proactive check failed / missed call
    information = "information"      # question, not a request for help


class Vulnerable(str, Enum):
    elderly = "elderly"
    child = "child"
    disabled = "disabled"
    pregnant = "pregnant"
    alone = "alone"        # lives / is alone (raises vulnerability)


class HazardType(str, Enum):
    water = "water"
    fire = "fire"
    collapse = "collapse"
    landslide = "landslide"


class MedicalSeverity(str, Enum):
    none = "none"
    minor = "minor"
    serious = "serious"
    life_threatening = "life_threatening"


class Band(str, Enum):
    CRIT = "CRIT"
    HIGH = "HIGH"
    MED = "MED"
    LOW = "LOW"


# --------------------------------------------------------------------------- location
class Location(BaseModel):
    text: Optional[str] = None          # what the sender said ("Rampur gali 3, mandir ke peeche")
    place_id: Optional[str] = None      # id from data/locations.csv when matched
    lat: Optional[float] = None
    lng: Optional[float] = None
    in_area: Optional[bool] = None      # inside the affected area?
    flooded: Optional[bool] = None      # roads blocked -> only boats reach


# --------------------------------------------------------------------------- ingestion
class MessageIn(BaseModel):
    """Body of POST /messages. Phone numbers are hashed server-side and never stored."""
    channel: Channel = Channel.sms
    raw_text: str = ""
    received_at: Optional[datetime] = None
    sender_id: Optional[str] = None      # raw phone / id from the simulator (hashed on arrival)
    sender_hash: Optional[str] = None    # already-hashed id (from an upstream system)
    audio_url: Optional[str] = None
    language: Optional[str] = None
    lat: Optional[float] = None          # volunteer map pin / cell location if known
    lng: Optional[float] = None
    meta: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    id: str
    channel: Channel
    received_at: datetime
    sender_hash: str
    raw_text: str = ""
    audio_url: Optional[str] = None
    language: Optional[str] = None
    incident_id: Optional[str] = None
    status: MessageStatus = MessageStatus.new
    extraction: Optional["Extraction"] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    meta: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- extractor output
class Extraction(BaseModel):
    """Structured fields from one message. Unknown = null. NEVER guessed."""
    location_text: Optional[str] = None
    place_id: Optional[str] = None
    people_count: Optional[int] = None
    people_estimated: bool = False       # "pura parivar" -> estimated, not stated
    vulnerable: list[Vulnerable] = Field(default_factory=list)
    needs: list[Need] = Field(default_factory=list)
    hazard: Optional[HazardType] = None
    hazard_detail: Optional[str] = None  # "water ~5 ft"
    water_depth_ft: Optional[float] = None
    medical: MedicalSeverity = MedicalSeverity.none
    language: Optional[str] = None       # hi / en / hinglish
    is_help_request: bool = True
    extractor: str = "rules"             # "rules" or "llm:<model>"


# --------------------------------------------------------------------------- incidents
class PriorityFactor(BaseModel):
    value: float     # 0..10
    weight: float
    weighted: float
    why: str = ""


class Callback(BaseModel):
    status: str = "none"     # none | pending | confirmed | denied | no_answer
    at: Optional[datetime] = None
    by: Optional[str] = None


class Incident(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    title: str = ""
    location: Location = Field(default_factory=Location)
    people_count: Optional[int] = None
    people_estimated: bool = False
    vulnerable: list[Vulnerable] = Field(default_factory=list)
    needs: list[Need] = Field(default_factory=list)
    hazard: Optional[HazardType] = None
    hazard_detail: Optional[str] = None
    water_depth_ft: Optional[float] = None
    medical: MedicalSeverity = MedicalSeverity.none
    priority_score: float = 0.0
    priority_breakdown: dict[str, PriorityFactor] = Field(default_factory=dict)
    band: Band = Band.LOW
    confidence: float = 0.6
    confidence_reasons: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    action: str = ""                      # cell of the priority x confidence matrix
    required_team_types: list[TeamType] = Field(default_factory=list)
    status: IncidentStatus = IncidentStatus.open
    message_ids: list[str] = Field(default_factory=list)
    sender_hashes: list[str] = Field(default_factory=list)
    callback: Callback = Field(default_factory=Callback)
    followup_sent: bool = False
    followup_question: Optional[str] = None
    source: str = "messages"              # messages | proactive_check | ivr
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- teams
class Team(BaseModel):
    id: str
    name: str
    type: TeamType
    equipment: list[str] = Field(default_factory=list)
    capacity: int = 4                     # people it can carry in one trip
    lat: float
    lng: float
    status: TeamStatus = TeamStatus.free
    last_seen: Optional[datetime] = None
    speed_kmh: Optional[float] = None     # override default speed for the team type


class Assignment(BaseModel):
    id: str
    incident_id: str
    team_id: str
    team_type_slot: TeamType              # which required team type this fills
    cost: float
    cost_breakdown: dict[str, float] = Field(default_factory=dict)
    eta_min: float
    recommended_at: datetime
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    status: AssignmentStatus = AssignmentStatus.recommended
    reason: str = ""


class VulnerableResident(BaseModel):
    id: str
    name: str                             # display name for the demo (fictional list)
    area: str
    place_id: Optional[str] = None
    lat: float
    lng: float
    phone_hash: str
    age: Optional[int] = None
    needs: list[str] = Field(default_factory=list)
    lives_alone: bool = False
    consent_at: datetime
    last_check_at: Optional[datetime] = None
    last_check_result: Optional[str] = None   # calling | ok | need_help | no_answer


class AuditLog(BaseModel):
    id: Optional[int] = None
    actor: str
    action: str
    entity: str
    before: Optional[dict[str, Any]] = None
    after: Optional[dict[str, Any]] = None
    at: datetime = Field(default_factory=now_utc)


# --------------------------------------------------------------------------- request bodies
class ApproveIn(BaseModel):
    action: str = "approve"        # approve | change | reject
    operator: str = "operator"
    team_id: Optional[str] = None  # for "change"


class TeamStatusIn(BaseModel):
    status: str                    # reached | rescued | no_one_here | need_backup | free
    lat: Optional[float] = None
    lng: Optional[float] = None
    client_ts: Optional[datetime] = None   # when the team tapped it (offline queue)


class VerifyIn(BaseModel):
    result: str                    # confirmed | denied | no_answer
    by: str = "callback"


class SurgeIn(BaseModel):
    on: bool
    actor: str = "duty_officer"


Message.model_rebuild()
