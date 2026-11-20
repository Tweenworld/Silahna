"""Build the couple view and the counsellor view from classified genes."""

from __future__ import annotations

from engine.classify import classify_genes, decide_state
from engine.models import (
    CounsellorNote,
    CounsellorProjection,
    CoupleFinding,
    CoupleInput,
    CoupleProjection,
    CoupleState,
    EngineResult,
)
from engine.version import ENGINE_VERSION

_RECURRENCE_N = 25
_RECURRENCE_TOTAL = 100
_RECURRENCE_LABEL = "1 in 4"


def run_engine(couple: CoupleInput, *, ruleset_version: str) -> EngineResult:
    facts = classify_genes(couple)
    state = decide_state(facts)
    shared = tuple(
        CoupleFinding(
            gene=gene,
            lower_bound=item.lower_bound,
            recurrence_n=_RECURRENCE_N,
            recurrence_total=_RECURRENCE_TOTAL,
            recurrence_label=_RECURRENCE_LABEL,
        )
        for gene, item in sorted(facts.items())
        if item.shared_carrier
    )
    # Couple view lists a gene only for a shared carrier finding both partners consented to see.
    couple_findings = shared if state is CoupleState.at_risk else ()
    panel_incomplete = any(item.incomplete for item in facts.values())
    notes: list[CounsellorNote] = []
    individual = []
    for item in facts.values():
        notes.extend(item.incomplete)
        individual.extend(item.individual)
    if panel_incomplete and state is CoupleState.at_risk:
        notes.append(CounsellorNote(code="other_genes_incomplete"))
    background: str | None = None
    if couple.related:
        background = "unmodelled"
        notes.append(CounsellorNote(code="related_panel_limit"))
    couple_view = CoupleProjection(
        state=state,
        findings=couple_findings,
        engine_version=ENGINE_VERSION,
        ruleset_version=ruleset_version,
        panel_incomplete=panel_incomplete,
    )
    counsellor_view = CounsellorProjection(
        state=state,
        findings=shared,
        notes=tuple(notes),
        individual_findings=tuple(individual),
        background_risk=background,
        background_risk_number=None,
        engine_version=ENGINE_VERSION,
        ruleset_version=ruleset_version,
    )
    return EngineResult(couple=couple_view, counsellor=counsellor_view)
