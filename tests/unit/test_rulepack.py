"""Tests for engine versioning, rule pack loading, and golden-file reproducibility (Part 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.models import (
    Call,
    Classification,
    CoupleInput,
    Inheritance,
    Zygosity,
)
from engine.rulepack import (
    RulePackError,
    RulePackValidationError,
    load_rulepack,
)


def test_approved_pack_without_reviewer_rejected(tmp_path: Path) -> None:
    """An approved rule pack must refuse to load if reviewed_by is missing or empty."""
    pack_dir = tmp_path / "content" / "rulepacks" / "0.2.0"
    pack_dir.mkdir(parents=True)

    manifest_content = """
version: "0.2.0"
engine_version: "0.1.0"
status: "approved"
reviewed_by: ""
reviewed_on: "2026-11-20"
"""
    panel_content = """
version: "0.2.0"
genes:
  - symbol: "CFTR"
    disease: "Cystic Fibrosis"
    inheritance: "autosomal_recessive"
    minimum_review_status: "practice_guideline"
    validity: "Definitive"
"""
    (pack_dir / "manifest.yaml").write_text(manifest_content, encoding="utf-8")
    (pack_dir / "panel.yaml").write_text(panel_content, encoding="utf-8")

    with pytest.raises(RulePackValidationError, match="(?i)approved.*reviewer"):
        load_rulepack("0.2.0", content_root=str(tmp_path / "content"), demo_mode=False)


def test_draft_pack_refused_outside_demo_mode(tmp_path: Path) -> None:
    """In non-demo mode (DEMO_MODE=0), loader must refuse draft rule packs."""
    pack_dir = tmp_path / "content" / "rulepacks" / "0.3.0-draft"
    pack_dir.mkdir(parents=True)

    manifest_content = """
version: "0.3.0-draft"
engine_version: "0.1.0"
status: "draft"
reviewed_by: null
reviewed_on: null
"""
    panel_content = """
version: "0.3.0-draft"
genes:
  - symbol: "HBB"
    disease: "Beta Thalassemia"
    inheritance: "autosomal_recessive"
    minimum_review_status: "criteria_provided_multiple_submitters"
    validity: "Definitive"
"""
    (pack_dir / "manifest.yaml").write_text(manifest_content, encoding="utf-8")
    (pack_dir / "panel.yaml").write_text(panel_content, encoding="utf-8")

    # In production mode (demo_mode=False), draft packs must be refused
    with pytest.raises(RulePackValidationError, match="draft.*refused"):
        load_rulepack("0.3.0-draft", content_root=str(tmp_path / "content"), demo_mode=False)


def test_draft_pack_allowed_in_demo_mode(tmp_path: Path) -> None:
    """In demo mode (DEMO_MODE=1), draft rule packs are permitted."""
    pack_dir = tmp_path / "content" / "rulepacks" / "0.3.0-draft"
    pack_dir.mkdir(parents=True)

    manifest_content = """
version: "0.3.0-draft"
engine_version: "0.1.0"
status: "draft"
reviewed_by: null
reviewed_on: null
"""
    panel_content = """
version: "0.3.0-draft"
genes:
  - symbol: "HBB"
    disease: "Beta Thalassemia"
    inheritance: "autosomal_recessive"
    minimum_review_status: "criteria_provided_multiple_submitters"
    validity: "Definitive"
"""
    (pack_dir / "manifest.yaml").write_text(manifest_content, encoding="utf-8")
    (pack_dir / "panel.yaml").write_text(panel_content, encoding="utf-8")

    pack = load_rulepack("0.3.0-draft", content_root=str(tmp_path / "content"), demo_mode=True)
    assert pack.manifest.status == "draft"
    assert "HBB" in pack.panel_genes


def test_schema_validation_rejects_invalid_manifest(tmp_path: Path) -> None:
    """Manifest failing schema validation (e.g. invalid status enum) is rejected."""
    pack_dir = tmp_path / "content" / "rulepacks" / "0.4.0"
    pack_dir.mkdir(parents=True)

    manifest_content = """
