"""Rule pack loading, schema validation, and clinical sign-off enforcement (Part 2)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from engine.models import Inheritance


class RulePackError(RuntimeError):
    """Base error for rule pack loading and validation."""


class RulePackValidationError(RulePackError):
    """Rule pack failed structural or governance validation."""


@dataclass(frozen=True)
class RulePackManifest:
    version: str
    engine_version: str
    status: str
    reviewed_by: str | None
    reviewed_on: str | None


@dataclass(frozen=True)
class PanelGene:
    symbol: str
    disease: str
    inheritance: Inheritance
    minimum_review_status: str
    validity: str


@dataclass(frozen=True)
class Panel:
    version: str
    genes: tuple[PanelGene, ...]

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(g.symbol for g in self.genes))


@dataclass(frozen=True)
class RulePack:
    manifest: RulePackManifest
    panel: Panel

    @property
    def panel_genes(self) -> tuple[str, ...]:
        return self.panel.symbols


class RulePackLoader:
    """Convenience loader for rule packs configured against a content root."""

    def __init__(self, content_root: str | Path = "content") -> None:
        self.content_root = str(content_root)

    def load_rulepack(self, version: str, *, demo_mode: bool = False) -> RulePack:
        return load_rulepack(version, content_root=self.content_root, demo_mode=demo_mode)


def _load_schema(schema_path: Path) -> dict[str, Any]:
    if not schema_path.is_file():
        raise RulePackError(f"Missing schema file: {schema_path}")
    try:
        content = json.loads(schema_path.read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            raise RulePackError(f"Schema must be a JSON object: {schema_path}")
        return content
    except Exception as exc:
        raise RulePackError(f"Failed to read schema {schema_path}: {exc}") from exc


def load_rulepack(
    version: str,
    *,
    content_root: str = "content",
    demo_mode: bool = False,
) -> RulePack:
    """Load, validate, and check approval for a rule pack version.

    Enforces:
    - JSON Schema validation of manifest.yaml and panel.yaml.
    - An approved rule pack must have a non-empty reviewer name.
    - In non-demo mode (DEMO_MODE=0), draft rule packs are strictly rejected.
    """
    root = Path(content_root)
    pack_dir = root / "rulepacks" / version
    if not pack_dir.is_dir():
        raise RulePackError(f"Rule pack directory not found: {pack_dir}")

    manifest_file = pack_dir / "manifest.yaml"
    panel_file = pack_dir / "panel.yaml"

    if not manifest_file.is_file():
        raise RulePackError(f"Missing manifest.yaml in {pack_dir}")
    if not panel_file.is_file():
        raise RulePackError(f"Missing panel.yaml in {pack_dir}")

    try:
        manifest_raw = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RulePackValidationError(f"Invalid YAML in {manifest_file}: {exc}") from exc

    try:
        panel_raw = yaml.safe_load(panel_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RulePackValidationError(f"Invalid YAML in {panel_file}: {exc}") from exc

    # Validate against JSON Schemas
    schema_dir = pack_dir.parent.parent / "schema"
    if not schema_dir.is_dir():
        schema_dir = Path("content/schema")

    manifest_schema_file = schema_dir / "manifest.schema.json"
    panel_schema_file = schema_dir / "panel.schema.json"

    if manifest_schema_file.is_file():
        manifest_schema = _load_schema(manifest_schema_file)
        try:
            jsonschema.validate(instance=manifest_raw, schema=manifest_schema)
        except jsonschema.ValidationError as exc:
            raise RulePackValidationError(f"manifest.yaml failed schema: {exc.message}") from exc
    else:
        raise RulePackError(f"Missing manifest schema at {manifest_schema_file}")

    if panel_schema_file.is_file():
        panel_schema = _load_schema(panel_schema_file)
        try:
            jsonschema.validate(instance=panel_raw, schema=panel_schema)
        except jsonschema.ValidationError as exc:
            raise RulePackValidationError(f"panel.yaml failed schema: {exc.message}") from exc
    else:
        raise RulePackError(f"Missing panel schema at {panel_schema_file}")

    if not isinstance(manifest_raw, dict):
        raise RulePackValidationError("manifest.yaml must be an object")
    if not isinstance(panel_raw, dict):
        raise RulePackValidationError("panel.yaml must be an object")

    manifest = RulePackManifest(
        version=str(manifest_raw.get("version", "")),
        engine_version=str(manifest_raw.get("engine_version", "")),
        status=str(manifest_raw.get("status", "")),
        reviewed_by=manifest_raw.get("reviewed_by"),
        reviewed_on=str(manifest_raw["reviewed_on"])
        if manifest_raw.get("reviewed_on") is not None
        else None,
    )

    # Governance rule 1: Refuse approved rule packs without a reviewer name
    if manifest.status == "approved":
        if not manifest.reviewed_by or not manifest.reviewed_by.strip():
            raise RulePackValidationError(
                f"Approved rule pack {version} must have a valid reviewer name"
            )

    # Governance rule 2: In non-demo mode, refuse draft packs
    if not demo_mode and manifest.status == "draft":
        raise RulePackValidationError(
            f"Draft rule pack {version} is refused outside demo mode (DEMO_MODE=0)"
        )

    # Parse panel genes
    raw_genes = panel_raw.get("genes", [])
    if not isinstance(raw_genes, list):
        raise RulePackValidationError("panel.yaml 'genes' must be a list")

    parsed_genes: list[PanelGene] = []
    for g in raw_genes:
        if not isinstance(g, dict):
            raise RulePackValidationError("gene definition must be a mapping")
        parsed_genes.append(
            PanelGene(
                symbol=str(g["symbol"]),
                disease=str(g["disease"]),
                inheritance=Inheritance(g["inheritance"]),
                minimum_review_status=str(g["minimum_review_status"]),
                validity=str(g["validity"]),
            )
        )

    panel = Panel(
        version=str(panel_raw.get("version", "")),
        genes=tuple(parsed_genes),
    )

    return RulePack(manifest=manifest, panel=panel)
