"""Inventory local originals and create an explicit, operator-controlled manifest."""

import argparse
import hashlib
import json
from pathlib import Path

from docx import Document

from app.ingestion import SECTION_HEADING

SOURCE_RULES = {
    "01_Giao_trinh_Google_Sheets_KPI_Dashboard.docx": (
        "DOC-GS-01",
        {f"GS-{number:02}": "GS-KPI-01" for number in range(1, 9)},
    ),
    "02_Giao_trinh_AI_Automation_n8n.docx": (
        "DOC-AUTO-01",
        {f"AUTO-{number:02}": "AUTO-N8N-01" for number in range(1, 11)},
    ),
    "03_FAQ_Quy_dinh_lop_hoc_GIA_LAP.docx": (
        "DOC-FAQ-01",
        {
            **{f"COMMON-{number:02}": "COMMON" for number in range(1, 5)},
            **{f"GS-CLASS-{number:02}": "GS-KPI-01" for number in range(1, 3)},
            **{f"AUTO-CLASS-{number:02}": "AUTO-N8N-01" for number in range(1, 3)},
        },
    ),
}


def inventory(root: Path) -> tuple[list[dict], dict]:
    items = []
    sources = []

    for path in sorted(root.iterdir()):
        if path.suffix.lower() not in {".docx", ".json"} or path.name.endswith(".local.json"):
            continue

        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        item = {"source_name": path.name, "sha256": checksum, "index": path.name in SOURCE_RULES}

        if path.suffix.lower() == ".docx":
            document = Document(path)
            sections = [
                match.group(1)
                for paragraph in document.paragraphs
                if (match := SECTION_HEADING.match(paragraph.text.strip()))
            ]
            item.update(
                paragraphs=len(document.paragraphs), tables=len(document.tables), sections=sections
            )

        if path.name in SOURCE_RULES:
            doc_id, scopes = SOURCE_RULES[path.name]

            if set(item["sections"]) != set(scopes):
                raise ValueError(f"Original section inventory changed: {path.name}")

            sources.append(
                {
                    "path": path.name,
                    "sha256": checksum,
                    "metadata": {
                        "doc_id": doc_id,
                        "version": "TEST-1.0",
                        "data_origin": "provided_sample",
                        "review_status": "provided_sample",
                        "sections": {
                            section: {"class_scope": scope} for section, scope in scopes.items()
                        },
                    },
                }
            )

        items.append(item)

    if len(sources) != len(SOURCE_RULES):
        raise ValueError("All three original course/FAQ DOCX files are required")

    return items, {"notice": "DEMO ONLY: supplied simulated materials", "sources": sources}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/raw"))
    parser.add_argument("--write-manifest", type=Path)
    args = parser.parse_args()
    items, manifest = inventory(args.root)

    if args.write_manifest:
        if args.write_manifest.resolve().parent != args.root.resolve():
            parser.error("Write the local manifest alongside its source files")

        if args.write_manifest.exists():
            parser.error("Manifest exists; review it manually instead of overwriting decisions")

        args.write_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(json.dumps({"mode": "DEMO ONLY", "inventory": items}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