version: "0.4.0"
engine_version: "0.1.0"
status: "unknown_status"
"""
    (pack_dir / "manifest.yaml").write_text(manifest_content, encoding="utf-8")
    (pack_dir / "panel.yaml").write_text("version: '0.4.0'\ngenes: []", encoding="utf-8")

    with pytest.raises(RulePackError):
        load_rulepack("0.4.0", content_root=str(tmp_path / "content"), demo_mode=True)


def test_official_0_1_0_rulepack_loads_successfully() -> None:
    """The bundled 0.1.0 rule pack must load cleanly in production mode."""
    pack = load_rulepack("0.1.0", content_root="content", demo_mode=False)
    assert pack.manifest.version == "0.1.0"
    assert pack.manifest.status == "approved"
    assert pack.manifest.reviewed_by is not None
    assert len(pack.panel_genes) > 0
    assert "CFTR" in pack.panel_genes
    assert "HBB" in pack.panel_genes


def test_golden_file_byte_identical() -> None:
    """Fixed inputs produce byte-identical output for a pinned version (Part 2 golden test)."""
    from engine.project import run_engine

    pack = load_rulepack("0.1.0", content_root="content", demo_mode=False)

    # Pinned inputs: Carrier couple for CFTR, assessed on all panel genes
    partner_a_calls = (
        Call(
            gene="CFTR",
            variant_id="VCV000007105",
            zygosity=Zygosity.heterozygous,
            classification=Classification.pathogenic,
            inheritance=Inheritance.autosomal_recessive,
        ),
    )
    partner_b_calls = (
        Call(
            gene="CFTR",
            variant_id="VCV000053998",
            zygosity=Zygosity.heterozygous,
            classification=Classification.pathogenic,
            inheritance=Inheritance.autosomal_recessive,
        ),
    )

    couple = CoupleInput(
        partner_a=partner_a_calls,
        partner_b=partner_b_calls,
        assessed_a=pack.panel_genes,
        assessed_b=pack.panel_genes,
        panel_genes=pack.panel_genes,
        related=False,
    )

    result = run_engine(couple, ruleset_version=pack.manifest.version)
    result_json = result.model_dump_json(indent=2)

    golden_path = Path("tests/golden/engine_0.1.0.json")
    assert golden_path.exists(), f"Golden file {golden_path} must exist"
    golden_bytes = golden_path.read_text(encoding="utf-8")

    # Byte-identical match against pinned golden output
    assert result_json.strip() == golden_bytes.strip()


def test_changing_rule_without_version_bump_fails() -> None:
    """Changing a rule without version bump fails reproducibility against golden output."""
    from engine.project import run_engine

    pack = load_rulepack("0.1.0", content_root="content", demo_mode=False)

    # Simulate an unauthorized rule change: CFTR omitted from assessed panel genes
    tampered_panel_genes = tuple(g for g in pack.panel_genes if g != "CFTR")

    partner_a_calls = (
        Call(
            gene="CFTR",
            variant_id="VCV000007105",
            zygosity=Zygosity.heterozygous,
            classification=Classification.pathogenic,
            inheritance=Inheritance.autosomal_recessive,
        ),
    )
    partner_b_calls = (
        Call(
            gene="CFTR",
            variant_id="VCV000053998",
            zygosity=Zygosity.heterozygous,
            classification=Classification.pathogenic,
            inheritance=Inheritance.autosomal_recessive,
        ),
    )

    tampered_couple = CoupleInput(
        partner_a=partner_a_calls,
        partner_b=partner_b_calls,
        assessed_a=tampered_panel_genes,
        assessed_b=tampered_panel_genes,
        panel_genes=tampered_panel_genes,
        related=False,
    )

    tampered_result = run_engine(tampered_couple, ruleset_version=pack.manifest.version)
    tampered_json = tampered_result.model_dump_json(indent=2)

    golden_path = Path("tests/golden/engine_0.1.0.json")
    golden_bytes = golden_path.read_text(encoding="utf-8")

    # Output MUST NOT match the golden file for version 0.1.0
    assert tampered_json.strip() != golden_bytes.strip()


def test_result_carries_versions_and_audit_persists_them() -> None:
    """Every result carries engine_version and ruleset_version; audit record persists both."""
    from fastapi.testclient import TestClient

    from audit.chain import GENESIS, make_result_audit_record
    from engine.version import ENGINE_VERSION
    from web.main import create_app

    app = create_app()
    client = TestClient(app)

    from db.memory_store import get_store

    store = get_store()
    couple_id = store.create_test_couple("p1", "p2")
    client.cookies.set("session", store.create_session("p1"))

    res = client.get(f"/api/couples/{couple_id}/result")
    assert res.status_code == 200
    data = res.json()
    assert data["engine_version"] == ENGINE_VERSION
    assert "ruleset_version" in data
    assert data["ruleset_version"] == "0.1.0"

    # Persist in audit chain
    key = b"0123456789abcdef0123456789abcdef"
    audit_rec = make_result_audit_record(
        couple_id=couple_id,
        engine_version=data["engine_version"],
        ruleset_version=data["ruleset_version"],
        state=data["state"],
        prev_mac=GENESIS,
        key=key,
    )
    assert audit_rec.body["engine_version"] == ENGINE_VERSION
    assert audit_rec.body["ruleset_version"] == "0.1.0"
    assert audit_rec.body["action"] == "result_snapshot"
