import json
from pathlib import Path

import pytest
from docx import Document
from openpyxl import load_workbook
from pypdf import PdfWriter

from app.ingestion import SourceMetadata, parse_document, parse_faq_xlsx
from app.source_manifest import SourceManifest, load_manifest

SAMPLES = Path(__file__).resolve().parents[1] / "data/samples"


def metadata():
    return SourceMetadata(
        doc_id="DOC-GS-01",
        version="TEST-1.0",
        data_origin="provided_sample",
        review_status="provided_sample",
        sections={"GS-01": {"class_scope": "GS-KPI-01"}},
    )


def test_docx_preserves_formula_table_order_and_precise_locators(tmp_path):
    path = tmp_path / "table.docx"
    doc = Document()
    doc.add_paragraph("GS-01 Công thức")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = '=SUMIFS(D2:D;A2:A;">=01/11")'
    table.cell(0, 1).text = "429"
    doc.add_paragraph("Sau bảng")
    doc.save(path)

    (section,) = parse_document(path, metadata())

    assert section.content.index("SUMIFS") < section.content.index("Sau bảng")
    assert "D2:D;A2:A;" in section.content
    assert section.blocks[1].locator == "block 2 / table 1 / row 1"


def test_scanned_pdf_and_missing_expected_section_fail(tmp_path):
    pdf = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(pdf)

    with pytest.raises(ValueError, match="OCR"):
        parse_document(pdf, metadata())

    path = tmp_path / "missing.docx"
    document = Document()
    document.add_paragraph("GS-01 heading")
    document.save(path)
    trusted = metadata().model_copy(
        update={"sections": {**metadata().sections, "GS-02": metadata().sections["GS-01"]}}
    )

    with pytest.raises(ValueError, match="Missing source sections"):
        parse_document(path, trusted)


def test_manifest_rejects_json_path_escape_and_checksum_mismatch(tmp_path):
    source = {"path": "events.json", "sha256": "0" * 64, "metadata": metadata().model_dump()}

    with pytest.raises(ValueError, match="Whitelist"):
        SourceManifest.model_validate({"notice": "test", "sources": [source]})

    source["path"] = "../outside.docx"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"notice": "test", "sources": [source]}))

    with pytest.raises(ValueError, match="inside"):
        load_manifest(manifest)

    source["path"] = "test.docx"
    (tmp_path / source["path"]).write_bytes(b"changed")
    manifest.write_text(json.dumps({"notice": "test", "sources": [source]}))

    with pytest.raises(ValueError, match="checksum"):
        load_manifest(manifest)


def test_xlsx_cannot_promote_pending_or_change_scope(tmp_path):
    path = SAMPLES / "derived/knowledge_demo.xlsx"
    records = parse_faq_xlsx(path)
    trusted = {
        record.record_id: SourceMetadata(
            doc_id=record.doc_id,
            version=record.version,
            data_origin=record.data_origin,
            review_status=record.review_status,
            sections={record.section_id: {"class_scope": record.class_scope}},
        )
        for record in records
    }
    assert len(parse_faq_xlsx(path, trusted)) == 8

    workbook = load_workbook(path)
    sheet = workbook["knowledge"]
    for row in sheet.iter_rows(min_row=2):
        if row[0].value == "DEMO-AUTO-003":
            row[8].value = "approved_demo"
    altered = tmp_path / "altered.xlsx"
    workbook.save(altered)
    workbook.close()

    with pytest.raises(ValueError, match="disagrees"):
        parse_faq_xlsx(altered, trusted)
