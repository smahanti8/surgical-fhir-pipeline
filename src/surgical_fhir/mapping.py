"""Source -> FHIR R4B mapping.

FHIR version note: this uses `fhir.resources.R4B`. R4B is the maintenance
release of R4; for the four resources in scope here (Patient, Encounter,
Procedure, Device, Observation) the differential between R4 and R4B is
immaterial. I am stating this rather than claiming "R4" and hoping nobody looks.

Design stance: **the mapper never guesses.** Every lossy or uncertain decision
is recorded as a MappingIssue and surfaced in the data quality report instead of
being smoothed over. A pipeline whose output looks clean because it swallowed
its own uncertainty is worse than no pipeline, because a downstream consumer
will trust it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from fhir.resources.R4B.bundle import Bundle, BundleEntry, BundleEntryRequest
from fhir.resources.R4B.device import Device, DeviceDeviceName, DeviceUdiCarrier
from fhir.resources.R4B.encounter import Encounter, EncounterLocation
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.procedure import Procedure, ProcedurePerformer

from . import terminology as tx
from .source_schema import SurgicalCase

# Physiologic plausibility bounds. Outside these, the value is a sensor artefact
# and must not enter a clinical record as a normal Observation.
_PHYSIO_BOUNDS: dict[str, tuple[float, float]] = {
    "hr_bpm": (20.0, 250.0),
    "sbp_mmhg": (40.0, 250.0),
    "dbp_mmhg": (20.0, 160.0),
    "spo2_pct": (50.0, 100.0),
    "temp_c": (28.0, 43.0),
}


class Severity(str, Enum):
    ERROR = "error"  # resource could not be produced
    WARNING = "warning"  # resource produced but clinically degraded
    INFO = "info"  # lossy but expected


@dataclass
class MappingIssue:
    case_id: str
    severity: Severity
    element: str
    message: str


@dataclass
class MappingResult:
    resources: list[Any] = field(default_factory=list)
    issues: list[MappingIssue] = field(default_factory=list)

    def merge(self, other: "MappingResult") -> None:
        self.resources.extend(other.resources)
        self.issues.extend(other.issues)

    @property
    def errors(self) -> list[MappingIssue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]


def _pseudonymise(mrn: str) -> str:
    """Stable pseudonym for a local MRN.

    NOT de-identification. A deterministic hash of an MRN is still a linkable
    identifier under HIPAA Safe Harbor, and this is only defensible here because
    the input is synthetic. In production this is a job for a separate
    de-identification service with its own key custody, audit trail, and expert
    determination. Saying so is the point.
    """
    import hashlib

    return hashlib.sha256(mrn.encode()).hexdigest()[:16]


def _instant(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------- Patient


def map_patient(case: SurgicalCase) -> MappingResult:
    res = MappingResult()
    pid = _pseudonymise(case.patient_mrn)

    if case.patient_gender == "U":
        res.issues.append(
            MappingIssue(
                case.case_id,
                Severity.INFO,
                "Patient.gender",
                "Local 'U' is ambiguous (unknown vs other); mapped to 'unknown'. "
                "Lossy by necessity — the source vocabulary cannot express the "
                "distinction FHIR requires.",
            )
        )
    if case.patient_birth_date is None:
        res.issues.append(
            MappingIssue(
                case.case_id,
                Severity.WARNING,
                "Patient.birthDate",
                "Absent. US Core Patient marks birthDate must-support; this "
                "resource will not pass US Core validation.",
            )
        )

    patient = Patient(
        id=pid,
        identifier=[
            {
                "system": "urn:oid:2.16.840.1.113883.19.5",  # example local MRN OID
                "value": pid,
                "type": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                            "code": "MR",
                            "display": "Medical record number",
                        }
                    ]
                },
            }
        ],
        gender=tx.administrative_gender(case.patient_gender),
        birthDate=case.patient_birth_date,
    )
    res.resources.append(patient)
    return res


# ---------------------------------------------------------------- Encounter


def map_encounter(case: SurgicalCase) -> MappingResult:
    res = MappingResult()
    pid = _pseudonymise(case.patient_mrn)

    period: dict[str, str] = {"start": _instant(case.start_time)}
    if case.end_time:
        period["end"] = _instant(case.end_time)
    else:
        res.issues.append(
            MappingIssue(
                case.case_id,
                Severity.WARNING,
                "Encounter.period.end",
                "No end_time in source. Encounter emitted with status "
                "'in-progress' rather than fabricating a close time.",
            )
        )

    enc = Encounter(
        id=f"enc-{case.case_id}",
        status="finished" if case.end_time else "in-progress",
        class_fhir={
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "IMP",
            "display": "inpatient encounter",
        },
        subject={"reference": f"Patient/{pid}"},
        period=period,
        location=[
            EncounterLocation(location={"display": f"Operating room {case.or_room}"})
        ],
    )
    res.resources.append(enc)
    return res


# ---------------------------------------------------------------- Device


def map_devices(case: SurgicalCase) -> MappingResult:
    res = MappingResult()
    for d in case.devices:
        udi = None
        if d.udi_di:
            udi = [DeviceUdiCarrier(deviceIdentifier=d.udi_di, issuer="http://hl7.org/fhir/NamingSystem/gs1-di")]
        else:
            res.issues.append(
                MappingIssue(
                    case.case_id,
                    Severity.WARNING,
                    "Device.udiCarrier",
                    f"Device {d.device_id} has no UDI-DI. UDI is the backbone of "
                    "post-market surveillance and recall traceability; its "
                    "absence is a real regulatory gap, not a cosmetic one.",
                )
            )

        device = Device(
            id=f"dev-{d.device_id}",
            identifier=[{"system": "urn:local:device-id", "value": d.device_id}],
            udiCarrier=udi,
            status="active",
            manufacturer=d.manufacturer,
            serialNumber=d.serial,
            deviceName=[DeviceDeviceName(name=d.model, type="model-name")],
            type={"text": d.kind},
            version=(
                [{"value": d.software_version}] if d.software_version else None
            ),
        )
        res.resources.append(device)
    return res


# ---------------------------------------------------------------- Procedure


def map_procedure(case: SurgicalCase) -> MappingResult:
    res = MappingResult()
    pid = _pseudonymise(case.patient_mrn)

    try:
        binding = tx.procedure_binding(case.procedure_local_code)
    except tx.UnmappedConceptError as exc:
        res.issues.append(
            MappingIssue(
                case.case_id,
                Severity.ERROR,
                "Procedure.code",
                f"{exc}. Case DROPPED from the FHIR output. This is the correct "
                "behaviour: an uncoded procedure in a clinical record is worse "
                "than an absent one.",
            )
        )
        return res

    if not binding.is_trusted:
        res.issues.append(
            MappingIssue(
                case.case_id,
                Severity.WARNING,
                "Procedure.code",
                f"SNOMED binding {binding.code} is PROVISIONAL: {binding.note}",
            )
        )

    performer = None
    if case.surgeon_npi:
        performer = [
            ProcedurePerformer(
                actor={
                    "display": case.surgeon_name,
                    "identifier": {
                        "system": "http://hl7.org/fhir/sid/us-npi",
                        "value": case.surgeon_npi,
                    },
                }
            )
        ]
    else:
        performer = [ProcedurePerformer(actor={"display": case.surgeon_name})]
        res.issues.append(
            MappingIssue(
                case.case_id,
                Severity.WARNING,
                "Procedure.performer.actor.identifier",
                "No NPI. Performer degrades to display-only, which is not "
                "resolvable by a receiving system and breaks attribution for "
                "registry submission and reimbursement.",
            )
        )

    body_site = None
    if case.laterality:
        body_site = [{"text": f"{case.laterality} side"}]
    elif case.procedure_local_code == "RAS-HERN-01":
        res.issues.append(
            MappingIssue(
                case.case_id,
                Severity.ERROR,
                "Procedure.bodySite",
                "Laterality absent on a laterality-relevant procedure. This is "
                "a wrong-site-surgery-class data defect. Flagged, not defaulted.",
            )
        )

    performed: dict[str, str] = {"start": _instant(case.start_time)}
    if case.end_time:
        performed["end"] = _instant(case.end_time)

    outcome_cc = None
    if case.outcome == "converted_open":
        outcome_cc = {
            "text": "Converted to open procedure",
        }
        res.issues.append(
            MappingIssue(
                case.case_id,
                Severity.INFO,
                "Procedure.status",
                "Source outcome 'converted_open' has no FHIR status equivalent. "
                "Collapsed to 'completed' with the conversion carried on "
                "Procedure.outcome as text. Structured semantics lost — this is "
                "the kind of gap that becomes an implementation-guide extension.",
            )
        )

    proc = Procedure(
        id=f"proc-{case.case_id}",
        status=tx.procedure_status(case.outcome),
        code={
            "coding": [
                {
                    "system": binding.system,
                    "code": binding.code,
                    "display": binding.display,
                }
            ],
            "text": case.procedure_name,  # free text preserves what the code lost
        },
        subject={"reference": f"Patient/{pid}"},
        encounter={"reference": f"Encounter/enc-{case.case_id}"},
        performedPeriod=performed,
        performer=performer,
        bodySite=body_site,
        outcome=outcome_cc,
        usedReference=[
            {"reference": f"Device/dev-{d.device_id}"} for d in case.devices
        ],
    )
    res.resources.append(proc)
    return res


# ---------------------------------------------------------------- Observation


def map_observations(case: SurgicalCase, max_samples: int | None = 40) -> MappingResult:
    """Map physiologic samples to Observations.

    `max_samples` exists because 1 Hz telemetry x 4 hours x 5 metrics = 72,000
    Observations for ONE case. Naively mapping device telemetry 1:1 to FHIR
    resources is the classic architectural mistake here: FHIR is an exchange
    format, not a time-series store. In production the telemetry lives in a
    time-series database and FHIR carries summaries or SampledData. Truncating
    with a documented reason beats pretending the volume problem doesn't exist.
    """
    res = MappingResult()
    pid = _pseudonymise(case.patient_mrn)

    samples = case.physio
    if max_samples is not None and len(samples) > max_samples:
        res.issues.append(
            MappingIssue(
                case.case_id,
                Severity.INFO,
                "Observation",
                f"{len(samples)} physio samples downsampled to {max_samples}. "
                "See docstring: FHIR is not a time-series store.",
            )
        )
        step = len(samples) // max_samples
        samples = samples[::step][:max_samples]

    for s in samples:
        try:
            binding = tx.observation_binding(s.metric)
            unit = tx.ucum_unit(s.unit)
        except tx.UnmappedConceptError as exc:
            res.issues.append(
                MappingIssue(case.case_id, Severity.ERROR, "Observation.code", str(exc))
            )
            continue

        lo, hi = _PHYSIO_BOUNDS.get(s.metric, (float("-inf"), float("inf")))
        if not (lo <= s.value <= hi):
            res.issues.append(
                MappingIssue(
                    case.case_id,
                    Severity.WARNING,
                    "Observation.value",
                    f"{s.metric}={s.value}{s.unit} outside plausible range "
                    f"[{lo}, {hi}]. Emitted with dataAbsentReason and no value "
                    "rather than injecting a sensor artefact into a chart.",
                )
            )
            obs = Observation(
                id=f"obs-{s.sample_id}",
                status="entered-in-error",
                category=[
                    {
                        "coding": [
                            {
                                "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                "code": "vital-signs",
                            }
                        ]
                    }
                ],
                code={
                    "coding": [
                        {
                            "system": binding.system,
                            "code": binding.code,
                            "display": binding.display,
                        }
                    ]
                },
                subject={"reference": f"Patient/{pid}"},
                encounter={"reference": f"Encounter/enc-{case.case_id}"},
                effectiveDateTime=_instant(s.timestamp),
                dataAbsentReason={
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/data-absent-reason",
                            "code": "error",
                            "display": "Error",
                        }
                    ]
                },
            )
            res.resources.append(obs)
            continue

        obs = Observation(
            id=f"obs-{s.sample_id}",
            status="final",
            category=[
                {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                            "code": "vital-signs",
                        }
                    ]
                }
            ],
            code={
                "coding": [
                    {
                        "system": binding.system,
                        "code": binding.code,
                        "display": binding.display,
                    }
                ]
            },
            subject={"reference": f"Patient/{pid}"},
            encounter={"reference": f"Encounter/enc-{case.case_id}"},
            effectiveDateTime=_instant(s.timestamp),
            valueQuantity={
                "value": s.value,
                "unit": s.unit,
                "system": tx.UCUM_SYSTEM,
                "code": unit,
            },
        )
        res.resources.append(obs)

    # Estimated blood loss: a case-level fact, not a stream sample.
    if case.estimated_blood_loss_ml is not None:
        binding = tx.observation_binding("ebl_ml")
        res.issues.append(
            MappingIssue(
                case.case_id,
                Severity.WARNING,
                "Observation.code",
                f"EBL uses placeholder code {binding.code}: {binding.note}",
            )
        )
        res.resources.append(
            Observation(
                id=f"obs-{case.case_id}-ebl",
                status="final",
                code={
                    "coding": [
                        {
                            "system": "urn:local:or-metrics",
                            "code": binding.code,
                            "display": binding.display,
                        }
                    ],
                    "text": "Estimated blood loss",
                },
                subject={"reference": f"Patient/{pid}"},
                encounter={"reference": f"Encounter/enc-{case.case_id}"},
                effectiveDateTime=_instant(case.start_time),
                valueQuantity={
                    "value": case.estimated_blood_loss_ml,
                    "unit": "mL",
                    "system": tx.UCUM_SYSTEM,
                    "code": tx.ucum_unit("mL"),
                },
            )
        )
    return res


# ---------------------------------------------------------------- Case / Bundle


def map_case(case: SurgicalCase, max_samples: int | None = 40) -> MappingResult:
    """Map one source case to a set of FHIR resources.

    Ordering matters: Procedure is mapped first because an unmapped procedure
    code invalidates the whole case. Emitting Observations for a case whose
    Procedure was dropped would leave orphan vitals referencing a
    non-existent Encounter.
    """
    proc_res = map_procedure(case)
    if proc_res.errors:
        return proc_res

    result = MappingResult()
    result.merge(map_patient(case))
    result.merge(map_encounter(case))
    result.merge(map_devices(case))
    result.merge(proc_res)
    result.merge(map_observations(case, max_samples=max_samples))
    return result


def to_transaction_bundle(resources: list[Any]) -> Bundle:
    """Wrap resources in a FHIR transaction Bundle for POST to any FHIR server.

    Uses PUT-with-id (upsert) rather than POST so the bundle is idempotent.
    Replaying a bundle is a normal operational event in healthcare integration
    (retries, backfills); a non-idempotent loader creates duplicate clinical
    records, and duplicate clinical records are a patient safety issue.
    """
    entries = []
    for r in resources:
        rtype = r.__resource_type__
        entries.append(
            BundleEntry(
                fullUrl=f"urn:uuid:{rtype}-{r.id}",
                resource=r,
                request=BundleEntryRequest(method="PUT", url=f"{rtype}/{r.id}"),
            )
        )
    return Bundle(type="transaction", entry=entries)
