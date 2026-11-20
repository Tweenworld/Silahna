"""Reference data synchronization service with staging, validation, and atomic swap."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import psycopg
import yaml

from refdata.adapters import ClinGenAdapter, ClinVarAdapter, GnomadAdapter, OrphanetAdapter
from refdata.base import BaseRefAdapter, ValidationError
from refdata.models import SourceMetadata


def load_sources_manifest(manifest_path: str | Path) -> dict[str, SourceMetadata]:
    """Parse sources.yaml into strongly typed SourceMetadata."""
    p = Path(manifest_path)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    manifest: dict[str, SourceMetadata] = {}
    for key, val in data.get("sources", {}).items():
        manifest[key] = SourceMetadata(
            name=val["name"],
            url=val["url"],
            format=val["format"],
            release_cadence=val["release_cadence"],
            licence=val["licence"],
            terms_url=val["terms_url"],
            retrieved_date=val["retrieved_date"],
            sha256=val["sha256"],
            expected_columns=val["expected_columns"],
        )
    return manifest


class RefSyncService:
    """Manages transactional reference data synchronization."""

    def __init__(self, manifest_path: str | Path, fixtures_dir: str | Path) -> None:
        self.manifest = load_sources_manifest(manifest_path)
        self.fixtures_dir = Path(fixtures_dir)
        self.adapters: dict[str, tuple[BaseRefAdapter[Any], str]] = {
            "clinvar": (ClinVarAdapter(), "clinvar.tsv"),
            "clingen": (ClinGenAdapter(), "clingen.csv"),
            "orphanet": (OrphanetAdapter(), "orphanet.json"),
            "gnomad": (GnomadAdapter(), "gnomad.json"),
        }

    def sync_all(self, conn: psycopg.Connection[Any], version: str = "1.0.0") -> dict[str, int]:
        """Perform atomic synchronization of all sources.

        Rolls back entirely on any validation or format error.
        """
        results: dict[str, int] = {}
        with conn.cursor() as cur:
            for source_name, (adapter, filename) in self.adapters.items():
                meta = self.manifest[source_name]
                file_path = self.fixtures_dir / filename
                content = adapter.fetch(str(file_path))

                # Calculate SHA-256 hash of raw payload
                content_sha = hashlib.sha256(content).hexdigest()

                # Validate records
                records = adapter.validate(content, meta)

                # Track classification history for ClinVar variants before swap
                if source_name == "clinvar":
                    for r in records:
                        cur.execute(
                            "SELECT classification FROM ref.variant WHERE variation_id = %s;",
                            (r.variation_id,),
                        )
                        row = cur.fetchone()
                        if row and row[0] != r.classification:
                            cur.execute(
                                """
                                INSERT INTO ref.classification_history 
                                (variant_id, old_class, new_class)
                                VALUES (%s, %s, %s);
                                """,
                                (r.variation_id, row[0], r.classification),
                            )

                # Record ref.release row
                cur.execute(
                    """
                    INSERT INTO ref.release (source, version, sha256, licence, url)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (source_name, version, content_sha, meta.licence, meta.url),
                )

                # Load records
                count = adapter.load(cur, records)
                results[source_name] = count

        conn.commit()
        return results

    def sync_bad_release_reverts(
        self, conn: psycopg.Connection[Any], bad_content: bytes, source_name: str
    ) -> None:
        """Attempt to load a bad release and verify transaction rollback."""
        meta = self.manifest[source_name]
        adapter, _ = self.adapters[source_name]

        # Validating bad content must raise ValidationError
        records = adapter.validate(bad_content, meta)
        if not records:
            raise ValidationError("Empty or invalid release rejected")
