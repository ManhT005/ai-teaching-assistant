"""Strict local whitelist. Never construct this contract from student uploads."""

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.knowledge.ingestion import (
    KnowledgeChunk,
    SourceMetadata,
    deduplicate_sources,
    parse_document,
    parse_faq_xlsx,
)

PARSER_REVISION = "gos-sections-v2"
ELIGIBLE_REVIEWS = ("provided_sample", "approved_demo")
CLASS_CODES = ("GS-KPI-01", "AUTO-N8N-01")


class ManifestSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metadata: SourceMetadata | None = None
    faq_records: dict[str, SourceMetadata] | None = None

    @model_validator(mode="after")
    def validate_parser(self):
        suffix = Path(self.path).suffix.lower()

        if suffix in {".docx", ".pdf"} and self.metadata is not None and self.faq_records is None:
            return self

        if suffix == ".xlsx" and self.faq_records and self.metadata is None:
            return self

        raise ValueError("Whitelist supports DOCX/PDF metadata or explicit XLSX row metadata only")


class SourceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    notice: str
    sources: list[ManifestSource] = Field(min_length=1)


def load_manifest(path: Path) -> list[KnowledgeChunk]:
    """Verify all inputs before returning sections; preferred representation wins."""
    path = path.resolve()
    manifest = SourceManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    sections = []

    for source in manifest.sources:
        source_path = (path.parent / source.path).resolve()

        if not source_path.is_relative_to(path.parent):
            raise ValueError("Manifest paths must stay inside the manifest directory")

        checksum = hashlib.sha256(source_path.read_bytes()).hexdigest()

        if checksum != source.sha256:
            raise ValueError(f"Source checksum mismatch: {source_path.name}")

        if source.faq_records is not None:
            parsed = parse_faq_xlsx(source_path, source.faq_records)
        else:
            parsed = parse_document(source_path, source.metadata)

        if any(section.source_hash != source.sha256 for section in parsed):
            raise ValueError(f"Source changed during parsing: {source_path.name}")

        sections.extend(parsed)

    return deduplicate_sources(sections)
