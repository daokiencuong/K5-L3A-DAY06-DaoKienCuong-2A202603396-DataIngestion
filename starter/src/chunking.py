"""Fixed-size and paragraph-aware chunking with stable source offsets."""
from __future__ import annotations

import hashlib
import re
from typing import Iterable

from .models import Chunk, DocumentPage


def _locator(page: DocumentPage) -> str:
    return page.unit_id or (f"page-{page.page}" if page.page is not None else "unknown-unit")


def _make_chunk(
    page: DocumentPage,
    text: str,
    index: int,
    start: int,
    end: int,
    strategy: str,
) -> Chunk:
    locator = _locator(page)
    stable_material = f"{page.document_id}\0{locator}\0{start}\0{end}\0{text}"
    chunk_id = hashlib.sha256(stable_material.encode("utf-8")).hexdigest()[:32]
    metadata = dict(page.metadata)
    metadata.update(
        {
            "unit_id": locator,
            "chunk_index": index,
            "char_start": start,
            "char_end": end,
            "chunking_strategy": strategy,
        }
    )
    return Chunk(
        chunk_id=chunk_id,
        document_id=page.document_id,
        source=page.source,
        page=page.page,
        unit_id=locator,
        text=text,
        metadata=metadata,
    )


def _window_ranges(text: str, size: int, overlap: int) -> Iterable[tuple[int, int]]:
    start = 0
    length = len(text)
    while start < length:
        end = min(start + size, length)
        if end < length:
            # Prefer a nearby word boundary while keeping chunks close to target size.
            boundary = text.rfind(" ", start + size // 2, end)
            if boundary > start:
                end = boundary
        if end <= start:
            end = min(start + size, length)
        yield start, end
        if end >= length:
            break
        start = max(start + 1, end - overlap)


def fixed_size(page: DocumentPage, size: int, overlap: int) -> list[Chunk]:
    """Split a source unit into approximately fixed character windows."""
    if size <= 0:
        raise ValueError("size must be greater than zero")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be non-negative and smaller than size")
    text = page.text.strip()
    if not text:
        return []
    # `strip` can remove source characters; record offsets against the original page.
    left_trim = len(page.text) - len(page.text.lstrip())
    chunks: list[Chunk] = []
    for index, (start, end) in enumerate(_window_ranges(text, size, overlap)):
        fragment = text[start:end].strip()
        if fragment:
            chunks.append(
                _make_chunk(
                    page,
                    fragment,
                    index,
                    left_trim + start,
                    left_trim + end,
                    "fixed_size",
                )
            )
    return chunks


def _paragraph_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for match in re.finditer(r"\S.*?(?=\n[ \t]*\n|\Z)", text, flags=re.DOTALL):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip())
        paragraph = raw.strip()
        if paragraph:
            start = match.start() + leading
            spans.append((start, start + len(paragraph), paragraph))
    return spans


def by_paragraph(page: DocumentPage, max_size: int) -> list[Chunk]:
    """Group whole paragraphs up to max_size, splitting only oversized paragraphs."""
    if max_size <= 0:
        raise ValueError("max_size must be greater than zero")
    text = page.text
    spans = _paragraph_spans(text)
    if not spans:
        return []

    chunks: list[Chunk] = []
    group: list[tuple[int, int, str]] = []
    group_size = 0

    def flush() -> None:
        nonlocal group, group_size
        if not group:
            return
        joined = "\n\n".join(item[2] for item in group)
        chunks.append(
            _make_chunk(page, joined, len(chunks), group[0][0], group[-1][1], "by_paragraph")
        )
        group = []
        group_size = 0

    for start, end, paragraph in spans:
        if len(paragraph) > max_size:
            flush()
            for sub_start, sub_end in _window_ranges(paragraph, max_size, min(80, max_size // 8)):
                fragment = paragraph[sub_start:sub_end].strip()
                if fragment:
                    chunks.append(
                        _make_chunk(
                            page,
                            fragment,
                            len(chunks),
                            start + sub_start,
                            start + sub_end,
                            "by_paragraph",
                        )
                    )
            continue
        proposed_size = group_size + (2 if group else 0) + len(paragraph)
        if group and proposed_size > max_size:
            flush()
        group.append((start, end, paragraph))
        group_size += len(paragraph) + (2 if len(group) > 1 else 0)

    flush()
    return chunks
