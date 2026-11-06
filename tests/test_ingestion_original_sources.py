"""Opt-in local originals; source content never enters version control."""

import os
from pathlib import Path

import pytest

from app.source_manifest import load_manifest
from scripts.inspect_sources import inventory


@pytest.fixture
def originals():
    manifest = os.getenv("ORIGINAL_SOURCES_MANIFEST")

    if not manifest:
        pytest.skip("Set ORIGINAL_SOURCES_MANIFEST to verify the supplied local bundle")

    return load_manifest(Path(manifest))


def test_complete_original_sections_and_technical_content(originals):
    sections = {section.section_id: section for section in originals}

    assert len(sections) == 26
    assert "SUMIFS" in sections["GS-02"].content
    assert "COUNTIFS" in sections["GS-02"].content
    assert "IMPORTRANGE" in sections["GS-04"].content
    assert all(code in sections["AUTO-03"].content for code in ("401", "403", "429"))
    assert any("table 1 / row" in section.locator for section in originals)
    assert sections["GS-CLASS-01"].class_scope == "GS-KPI-01"
    assert sections["AUTO-CLASS-01"].class_scope == "AUTO-N8N-01"
    assert all(
        section.class_scope == "COMMON"
        for key, section in sections.items()
        if key.startswith("COMMON-")
    )
    assert all(section.blocks and len(section.source_hash) == 64 for section in originals)


def test_inventory_excludes_guide_and_event_json(originals):
    manifest_path = Path(os.environ["ORIGINAL_SOURCES_MANIFEST"])
    items, manifest = inventory(manifest_path.parent)

    assert len(manifest["sources"]) == 3
    assert sum(item["index"] for item in items) == 3
    assert not any(section.source_name.startswith("00_") for section in originals)
