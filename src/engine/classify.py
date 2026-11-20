"""Couple classification. Pure, deterministic, and symmetric in partner order (FR-E1).

Rules protected here:
- FR-E2 states are only at_risk, no_panel_finding, incomplete. Never a clear or zero-risk state.
- FR-E3 autosomal recessive only. Any other mode with a variant is incomplete.
- FR-E4 a VUS is never pathogenic and never a clear result.
- FR-E5 a homozygous finding is individual-only.
- FR-E6 linked loci merge; the combined figure is a lower bound.
- FR-E7 related couples get an unmodelled extra-panel note and no number.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.merge import carrier_alleles, has_unphased_compound, is_simple_carrier_call
from engine.models import (
    Call,
    Classification,
    CounsellorNote,
    CoupleInput,
    CoupleState,
    IndividualFinding,
    Inheritance,
    Zygosity,
)

_UNCERTAIN = {Classification.vus}
_NON_AR = {
    Inheritance.autosomal_dominant,
    Inheritance.x_linked,
    Inheritance.unknown,
}


@dataclass
class GeneFacts:
    shared_carrier: bool = False
    lower_bound: bool = False
    incomplete: list[CounsellorNote] = field(default_factory=list)
    individual: list[IndividualFinding] = field(default_factory=list)


def _partner_calls(calls: tuple[Call, ...], gene: str) -> tuple[Call, ...]:
    return tuple(call for call in calls if call.gene == gene)


def _facts_for_gene(couple: CoupleInput, gene: str) -> GeneFacts:
    facts = GeneFacts()
    if gene not in couple.assessed_a or gene not in couple.assessed_b:
        facts.incomplete.append(CounsellorNote(code="not_assessed", gene=gene))
        return facts

    for label, calls in (("a", couple.partner_a), ("b", couple.partner_b)):
        for call in _partner_calls(calls, gene):
            if call.classification in _UNCERTAIN:
                facts.incomplete.append(
                    CounsellorNote(code="vus", gene=gene, detail=f"partner_{label}")
                )
            if call.inheritance in _NON_AR and call.classification not in {
                Classification.benign,
                Classification.likely_benign,
            }:
                facts.incomplete.append(
                    CounsellorNote(
                        code="non_autosomal_recessive",
                        gene=gene,
                        detail=call.inheritance.value,
                    )
                )
            if call.zygosity is Zygosity.homozygous and call.classification in {
                Classification.pathogenic,
                Classification.likely_pathogenic,
            }:
                facts.individual.append(
                    IndividualFinding(
                        gene=gene,
                        zygosity=call.zygosity,
                        classification=call.classification,
                    )
                )
                facts.incomplete.append(
                    CounsellorNote(
                        code="homozygous_individual", gene=gene, detail=f"partner_{label}"
                    )
                )
            if call.zygosity is Zygosity.not_assessed:
                facts.incomplete.append(CounsellorNote(code="not_assessed", gene=gene))

    if any(note.code == "vus" for note in facts.incomplete):
        return facts
    if any(note.code == "homozygous_individual" for note in facts.incomplete):
        return facts
    if any(note.code == "non_autosomal_recessive" for note in facts.incomplete):
        return facts
    if has_unphased_compound(couple.partner_a, gene) or has_unphased_compound(
        couple.partner_b, gene
    ):
        facts.incomplete.append(CounsellorNote(code="phase_unknown", gene=gene))
        return facts

    alleles_a, lower_a = carrier_alleles(couple.partner_a, gene)
    alleles_b, lower_b = carrier_alleles(couple.partner_b, gene)
    simple_a = len(alleles_a) == 1 and all(is_simple_carrier_call(call) for call in alleles_a[0])
    simple_b = len(alleles_b) == 1 and all(is_simple_carrier_call(call) for call in alleles_b[0])
    if simple_a and simple_b:
        facts.shared_carrier = True
        facts.lower_bound = lower_a or lower_b
    return facts


def classify_genes(couple: CoupleInput) -> dict[str, GeneFacts]:
    genes = tuple(sorted(set(couple.panel_genes)))
    return {gene: _facts_for_gene(couple, gene) for gene in genes}


def decide_state(facts: dict[str, GeneFacts]) -> CoupleState:
    """One couple state. A known shared carrier is at_risk even if another gene is incomplete.

    Incomplete wins only when there is no clean shared autosomal-recessive carrier pair.
    That avoids calling the panel clear, and it still shows a known 1-in-4 chance.
    """

    if any(item.shared_carrier for item in facts.values()):
        return CoupleState.at_risk
    if any(item.incomplete for item in facts.values()):
        return CoupleState.incomplete
    return CoupleState.no_panel_finding
