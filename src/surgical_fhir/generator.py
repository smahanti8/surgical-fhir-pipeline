"""Synthetic RAS case generator.

Generates surgical cases in the SOURCE schema, not in FHIR. The generator
deliberately injects the data-quality defects that make real interoperability
work hard, because a pipeline that only handles clean input has proven nothing:

  - missing surgeon NPI (~15% of cases)
  - missing laterality on a laterality-relevant procedure (~20%)
  - open cases with no end_time (~5%)
  - an unmapped local procedure code (~8%) -> exercises the fail-loud path
  - physio dropouts (gaps in the sample stream)
  - out-of-physiologic-range sensor artefacts

No real patient data. No PHI. Ever.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from .source_schema import (
    DeviceRecord,
    PhysioSample,
    SurgicalCase,
    TelemetryEvent,
)

_PROCEDURES = [
    ("RAS-CHOL-01", "Robotic-assisted laparoscopic cholecystectomy", False),
    ("RAS-APPY-01", "Robotic-assisted laparoscopic appendectomy", False),
    ("RAS-HERN-01", "Robotic-assisted inguinal hernia repair", True),
    ("RAS-PROST-01", "Robotic-assisted radical prostatectomy", False),
    ("RAS-HYST-01", "Robotic-assisted total hysterectomy", False),
]

# Intentionally NOT in the concept map. Simulates the real situation where the
# device team ships a new procedure profile and nobody tells the informatics
# team. This is the single most common interop failure in the field.
_UNMAPPED_PROCEDURE = ("RAS-COLEC-99", "Robotic-assisted low anterior resection", False)

_INSTRUMENTS = [
    ("Monopolar Curved Scissors", "MCS-420"),
    ("Fenestrated Bipolar Forceps", "FBF-310"),
    ("Large Needle Driver", "LND-205"),
    ("Cadiere Forceps", "CDF-118"),
]

_SURGEONS = [
    ("Dr. A. Rivera", "1234567893"),
    ("Dr. M. Okafor", "1245319599"),
    ("Dr. S. Lindqvist", None),  # fellow, no NPI on file
    ("Dr. J. Nakamura", "1467560003"),
]

_METRICS = [
    ("hr_bpm", "bpm", 55, 95),
    ("sbp_mmhg", "mmHg", 95, 140),
    ("dbp_mmhg", "mmHg", 55, 90),
    ("spo2_pct", "%", 95, 100),
    ("temp_c", "C", 35.8, 37.2),
]


def _devices(rng: random.Random, case_id: str) -> list[DeviceRecord]:
    system = DeviceRecord(
        device_id=f"{case_id}-SYS",
        kind="robotic_system",
        model="RAS-Platform-X1",
        manufacturer="Synthetic Surgical Systems",
        udi_di=f"0{rng.randint(10**12, 10**13 - 1)}",
        serial=f"SN-{rng.randint(100000, 999999)}",
        software_version=rng.choice(["4.2.1", "4.3.0", "5.0.0-rc2"]),
    )
    out = [system]
    for name, model in rng.sample(_INSTRUMENTS, k=rng.randint(2, 4)):
        out.append(
            DeviceRecord(
                device_id=f"{case_id}-{model}",
                kind="instrument",
                model=model,
                manufacturer="Synthetic Surgical Systems",
                udi_di=f"0{rng.randint(10**12, 10**13 - 1)}",
                serial=f"IN-{rng.randint(100000, 999999)}",
            )
        )
    return out


def _events(
    rng: random.Random, case_id: str, start: datetime, end: datetime,
    devices: list[DeviceRecord],
) -> list[TelemetryEvent]:
    instruments = [d for d in devices if d.kind == "instrument"]
    events: list[TelemetryEvent] = []
    t = start
    n = 0
    while t < end:
        n += 1
        etype = rng.choices(
            ["instrument_exchange", "energy_activation", "camera_move", "arm_collision"],
            weights=[0.25, 0.45, 0.25, 0.05],
        )[0]
        dev = rng.choice(instruments) if instruments else None
        payload: dict[str, object] = {}
        if etype == "energy_activation":
            payload = {"duration_ms": rng.randint(200, 4000), "mode": rng.choice(["cut", "coag"])}
        elif etype == "instrument_exchange":
            payload = {"to_model": dev.model if dev else None}
        events.append(
            TelemetryEvent(
                event_id=f"{case_id}-EV{n:04d}",
                case_id=case_id,
                timestamp=t,
                event_type=etype,
                arm=rng.choice(["arm1", "arm2", "arm3", "arm4"]),
                device_id=dev.device_id if dev else None,
                payload=payload,
            )
        )
        t += timedelta(seconds=rng.randint(20, 180))
    return events


def _physio(
    rng: random.Random, case_id: str, start: datetime, end: datetime
) -> list[PhysioSample]:
    samples: list[PhysioSample] = []
    n = 0
    for metric, unit, lo, hi in _METRICS:
        t = start
        while t < end:
            # 3% dropout: the monitor stopped reporting. Real streams do this.
            if rng.random() > 0.03:
                n += 1
                value = round(rng.uniform(lo, hi), 1)
                # 1% sensor artefact: physiologically impossible value that the
                # pipeline must NOT silently pass through to a clinical record.
                if rng.random() < 0.01:
                    value = round(value * rng.choice([0.05, 12.0]), 1)
                samples.append(
                    PhysioSample(
                        sample_id=f"{case_id}-PS{n:05d}",
                        case_id=case_id,
                        timestamp=t,
                        metric=metric,
                        value=value,
                        unit=unit,
                    )
                )
            t += timedelta(minutes=5)
    return samples


def generate_case(rng: random.Random, index: int) -> SurgicalCase:
    case_id = f"CASE-{index:05d}"

    if rng.random() < 0.08:
        local_code, name, lateral = _UNMAPPED_PROCEDURE
    else:
        local_code, name, lateral = rng.choice(_PROCEDURES)

    start = datetime(2026, 1, 6, tzinfo=timezone.utc) + timedelta(
        days=rng.randint(0, 120), hours=rng.randint(7, 16), minutes=rng.choice([0, 15, 30, 45])
    )
    duration = timedelta(minutes=rng.randint(45, 260))
    end: datetime | None = start + duration
    if rng.random() < 0.05:
        end = None  # open case / aborted telemetry session

    surgeon, npi = rng.choice(_SURGEONS)
    if rng.random() < 0.15:
        npi = None

    laterality = None
    if lateral and rng.random() > 0.20:
        laterality = rng.choice(["left", "right"])

    devices = _devices(rng, case_id)
    window_end = end or (start + timedelta(minutes=30))

    return SurgicalCase(
        case_id=case_id,
        patient_mrn=f"MRN{rng.randint(1000000, 9999999)}",
        patient_birth_date=(
            None
            if rng.random() < 0.03
            else f"{rng.randint(1945, 2005)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        ),
        patient_gender=rng.choices(["M", "F", "U"], weights=[0.47, 0.47, 0.06])[0],
        procedure_name=name,
        procedure_local_code=local_code,
        laterality=laterality,
        start_time=start,
        end_time=end,
        or_room=f"OR-{rng.randint(1, 12)}",
        surgeon_name=surgeon,
        surgeon_npi=npi,
        outcome=rng.choices(
            ["completed", "converted_open", "aborted"], weights=[0.90, 0.07, 0.03]
        )[0],
        estimated_blood_loss_ml=(
            None if rng.random() < 0.10 else float(rng.randint(5, 400))
        ),
        devices=devices,
        events=_events(rng, case_id, start, window_end, devices),
        physio=_physio(rng, case_id, start, window_end),
    )


def generate_cases(n: int = 25, seed: int = 42) -> list[SurgicalCase]:
    """Deterministic by default. Reproducibility is a regulated-domain habit."""
    rng = random.Random(seed)
    return [generate_case(rng, i + 1) for i in range(n)]
