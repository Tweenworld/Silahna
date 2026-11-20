"""Run reference data synchronization into PostgreSQL."""

from __future__ import annotations

import argparse
from pathlib import Path

import psycopg

from refdata.sync import RefSyncService


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synchronize public reference data into ref schema."
    )
    parser.add_argument("--db-url", required=True, help="PostgreSQL connection URL")
    parser.add_argument(
        "--manifest",
        default="data/reference/sources.yaml",
        help="Path to sources.yaml manifest",
    )
    parser.add_argument(
        "--fixtures-dir",
        default="data/reference/fixtures",
        help="Path to fixture directory",
    )
    parser.add_argument("--version", default="1.0.0", help="Release version string")
    args = parser.parse_args()

    service = RefSyncService(
        manifest_path=Path(args.manifest), fixtures_dir=Path(args.fixtures_dir)
    )
    with psycopg.connect(args.db_url) as conn:
        results = service.sync_all(conn, version=args.version)
        print(f"Sync complete. Loaded: {results}")


if __name__ == "__main__":
    main()
