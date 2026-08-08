"""Terminology binding layer.

THE MOST IMPORTANT MODULE IN THIS REPO. Read the note below before the code.

Every portfolio project that touches clinical data hardcodes a SNOMED code and
moves on. That is exactly the failure mode that produces unusable clinical data
in production. Real terminology binding has three properties this module models:

1. **Provenance.** Every binding records WHERE the code came from and WHO
   asserted it. A code without provenance is a guess wearing a lab coat.

2. **Verification status.** A binding is `VERIFIED` only if it has been checked
   against an authoritative terminology server (a licensed SNOMED CT release, or
   the LOINC distribution). Everything else is `PROVISIONAL` and must be treated
   as untrusted by downstream consumers. This repo ships mostly PROVISIONAL
   procedure bindings on purpose: I do not have a SNOMED CT affiliate licence,
   and pretending otherwise would be the single most dishonest thing I could put
   in a public repo.

3. **Fail-loud on miss.** An unmapped local code does NOT silently become a
   text-only CodeableConcept in production. It raises, gets counted, and shows
   up in the data quality report. Silent degradation is how a claim gets denied
   or a registry submission gets rejected six months later.

SNOMED CT is licensed content (IHTSDO/SNOMED International; free in UK/US member
territories via UMLS licence). LOINC is freely available under the LOINC licence.
Codes below are reproduced as identifiers only for interoperability
demonstration, which is the normal use of a code system in an implementation.

Trust-status primitives (BindingStatus, Binding, UnmappedConceptError) are
defined in trust_status.py and re-exported here so existing callers are
unaffected by the split.
"""

from __future__ import annotations

# Re-exported so callers that `from . import terminology as tx` and use
# tx.BindingStatus / tx.Binding / tx.UnmappedConceptError continue to work
# without any change.
from .trust_status import Binding, BindingStatus, UnmappedConceptError

__all__ = [
    "BindingStatus",
    "Binding",
    "UnmappedConceptError",
    "SNOMED_SYSTEM",
    "LOINC_SYSTEM",
    "UCUM_SYSTEM",
    "procedure_binding",
    "observation_binding",
    "ucum_unit",
    "administrative_gender",
    "procedure_status",
    "all_bindings",
    "coverage_report",
]

SNOMED_SYSTEM = "http://snomed.info/sct"
LOINC_SYSTEM = "http://loinc.org"
UCUM_SYSTEM = "http://unitsofmeasure.org"


# --------------------------------------------------------------------------
# Procedure bindings: local vendor code -> SNOMED CT
#
# All PROVISIONAL. These SNOMED concept IDs are plausible and drawn from the
# procedure hierarchy, but I have not validated them against a licensed SNOMED
# CT release, and several are almost certainly wrong at the granularity a
# surgical registry would demand (e.g. robotic-assisted approach is frequently a
# separate qualifier or a post-coordinated expression rather than a pre-
# coordinated concept). In a real deployment these bindings would be authored by
# a clinical informaticist against a terminology server (Ontoserver / Snowstorm)
# and reviewed, not written by the pipeline engineer.
# --------------------------------------------------------------------------
_PROCEDURE_BINDINGS: dict[str, Binding] = {
    b.local_code: b
    for b in [
        Binding(
            local_code="RAS-CHOL-01",
            system=SNOMED_SYSTEM,
            code="45595009",
            display="Cholecystectomy",
            status=BindingStatus.PROVISIONAL,
            source="developer-asserted",
            note=(
                "Parent concept only. Loses BOTH the minimally-invasive approach "
                "AND the robotic assistance. A surgical registry would reject "
                "this granularity."
            ),
        ),
        Binding(
            local_code="RAS-APPY-01",
            system=SNOMED_SYSTEM,
            code="80146002",
            display="Appendectomy",
            status=BindingStatus.PROVISIONAL,
            source="developer-asserted",
            note="Same approach-loss problem as RAS-CHOL-01.",
        ),
        Binding(
            local_code="RAS-HERN-01",
            system=SNOMED_SYSTEM,
            code="86711006",
            display="Repair of inguinal hernia",
            status=BindingStatus.PROVISIONAL,
            source="developer-asserted",
            note="Laterality is NOT in the code; it must ride on Procedure.bodySite.",
        ),
        Binding(
            local_code="RAS-PROST-01",
            system=SNOMED_SYSTEM,
            code="26294005",
            display="Radical prostatectomy",
            status=BindingStatus.PROVISIONAL,
            source="developer-asserted",
            note="High-volume RAS procedure; granularity needs informaticist review.",
        ),
        Binding(
            local_code="RAS-HYST-01",
            system=SNOMED_SYSTEM,
            code="236886002",
            display="Hysterectomy",
            status=BindingStatus.PROVISIONAL,
            source="developer-asserted",
            note="Parent concept; total vs subtotal distinction is lost.",
        ),
    ]
}


