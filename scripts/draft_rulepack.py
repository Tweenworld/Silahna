"""Propose a draft rule pack from reference criteria."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import psycopg
import yaml


def generate_draft_rulepack(
    conn: psycopg.Connection[Any],
    output_dir: str | Path,
    version: str = "0.2.0-draft",
    min_validity: str = "Definitive",
) -> Path:
    """Generate draft rule pack files (manifest.yaml and panel.yaml).

    Strict Clinical Rule:
    The output status MUST be 'draft' and cannot be approved without
    clinical review and a designated reviewer name.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, disease, inheritance, validity
            FROM ref.gene
            WHERE inheritance ILIKE '%%autosomal recessive%%'
              AND validity = %s
            ORDER BY symbol;
            """,
            (min_validity,),
        )
        genes = cur.fetchall()

    panel_genes: list[dict[str, object]] = []
    for symbol, disease, _inheritance, validity in genes:
        panel_genes.append(
            {
                "symbol": symbol,
                "disease": disease,
                "inheritance": "autosomal_recessive",
                "minimum_review_status": "reviewed_by_expert_panel",
                "validity": validity,
            }
        )

    panel_doc = {
        "version": version,
        "genes": panel_genes,
    }

    manifest_doc = {
        "version": version,
        "engine_version": "0.1.0",
        "status": "draft",  # Strictly draft!
        "reviewed_by": None,  # Needs clinical reviewer sign-off to become approved
        "reviewed_on": None,
    }

    with (out_path / "panel.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(panel_doc, f, sort_keys=False)

    with (out_path / "manifest.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(manifest_doc, f, sort_keys=False)

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a draft rule pack from ref schema.")
    parser.add_argument("--db-url", required=True, help="PostgreSQL connection URL")
    parser.add_argument("--out-dir", required=True, help="Output directory path")
    parser.add_argument("--version", default="0.2.0-draft", help="Rule pack version")
    args = parser.parse_args()

    with psycopg.connect(args.db_url) as conn:
        path = generate_draft_rulepack(conn, args.out_dir, version=args.version)
        print(f"Draft rule pack created at {path}")


if __name__ == "__main__":
    main()
