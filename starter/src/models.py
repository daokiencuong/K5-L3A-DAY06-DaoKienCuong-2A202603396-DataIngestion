"""Shared data contracts; keep traceability from chunk back to source."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DocumentPage:
    """An extracted physical page or a stable source unit such as an HTML page."""

    document_id: str
    source: str
    page: int | None
    text: str
    unit_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    """A retrieval chunk with a stable ID and its original source locator."""

    chunk_id: str
    document_id: str
    source: str
    page: int | None
    text: str
    unit_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
