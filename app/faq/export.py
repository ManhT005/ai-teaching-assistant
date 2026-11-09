"""Canonical XLSX plus checksum-verified per-row trusted source manifest."""

import hashlib
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from openpyxl import Workbook

from app.knowledge.ingestion import FAQ_COLUMNS, ReviewDecision, SectionMetadata, SourceMetadata
from app.knowledge.source_manifest import ManifestSource, SourceManifest, load_manifest
from app.teacher.contracts import clean_text


def export_faq(output_dir: Path, candidate, revision, class_code: str):
    output_dir.mkdir(parents=True, exist_ok=True)
    record_id = f"FAQ-{candidate.id}"
    doc_id = f"teacher-faq-{candidate.id}"
    section_id = f"FAQ-{candidate.id}"
    artifact = output_dir / f"faq-{candidate.id}-r{revision.revision}.xlsx"
    manifest_path = artifact.with_suffix(".manifest.json")
    metadata = SourceMetadata(
        doc_id=doc_id,
        version=str(revision.revision),
        data_origin="synthetic_demo",
        review_status="approved_demo",
        sections={
            section_id: SectionMetadata(class_scope=class_code, topics=tuple(revision.topics))
        },
        review_decision=ReviewDecision(
            reviewer=f"staff:{revision.reviewer_actor_id}",
            reviewed_at=revision.reviewed_at,
            reason=revision.review_reason,
        ),
    )
    # Explicit text cells preserve reviewed bytes and cannot become formula cells.
    workbook = Workbook()
    workbook.properties.created = datetime(2020, 1, 1)
    workbook.properties.modified = datetime(2020, 1, 1)
    sheet = workbook.active
    sheet.title = "knowledge"
    sheet.append(FAQ_COLUMNS)
    values = [
        record_id,
        class_code,
        clean_text(revision.question),
        clean_text(revision.answer),
        doc_id,
        section_id,
        str(revision.revision),
        "synthetic_demo",
        "approved_demo",
        ",".join(revision.topics),
    ]
    sheet.append(values)
    for cell in sheet[2]:
        cell.data_type = "s"
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    canonical = BytesIO()
    with ZipFile(buffer) as original, ZipFile(canonical, "w", ZIP_DEFLATED) as target:
        for name in sorted(original.namelist()):
            info = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            data = original.read(name)
            if name == "docProps/core.xml":
                data = re.sub(
                    rb"(<dcterms:modified[^>]*>).*?(</dcterms:modified>)",
                    rb"\g<1>2020-01-01T00:00:00Z\2",
                    data,
                )
            target.writestr(info, data)
    temporary = artifact.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_bytes(canonical.getvalue())
    temporary.replace(artifact)
    checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest = SourceManifest(
        notice="Class-pinned independently staff-reviewed DEMO FAQ; not organizational policy approval.",
        sources=[
            ManifestSource(path=artifact.name, sha256=checksum, faq_records={record_id: metadata})
        ],
    )
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    sections = load_manifest(manifest_path)
    return (
        sections,
        artifact,
        manifest_path,
        checksum,
        hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    )
