"""Concrete reference data source adapters."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import psycopg

from refdata.base import BaseRefAdapter, ValidationError
from refdata.models import (
    AlleleFrequencyRecord,
    DiseaseRecord,
    GeneRecord,
    SourceMetadata,
    VariantRecord,
)


class ClinVarAdapter(BaseRefAdapter[VariantRecord]):
    """Adapter for NCBI ClinVar variant summary (GRCh38)."""

    def fetch(self, fixture_or_url: str) -> bytes:
        p = Path(fixture_or_url)
        if not p.exists():
            raise FileNotFoundError(f"ClinVar source not found at: {fixture_or_url}")
        return p.read_bytes()

    def validate(self, raw_content: bytes, metadata: SourceMetadata) -> list[VariantRecord]:
        text = raw_content.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text), delimiter="\t")
        if not reader.fieldnames:
            raise ValidationError("ClinVar file has no header columns")

        missing = set(metadata.expected_columns) - set(reader.fieldnames)
        if missing:
            raise ValidationError(f"ClinVar file missing required columns: {missing}")

        records: list[VariantRecord] = []
        for i, row in enumerate(reader):
            # Check GRCh38 assembly normalization
            assembly = row.get("Assembly", "").strip()
            if assembly != "GRCh38":
                continue

            var_id = row.get("VariationID", "").strip()
            gene = row.get("GeneSymbol", "").strip()
            notation = row.get("Name", "").strip()
            classification = row.get("ClinicalSignificance", "").strip()
            review_status = row.get("ReviewStatus", "").strip()

            if not var_id or not gene:
                raise ValidationError(f"Empty variation_id or gene symbol at row {i}")

            records.append(
                VariantRecord(
                    variation_id=var_id,
                    gene=gene,
                    notation=notation,
                    classification=classification,
                    review_status=review_status,
                )
            )

        if not records:
            raise ValidationError("ClinVar release contains 0 valid GRCh38 rows")

        return records

    def load(self, cur: psycopg.Cursor[Any], records: list[VariantRecord]) -> int:
        for r in records:
            cur.execute(
                """
                INSERT INTO ref.variant 
                    (variation_id, gene, notation, classification, review_status)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (variation_id) DO UPDATE SET
                    classification = EXCLUDED.classification,
                    review_status = EXCLUDED.review_status,
                    notation = EXCLUDED.notation;
                """,
                (r.variation_id, r.gene, r.notation, r.classification, r.review_status),
            )
        return len(records)


class ClinGenAdapter(BaseRefAdapter[GeneRecord]):
    """Adapter for ClinGen Gene-Disease Validity."""

    def fetch(self, fixture_or_url: str) -> bytes:
        p = Path(fixture_or_url)
        if not p.exists():
            raise FileNotFoundError(f"ClinGen source not found at: {fixture_or_url}")
        return p.read_bytes()

    def validate(self, raw_content: bytes, metadata: SourceMetadata) -> list[GeneRecord]:
        text = raw_content.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValidationError("ClinGen file has no header columns")

        missing = set(metadata.expected_columns) - set(reader.fieldnames)
        if missing:
            raise ValidationError(f"ClinGen file missing required columns: {missing}")

        records: list[GeneRecord] = []
        for i, row in enumerate(reader):
            gene = row.get("gene_symbol", "").strip()
            disease = row.get("disease_label", "").strip()
            moi = row.get("moi", "").strip()
            validity = row.get("classification", "").strip()

            if not gene or not disease:
                raise ValidationError(f"ClinGen row {i} has empty gene or disease")

            records.append(
                GeneRecord(
                    symbol=gene,
                    disease=disease,
                    inheritance=moi,
                    validity=validity,
                    minimum_review_status="reviewed_by_expert_panel",
                )
            )

        if not records:
            raise ValidationError("ClinGen release contains 0 valid rows")

        return records

    def load(self, cur: psycopg.Cursor[Any], records: list[GeneRecord]) -> int:
        for r in records:
            cur.execute(
                """
                INSERT INTO ref.gene (symbol, disease, inheritance, validity, minimum_review_status)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (symbol) DO UPDATE SET
                    disease = EXCLUDED.disease,
                    inheritance = EXCLUDED.inheritance,
                    validity = EXCLUDED.validity;
                """,
                (r.symbol, r.disease, r.inheritance, r.validity, r.minimum_review_status),
            )
        return len(records)


class OrphanetAdapter(BaseRefAdapter[DiseaseRecord]):
    """Adapter for Orphanet Rare Diseases."""

    def fetch(self, fixture_or_url: str) -> bytes:
        p = Path(fixture_or_url)
        if not p.exists():
            raise FileNotFoundError(f"Orphanet source not found at: {fixture_or_url}")
        return p.read_bytes()

    def validate(self, raw_content: bytes, metadata: SourceMetadata) -> list[DiseaseRecord]:
        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"Orphanet file invalid JSON: {exc}") from exc

        if not isinstance(data, list):
            raise ValidationError("Orphanet root must be a list of records")

        records: list[DiseaseRecord] = []
        for i, item in enumerate(data):
            for col in metadata.expected_columns:
                if col not in item:
                    raise ValidationError(f"Orphanet item {i} missing key: {col}")

            orpha_code = str(item["orpha_code"]).strip()
            name = str(item["name"]).strip()
            omim = str(item.get("omim", "")).strip() or None

            if not orpha_code or not name:
                raise ValidationError(f"Orphanet item {i} has empty code or name")

            records.append(
                DiseaseRecord(id=orpha_code, name=name, orpha_code=orpha_code, omim_id=omim)
            )

        return records

    def load(self, cur: psycopg.Cursor[Any], records: list[DiseaseRecord]) -> int:
        for r in records:
            cur.execute(
                """
                INSERT INTO ref.disease (id, name, orpha_code, omim_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    omim_id = EXCLUDED.omim_id;
                """,
                (r.id, r.name, r.orpha_code, r.omim_id),
            )
        return len(records)


class GnomadAdapter(BaseRefAdapter[AlleleFrequencyRecord]):
    """Adapter for gnomAD panel gene allele frequencies."""

    def fetch(self, fixture_or_url: str) -> bytes:
        p = Path(fixture_or_url)
        if not p.exists():
            raise FileNotFoundError(f"gnomAD source not found at: {fixture_or_url}")
        return p.read_bytes()

    def validate(self, raw_content: bytes, metadata: SourceMetadata) -> list[AlleleFrequencyRecord]:
        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"gnomAD file invalid JSON: {exc}") from exc

        if not isinstance(data, list):
            raise ValidationError("gnomAD root must be a list of records")

        records: list[AlleleFrequencyRecord] = []
        for i, item in enumerate(data):
            for col in metadata.expected_columns:
                if col not in item:
                    raise ValidationError(f"gnomAD item {i} missing key: {col}")

            var_id = str(item["variant_id"]).strip()
            pop = str(item["population"]).strip()
            af = float(item["allele_frequency"])

            if not var_id or not pop or af < 0.0 or af > 1.0:
                raise ValidationError(f"gnomAD item {i} invalid values (var_id={var_id}, af={af})")

            records.append(AlleleFrequencyRecord(variant_id=var_id, population=pop, af=af))

        return records

    def load(self, cur: psycopg.Cursor[Any], records: list[AlleleFrequencyRecord]) -> int:
        for r in records:
            cur.execute(
                """
                INSERT INTO ref.allele_frequency (variant_id, population, af)
                VALUES (%s, %s, %s)
                ON CONFLICT (variant_id, population) DO UPDATE SET
                    af = EXCLUDED.af;
                """,
                (r.variant_id, r.population, r.af),
            )
        return len(records)
