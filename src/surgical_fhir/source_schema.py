"""Source-system schema: what a robotic-assisted surgery (RAS) platform actually emits.

This is deliberately NOT FHIR. It is the shape of data that comes off an OR
platform: a case record, a device inventory, a stream of console/instrument
events, and a stream of physiologic samples from the anesthesia machine.

The whole point of this repo is that the gap between THIS and FHIR is where
healthcare interoperability actually lives. Vendor telemetry is optimised for
engineering (high-frequency, denormalised, local vocabularies, nullable
everything). FHIR is optimised for exchange (coded, referenced, validated).

Schema is vendor-neutral and synthetic. No proprietary schema is reproduced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class DeviceRecord:
    """A physical device in the OR. Serials/UDIs are synthetic."""

    device_id: str
    kind: str  # "robotic_system" | "instrument" | "endoscope" | "generator"
    model: str
    manufacturer: str
    udi_di: str | None = None  # Device Identifier portion of UDI
    serial: str | None = None
    software_version: str | None = None


@dataclass
class TelemetryEvent:
    """A discrete event from the console or an instrument arm."""

    event_id: str
    case_id: str
    timestamp: datetime
    event_type: str  # local vocabulary, e.g. "instrument_exchange"
    arm: str | None = None
    device_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class PhysioSample:
    """A physiologic sample from the anesthesia machine / patient monitor."""

    sample_id: str
    case_id: str
    timestamp: datetime
    metric: str  # local vocabulary, e.g. "hr_bpm"
    value: float
    unit: str


@dataclass
class SurgicalCase:
    """One OR case as the source platform records it.

    Note the deliberate warts, all of which are real-world:
      - `procedure_name` is free text plus a *local* vendor code, not SNOMED.
      - `patient_mrn` is a local identifier, not a resolvable Patient reference.
      - `laterality` is optional and frequently absent.
      - `end_time` can be None for a case that aborted or is still open.
      - `surgeon_npi` may be missing for residents/fellows.
    """

    case_id: str
    patient_mrn: str
    patient_birth_date: str | None
    patient_gender: str | None  # local vocabulary: "M" | "F" | "U"
    procedure_name: str
    procedure_local_code: str
    laterality: str | None
    start_time: datetime
    end_time: datetime | None
    or_room: str
    surgeon_name: str
    surgeon_npi: str | None
    outcome: str  # local vocabulary: "completed" | "converted_open" | "aborted"
    estimated_blood_loss_ml: float | None
    devices: list[DeviceRecord] = field(default_factory=list)
    events: list[TelemetryEvent] = field(default_factory=list)
    physio: list[PhysioSample] = field(default_factory=list)
