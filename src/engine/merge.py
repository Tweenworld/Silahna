"""Merge linked loci into one carrier allele (FR-E6)."""

from __future__ import annotations

from engine.models import Call, Classification, Inheritance, Zygosity

_CARRIER = {Classification.pathogenic, Classification.likely_pathogenic}
_AR = Inheritance.autosomal_recessive


def is_simple_carrier_call(call: Call) -> bool:
    """One heterozygous autosomal-recessive pathogenic allele."""

    return (
        call.classification in _CARRIER
        and call.zygosity is Zygosity.heterozygous
        and call.inheritance is _AR
    )


def carrier_alleles(
    calls: tuple[Call, ...], gene: str
) -> tuple[tuple[tuple[Call, ...], ...], bool]:
    """Return carrier allele groups for one gene and whether the figure is a lower bound.

    Calls that share a linked_locus_id collapse into one allele. A person with two
    unlinked pathogenic calls in the same gene is not treated as a simple carrier.
    """

    pathogenic = [
        call
        for call in calls
        if call.gene == gene and call.classification in _CARRIER and call.inheritance is _AR
    ]
    groups: dict[str, list[Call]] = {}
    for index, call in enumerate(pathogenic):
        key = call.linked_locus_id or f"unlinked:{call.variant_id}:{index}"
        groups.setdefault(key, []).append(call)
    lower_bound = any(len(group) > 1 for group in groups.values())
    alleles = tuple(tuple(group) for group in groups.values())
    return alleles, lower_bound


def has_unphased_compound(calls: tuple[Call, ...], gene: str) -> bool:
    """Two pathogenic calls in one gene without a shared linkage id."""

    pathogenic = [
        call
        for call in calls
        if call.gene == gene
        and call.classification in _CARRIER
        and call.zygosity is Zygosity.heterozygous
        and call.inheritance is _AR
    ]
    if len(pathogenic) < 2:
        return False
    link_ids = {call.linked_locus_id for call in pathogenic}
    return None in link_ids or len(link_ids) > 1
