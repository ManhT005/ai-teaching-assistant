"""Offline parsers for curated demo fixtures. No embeddings, indexing or DB writes.

Metadata is operator-controlled, never taken from a webhook or unreviewed upload.
Index staging, activation and SQL class filtering remain Day 2 work.
"""
from pathlib import Path
import re
from typing import Literal

from docx import Document as DocxDocument
from docx.table import Table
from openpyxl import load_workbook
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pypdf import PdfReader

ClassScope = Literal["GS-KPI-01", "AUTO-N8N-01", "COMMON"]
ReviewStatus = Literal["provided_sample", "pending", "approved_demo"]
DataOrigin = Literal["provided_sample", "synthetic_demo"]
SECTION_HEADING = re.compile(r"^((?:COMMON|GS|AUTO)(?:-CLASS)?-\d{2})\s+")


class SectionMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    class_scope: ClassScope
    topics: tuple[str, ...] = ()


class SourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    doc_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    data_origin: DataOrigin
    review_status: ReviewStatus
    sections: dict[str, SectionMetadata]


class KnowledgeChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    record_id: str = Field(min_length=1)
    doc_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    class_scope: ClassScope
    content: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    data_origin: DataOrigin
    review_status: ReviewStatus
    topics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_origin(self):
        if self.data_origin == "synthetic_demo" and self.review_status == "provided_sample":
            raise ValueError("Synthetic content cannot claim to be a supplied sample")
        return self

    @property
    def canonical_key(self) -> tuple[str, str, str, str]:
        return self.doc_id, self.section_id, self.version, self.class_scope


def _sections(lines: list[tuple[str, str]], path: Path, metadata: SourceMetadata) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    current: str | None = None
    text: list[str] = []
    locations: list[str] = []

    def flush() -> None:
        if current is None:
            return
        section = metadata.sections[current]
        chunks.append(KnowledgeChunk(
            record_id=current, doc_id=metadata.doc_id, section_id=current,
            version=metadata.version, class_scope=section.class_scope,
            content="\n".join(text), source_name=path.name,
            locator="; ".join(dict.fromkeys(locations)),
            data_origin=metadata.data_origin, review_status=metadata.review_status,
            topics=section.topics,
        ))

    for line, locator in lines:
        if not line.strip():
            continue
        heading = SECTION_HEADING.match(line.strip())
        if heading:
            flush()
            current = heading.group(1)
            if current not in metadata.sections:
                raise ValueError(f"Missing operator scope metadata for {current}")
            text, locations = [], []
        if current is not None:
            text.append(line.strip())
            locations.append(locator)
    flush()
    if not chunks:
        raise ValueError(f"No supported section headings in {path.name}; OCR is not implemented")
    return chunks


def parse_document(path: Path, metadata: SourceMetadata) -> list[KnowledgeChunk]:
    """Parse text-based PDF or sectioned DOCX using explicit scope per section."""
    path = Path(path)
    lines: list[tuple[str, str]] = []
    if path.suffix.lower() == ".docx":
        for index, block in enumerate(DocxDocument(path).iter_inner_content(), start=1):
            if isinstance(block, Table):
                lines.extend((" | ".join(cell.text for cell in row.cells), f"block {index}") for row in block.rows)
            else:
                lines.append((block.text, f"block {index}"))
    elif path.suffix.lower() == ".pdf":
        for page, item in enumerate(PdfReader(path).pages, start=1):
            lines.extend((line, f"page {page}") for line in (item.extract_text() or "").splitlines())
    else:
        raise ValueError("Expected PDF or DOCX")
    return _sections(lines, path, metadata)


FAQ_COLUMNS = (
    "record_id", "class_scope", "question", "answer", "source_doc_id",
    "source_section_id", "version", "data_origin", "review_status", "topics",
)


def parse_faq_xlsx(path: Path) -> list[KnowledgeChunk]:
    """Read curated local rows. Uploaded rows cannot grant approval or class access."""
    workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    try:
        sheet = workbook["knowledge"]
        rows = sheet.iter_rows()
        header = [cell.value for cell in next(rows)]
        if tuple(header) != FAQ_COLUMNS:
            raise ValueError(f"Expected columns in order: {FAQ_COLUMNS}")
        result = []
        identities: set[str] = set()
        logical_keys: set[tuple] = set()
        for row_number, cells in enumerate(rows, start=2):
            values = [cell.value for cell in cells]
            if all(value is None for value in values):
                continue
            if any(cell.data_type == "f" for cell in cells):
                raise ValueError(f"Formulas are not allowed in FAQ row {row_number}")
            row = dict(zip(header, values))
            for column in FAQ_COLUMNS:
                if column != "topics" and (not isinstance(row[column], str) or not row[column].strip()):
                    raise ValueError(f"Missing text field {column} in row {row_number}")
            if row["record_id"] in identities:
                raise ValueError(f"Duplicate FAQ ID: {row['record_id']}")
            chunk = KnowledgeChunk(
                record_id=row["record_id"], doc_id=row["source_doc_id"],
                section_id=row["source_section_id"], version=row["version"],
                class_scope=row["class_scope"], content=f"Q: {row['question']}\nA: {row['answer']}",
                source_name=Path(path).name, locator=f"knowledge!row {row_number}; {row['record_id']}",
                data_origin=row["data_origin"], review_status=row["review_status"],
                topics=tuple(topic.strip() for topic in (row["topics"] or "").split(",") if topic.strip()),
            )
            if chunk.canonical_key in logical_keys:
                raise ValueError("Use distinct source section IDs for distinct FAQ answers")
            result.append(chunk)
            identities.add(chunk.record_id)
            logical_keys.add(chunk.canonical_key)
        if not result:
            raise ValueError("FAQ workbook is empty")
        return result
    finally:
        workbook.close()


def eligible_demo_chunks(chunks: list[KnowledgeChunk], class_code: str) -> list[KnowledgeChunk]:
    """Scope and review gate before indexing/retrieval in DEMO mode only."""
    if class_code not in {"GS-KPI-01", "AUTO-N8N-01"}:
        raise ValueError("Unknown authorized class")
    return [chunk for chunk in chunks if chunk.class_scope in {class_code, "COMMON"}
            and chunk.review_status in {"provided_sample", "approved_demo"}]


def deduplicate_sources(chunks: list[KnowledgeChunk]) -> list[KnowledgeChunk]:
    """First representation wins; manifest order selects the preferred source format."""
    seen = set()
    result = []
    for chunk in chunks:
        if chunk.canonical_key not in seen:
            result.append(chunk)
            seen.add(chunk.canonical_key)
    return result

