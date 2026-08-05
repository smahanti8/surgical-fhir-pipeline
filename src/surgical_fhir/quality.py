"""Data quality reporting.

The report is the deliverable, not a byproduct. When you integrate a device
platform with an EHR, the first artefact the receiving organisation asks for is
not the mapper — it is evidence about what fraction of the data is actually
exchangeable and where it degrades. This module produces that evidence.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field

from .mapping import MappingIssue, MappingResult, Severity, map_case
from .source_schema import SurgicalCase
from . import terminology as tx


@dataclass
class QualityReport:
    cases_in: int = 0
    cases_exchangeable: int = 0
    cases_dropped: int = 0
    resources_out: int = 0
    resources_by_type: dict[str, int] = field(default_factory=dict)
    issues_by_severity: dict[str, int] = field(default_factory=dict)
    issues_by_element: dict[str, int] = field(default_factory=dict)
    # ERROR-severity only: the elements that caused cases to be dropped.
    # Kept separate from issues_by_element (which mixes all severities) so a
    # compliance reviewer sees drop causes without WARNING noise in the same bucket.
    error_by_element: dict[str, int] = field(default_factory=dict)
    terminology_coverage: dict[str, int] = field(default_factory=dict)
    sample_issues: list[dict] = field(default_factory=list)

    @property
    def exchangeable_rate(self) -> float:
        return self.cases_exchangeable / self.cases_in if self.cases_in else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["exchangeable_rate"] = round(self.exchangeable_rate, 4)
        return d

    def render(self) -> str:
        lines = [
            "=" * 68,
            "  SURGICAL -> FHIR PIPELINE :: DATA QUALITY REPORT",
            "=" * 68,
            "",
            f"  Cases ingested        : {self.cases_in}",
            f"  Cases exchangeable    : {self.cases_exchangeable} "
            f"({self.exchangeable_rate:.1%})",
            f"  Cases dropped         : {self.cases_dropped}",
            f"  FHIR resources out    : {self.resources_out}",
            "",
            "  Resources by type",
            "  -----------------",
        ]
        for k, v in sorted(self.resources_by_type.items()):
            lines.append(f"    {k:<14} {v:>6}")
        lines += ["", "  Issues by severity", "  ------------------"]
        for k in ("error", "warning", "info"):
            lines.append(f"    {k:<14} {self.issues_by_severity.get(k, 0):>6}")
        lines += ["", "  Drop reasons (ERROR-only)", "  -------------------------"]
        for k, v in sorted(self.error_by_element.items(), key=lambda kv: -kv[1])[:8]:
            lines.append(f"    {k:<42} {v:>6}")
        lines += ["", "  Top degraded elements (all severities)", "  -------------------------------------"]
        for k, v in sorted(
            self.issues_by_element.items(), key=lambda kv: -kv[1]
        )[:8]:
            lines.append(f"    {k:<42} {v:>6}")
        tc = self.terminology_coverage
        lines += [
            "",
            "  Terminology binding trust",
            "  -------------------------",
            f"    verified   : {tc.get('verified', 0)}/{tc.get('total', 0)}",
            f"    provisional: {tc.get('provisional', 0)}/{tc.get('total', 0)}"
            "   <-- NOT clinically safe without terminology-server validation",
            "",
            "=" * 68,
        ]
        return "\n".join(lines)


def build_report(
    cases: list[SurgicalCase], max_samples: int | None = 40
) -> tuple[QualityReport, list]:
    report = QualityReport(cases_in=len(cases))
    all_resources: list = []
    all_issues: list[MappingIssue] = []

    for case in cases:
        result: MappingResult = map_case(case, max_samples=max_samples)
        all_issues.extend(result.issues)
        if not result.resources:
            report.cases_dropped += 1
            continue
        report.cases_exchangeable += 1
        all_resources.extend(result.resources)

    report.resources_out = len(all_resources)
    report.resources_by_type = dict(
        Counter(r.__resource_type__ for r in all_resources)
    )
    report.issues_by_severity = dict(
        Counter(i.severity.value for i in all_issues)
    )
    report.issues_by_element = dict(Counter(i.element for i in all_issues))
    report.error_by_element = dict(
        Counter(i.element for i in all_issues if i.severity is Severity.ERROR)
    )
    report.terminology_coverage = tx.coverage_report()
    report.sample_issues = [
        {
            "case_id": i.case_id,
            "severity": i.severity.value,
            "element": i.element,
            "message": i.message,
        }
        for i in all_issues
        if i.severity is Severity.ERROR
    ][:10]

    return report, all_resources
