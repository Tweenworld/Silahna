"""Unit tests for the pure deterministic couple engine (FR-E1 through FR-E7)."""

from __future__ import annotations

from engine.models import (
    Call,
    Classification,
    CoupleInput,
    CoupleState,
    Inheritance,
    Zygosity,
)
from engine.project import run_engine


def test_engine_symmetric_in_partner_order() -> None:
    """FR-E1: Engine must be symmetric regardless of whether partner A or B is flipped."""
    call_a = Call(
        gene="HBB",
        variant_id="VAR1",
        zygosity=Zygosity.heterozygous,
        classification=Classification.pathogenic,
        inheritance=Inheritance.autosomal_recessive,
    )
    call_b = Call(
        gene="HBB",
        variant_id="VAR2",
        zygosity=Zygosity.heterozygous,
        classification=Classification.pathogenic,
        inheritance=Inheritance.autosomal_recessive,
    )

    couple_1 = CoupleInput(
        partner_a=(call_a,),
        partner_b=(call_b,),
        assessed_a=("HBB",),
        assessed_b=("HBB",),
        panel_genes=("HBB",),
    )
    couple_2 = CoupleInput(
        partner_a=(call_b,),
        partner_b=(call_a,),
        assessed_a=("HBB",),
        assessed_b=("HBB",),
        panel_genes=("HBB",),
    )

    res_1 = run_engine(couple_1, ruleset_version="0.1.0")
    res_2 = run_engine(couple_2, ruleset_version="0.1.0")

    assert res_1.couple.state == CoupleState.at_risk
    assert res_2.couple.state == CoupleState.at_risk
    assert res_1.couple.state == res_2.couple.state
    assert len(res_1.couple.findings) == 1
    assert res_1.couple.findings[0].recurrence_label == "1 in 4"
    assert res_1.couple.findings[0].recurrence_n == 25
    assert res_1.couple.findings == res_2.couple.findings


def test_engine_no_panel_finding_never_says_no_risk() -> None:
    """FR-E2: A negative result returns CoupleState.no_panel_finding, never 'no risk'."""
    couple = CoupleInput(
        partner_a=(),
        partner_b=(),
        assessed_a=("HBB", "CFTR"),
        assessed_b=("HBB", "CFTR"),
        panel_genes=("HBB", "CFTR"),
    )
    res = run_engine(couple, ruleset_version="0.1.0")
    assert res.couple.state == CoupleState.no_panel_finding
    assert res.couple.findings == ()


def test_engine_non_autosomal_recessive_routes_to_incomplete() -> None:
    """FR-E3: Non-AR pathogenic variants must route couple state to incomplete."""
    call_x = Call(
        gene="G6PD",
        variant_id="VAR_X",
        zygosity=Zygosity.heterozygous,
        classification=Classification.pathogenic,
        inheritance=Inheritance.x_linked,
    )
    couple = CoupleInput(
        partner_a=(call_x,),
        partner_b=(),
        assessed_a=("G6PD",),
        assessed_b=("G6PD",),
        panel_genes=("G6PD",),
    )
    res = run_engine(couple, ruleset_version="0.1.0")
    assert res.couple.state == CoupleState.incomplete
    assert any(note.code == "non_autosomal_recessive" for note in res.counsellor.notes)


def test_engine_vus_is_never_pathogenic_nor_clear() -> None:
    """FR-E4: A variant of uncertain significance routes to incomplete."""
    call_vus = Call(
        gene="HBB",
        variant_id="VAR_VUS",
        zygosity=Zygosity.heterozygous,
        classification=Classification.vus,
        inheritance=Inheritance.autosomal_recessive,
    )
    couple = CoupleInput(
        partner_a=(call_vus,),
        partner_b=(),
        assessed_a=("HBB",),
        assessed_b=("HBB",),
        panel_genes=("HBB",),
    )
    res = run_engine(couple, ruleset_version="0.1.0")
    assert res.couple.state == CoupleState.incomplete
    assert any(note.code == "vus" for note in res.counsellor.notes)


def test_engine_homozygous_finding_is_individual_only() -> None:
    """FR-E5: Homozygous pathogenic finding is an individual finding, not in couple findings."""
    call_homo = Call(
        gene="HBB",
        variant_id="VAR_HOMO",
        zygosity=Zygosity.homozygous,
        classification=Classification.pathogenic,
        inheritance=Inheritance.autosomal_recessive,
    )
    couple = CoupleInput(
        partner_a=(call_homo,),
        partner_b=(),
        assessed_a=("HBB",),
        assessed_b=("HBB",),
        panel_genes=("HBB",),
    )
    res = run_engine(couple, ruleset_version="0.1.0")
    assert res.couple.state == CoupleState.incomplete
    assert res.couple.findings == ()
    assert len(res.counsellor.individual_findings) == 1
    assert res.counsellor.individual_findings[0].zygosity == Zygosity.homozygous


def test_engine_linked_loci_merge_lower_bound() -> None:
    """FR-E6: Linked loci in cis merge into one allele, marked lower_bound."""
    call_l1 = Call(
        gene="HBB",
        variant_id="VAR_L1",
        zygosity=Zygosity.heterozygous,
        classification=Classification.pathogenic,
        inheritance=Inheritance.autosomal_recessive,
        linked_locus_id="LINK_1",
    )
    call_l2 = Call(
        gene="HBB",
        variant_id="VAR_L2",
        zygosity=Zygosity.heterozygous,
        classification=Classification.pathogenic,
        inheritance=Inheritance.autosomal_recessive,
        linked_locus_id="LINK_1",
    )
    call_b = Call(
        gene="HBB",
        variant_id="VAR_B",
        zygosity=Zygosity.heterozygous,
        classification=Classification.pathogenic,
        inheritance=Inheritance.autosomal_recessive,
    )
    couple = CoupleInput(
        partner_a=(call_l1, call_l2),
        partner_b=(call_b,),
        assessed_a=("HBB",),
        assessed_b=("HBB",),
        panel_genes=("HBB",),
    )
    res = run_engine(couple, ruleset_version="0.1.0")
    assert res.couple.state == CoupleState.at_risk
    assert len(res.couple.findings) == 1
    assert res.couple.findings[0].lower_bound is True


def test_engine_related_couples_report_unmodelled_background_risk() -> None:
    """FR-E7: Related couples report unmodelled background risk with no speculative number."""
    couple = CoupleInput(
        partner_a=(),
        partner_b=(),
        assessed_a=("HBB",),
        assessed_b=("HBB",),
        panel_genes=("HBB",),
        related=True,
    )
    res = run_engine(couple, ruleset_version="0.1.0")
    assert res.counsellor.background_risk == "unmodelled"
    assert res.counsellor.background_risk_number is None
    assert any(note.code == "related_panel_limit" for note in res.counsellor.notes)