# --------------------------------------------------------------------------
# Physiologic metric bindings: local metric -> LOINC
#
# These are VERIFIED. LOINC vital-sign codes are stable, freely published, and
# these five are the canonical set used by the US Core Vital Signs profile, so I
# am willing to stand behind them. Note the contrast with the procedure table
# above: the difference in status is the honest signal, not a formality.
# --------------------------------------------------------------------------
_OBSERVATION_BINDINGS: dict[str, Binding] = {
    b.local_code: b
    for b in [
        Binding(
            local_code="hr_bpm",
            system=LOINC_SYSTEM,
            code="8867-4",
            display="Heart rate",
            status=BindingStatus.VERIFIED,
            source="LOINC / US Core Vital Signs",
        ),
        Binding(
            local_code="sbp_mmhg",
            system=LOINC_SYSTEM,
            code="8480-6",
            display="Systolic blood pressure",
            status=BindingStatus.VERIFIED,
            source="LOINC / US Core Vital Signs",
            note="In US Core this is a component of the 85354-9 BP panel, not standalone.",
        ),
        Binding(
            local_code="dbp_mmhg",
            system=LOINC_SYSTEM,
            code="8462-4",
            display="Diastolic blood pressure",
            status=BindingStatus.VERIFIED,
            source="LOINC / US Core Vital Signs",
        ),
        Binding(
            local_code="spo2_pct",
            system=LOINC_SYSTEM,
            code="59408-5",
            display="Oxygen saturation in Arterial blood by Pulse oximetry",
            status=BindingStatus.VERIFIED,
            source="LOINC / US Core Vital Signs",
        ),
        Binding(
            local_code="temp_c",
            system=LOINC_SYSTEM,
            code="8310-5",
            display="Body temperature",
            status=BindingStatus.VERIFIED,
            source="LOINC / US Core Vital Signs",
        ),
        Binding(
            local_code="ebl_ml",
            system=LOINC_SYSTEM,
            code="LOCAL-EBL",
            display="Estimated blood loss",
            status=BindingStatus.PROVISIONAL,
            source="developer-asserted",
            note=(
                "PLACEHOLDER. I could not confirm a LOINC code for estimated "
                "blood loss and refuse to invent one. Emitted as a local code "
                "so it fails a US Core validator loudly rather than passing "
                "with a fabricated LOINC."
            ),
        ),
    ]
}


# UCUM units. Getting these wrong is a classic silent-corruption bug: a value of
# 37 is a fever or a hypothermic disaster depending on whether you said Cel or
# [degF], and nothing in the JSON tells you which.
_UNIT_TO_UCUM: dict[str, str] = {
    "bpm": "/min",
    "mmHg": "mm[Hg]",
    "%": "%",
    "C": "Cel",
    "mL": "mL",
}

# Local gender vocabulary -> FHIR administrative-gender.
# "U" is genuinely ambiguous: it could mean unknown OR other. Mapping it to
# "unknown" is a lossy assumption I am flagging rather than hiding.
_GENDER_MAP: dict[str, str] = {"M": "male", "F": "female", "U": "unknown"}

# Local outcome vocabulary -> FHIR Procedure.status (required binding).
# "converted_open" is the interesting one: FHIR has no status for "the procedure
# succeeded but the approach changed mid-case". It collapses to "completed" and
# the clinically important fact has to survive elsewhere (Procedure.outcome).
_OUTCOME_TO_STATUS: dict[str, str] = {
    "completed": "completed",
    "converted_open": "completed",
    "aborted": "stopped",
}


def procedure_binding(local_code: str) -> Binding:
    try:
        return _PROCEDURE_BINDINGS[local_code]
    except KeyError:
        raise UnmappedConceptError(local_code, "procedure") from None


def observation_binding(local_metric: str) -> Binding:
    try:
        return _OBSERVATION_BINDINGS[local_metric]
    except KeyError:
        raise UnmappedConceptError(local_metric, "observation") from None


def ucum_unit(local_unit: str) -> str:
    try:
        return _UNIT_TO_UCUM[local_unit]
    except KeyError:
        raise UnmappedConceptError(local_unit, "unit") from None


def administrative_gender(local: str | None) -> str:
    if local is None:
        return "unknown"
    return _GENDER_MAP.get(local, "unknown")


def procedure_status(local_outcome: str) -> str:
    try:
        return _OUTCOME_TO_STATUS[local_outcome]
    except KeyError:
        raise UnmappedConceptError(local_outcome, "procedure-status") from None


def all_bindings() -> list[Binding]:
    return list(_PROCEDURE_BINDINGS.values()) + list(_OBSERVATION_BINDINGS.values())


def coverage_report() -> dict[str, int]:
    """Governance metric: what fraction of the concept map is actually trusted?"""
    bindings = all_bindings()
    return {
        "total": len(bindings),
        "verified": sum(1 for b in bindings if b.status is BindingStatus.VERIFIED),
        "provisional": sum(
            1 for b in bindings if b.status is BindingStatus.PROVISIONAL
        ),
    }
