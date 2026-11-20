"""Value types for the pure couple engine. No I/O."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Zygosity(StrEnum):
    heterozygous = "heterozygous"
    homozygous = "homozygous"
    hemizygous = "hemizygous"
    absent = "absent"
    not_assessed = "not_assessed"


class Classification(StrEnum):
    pathogenic = "pathogenic"
    likely_pathogenic = "likely_pathogenic"
    vus = "vus"
    likely_benign = "likely_benign"
    benign = "benign"
    not_assessed = "not_assessed"


class Inheritance(StrEnum):
    autosomal_recessive = "autosomal_recessive"
    autosomal_dominant = "autosomal_dominant"
    x_linked = "x_linked"
    unknown = "unknown"


class CoupleState(StrEnum):
    at_risk = "at_risk"
    no_panel_finding = "no_panel_finding"
    incomplete = "incomplete"


class Call(BaseModel):
    model_config = ConfigDict(frozen=True)

    gene: str
    variant_id: str
    zygosity: Zygosity
    classification: Classification
    inheritance: Inheritance
    linked_locus_id: str | None = None


class CoupleInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    partner_a: tuple[Call, ...] = ()
    partner_b: tuple[Call, ...] = ()
    assessed_a: tuple[str, ...] = ()
    assessed_b: tuple[str, ...] = ()
    panel_genes: tuple[str, ...] = ()
    related: bool = False


class CoupleFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    gene: str
    lower_bound: bool
    recurrence_n: int
    recurrence_total: int = 100
    recurrence_label: str


class CounsellorNote(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    gene: str | None = None
    detail: str | None = None


class IndividualFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    gene: str
    zygosity: Zygosity
    classification: Classification


class CoupleProjection(BaseModel):
    """What both partners may see after couple_compute. No consanguinity, no zygosity."""

    model_config = ConfigDict(frozen=True)

    state: CoupleState
    findings: tuple[CoupleFinding, ...] = ()
    engine_version: str
    ruleset_version: str
    panel_incomplete: bool = False


class CounsellorProjection(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: CoupleState
    findings: tuple[CoupleFinding, ...] = ()
    notes: tuple[CounsellorNote, ...] = ()
    individual_findings: tuple[IndividualFinding, ...] = ()
    background_risk: str | None = None
    background_risk_number: int | None = Field(default=None)
    engine_version: str
    ruleset_version: str


class EngineResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    couple: CoupleProjection
    counsellor: CounsellorProjection
