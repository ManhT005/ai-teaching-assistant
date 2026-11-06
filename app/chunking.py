"""Section-first chunks measured with the same tokenizer as the embedding model."""

import hashlib
import json
import re

from app.embedding_contract import MAX_INPUT_TOKENS, PASSAGE_PREFIX
from app.ingestion import KnowledgeChunk, SourceBlock


class IndexedChunk(KnowledgeChunk):
    chunk_index: int
    chunk_id: str
    content_hash: str
    token_count: int


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_sections(
    sections: list[KnowledgeChunk],
    tokenizer,
    target_tokens: int = 320,
    overlap_tokens: int = 40,
) -> list[IndexedChunk]:
    if not 16 <= target_tokens <= MAX_INPUT_TOKENS or not 0 <= overlap_tokens < target_tokens // 2:
        raise ValueError("Invalid chunk token budget or overlap")

    def count(text: str) -> int:
        return len(tokenizer.encode(PASSAGE_PREFIX + text, add_special_tokens=True))

    result = []

    for section in sections:
        blocks = list(section.blocks) or [SourceBlock(content=section.content, locator=section.locator)]
        heading = section.content.splitlines()[0]

        if count(heading) >= target_tokens - 8:
            raise ValueError(f"Section heading exceeds token budget: {section.section_id}")

        def render(parts: list[SourceBlock]) -> str:
            return "\n".join([heading, *(part.content for part in parts)])

        units = []

        for block_index, block in enumerate(blocks):
            text = block.content

            if block_index == 0 and text.startswith(heading):
                text = text[len(heading):].lstrip("\n")

            if not text:
                continue

            # Keep short paragraphs, formulas, checklist entries and table rows intact.
            pieces = [text] if count(heading + "\n" + text) <= target_tokens else re.split(r"(?<=[.!?])\s+|\n", text)

            for piece in pieces:
                while piece:
                    if count(heading + "\n" + piece) <= target_tokens:
                        units.append(SourceBlock(content=piece, locator=block.locator))
                        break

                    low, high = 1, len(piece)

                    while low < high:
                        midpoint = (low + high + 1) // 2
                        if count(heading + "\n" + piece[:midpoint]) <= target_tokens:
                            low = midpoint
                        else:
                            high = midpoint - 1

                    if count(heading + "\n" + piece[:low]) > target_tokens:
                        raise ValueError("Cannot fit source text into the tokenizer budget")

                    units.append(SourceBlock(content=piece[:low], locator=block.locator))
                    piece = piece[low:]

        groups: list[list[SourceBlock]] = []
        current: list[SourceBlock] = []

        for unit in units:
            if current and count(render([*current, unit])) > target_tokens:
                groups.append(current)
                overlap = []

                for previous in reversed(current):
                    candidate = [previous, *overlap]
                    overlap_count = len(tokenizer.encode("\n".join(part.content for part in candidate), add_special_tokens=False))

                    if overlap_count > overlap_tokens:
                        break

                    overlap = candidate

                current = overlap

                if count(render([*current, unit])) > target_tokens:
                    current = []

            current.append(unit)

        if current or not groups:
            groups.append(current)

        for index, group in enumerate(groups):
            content = render(group)
            token_count = count(content)

            if token_count > min(target_tokens, MAX_INPUT_TOKENS):
                raise ValueError("Chunk exceeds the model token limit")

            content_hash = digest(content)
            identity = [section.doc_id, section.version, section.section_id, section.class_scope, index, content_hash]
            locators = [blocks[0].locator, *(part.locator for part in group)]
            result.append(IndexedChunk(
                **section.model_dump(exclude={"blocks", "content", "locator"}),
                blocks=tuple(group),
                content=content,
                locator="; ".join(dict.fromkeys(locators)),
                chunk_index=index,
                chunk_id=digest(json.dumps(identity, ensure_ascii=False)),
                content_hash=content_hash,
                token_count=token_count,
            ))

    return result
