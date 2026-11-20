"""Data models for reference data pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SourceMetadata:
    name: str
    url: str
    format: str
    release_cadence: str
    licence: str
    terms_url: str
    retrieved_date: str
    sha256: str
    expected_columns: list[str]


@dataclass(frozen=True)
class ReleaseRecord:
    source: str
    version: str
    sha256: str
    licence: str
    url: str
    retrieved_at: datetime | None = None


@dataclass(frozen=True)
class VariantRecord:
    variation_id: str
    gene: str
    notation: str
    classification: str
    review_status: str


@dataclass(frozen=True)
class GeneRecord:
    symbol: str
    disease: str
    inheritance: str
    validity: str
    minimum_review_status: str


@dataclass(frozen=True)
class DiseaseRecord:
    id: str
    name: str
    orpha_code: str | None = None
    omim_id: str | None = None


@dataclass(frozen=True)
class AlleleFrequencyRecord:
    variant_id: str
    population: str
    af: float
