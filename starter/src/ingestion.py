"""Read HTML/PDF sources and preserve stable source locations."""
from __future__ import annotations

import json
import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .models import DocumentPage


class _MainContentParser(HTMLParser):
    """Extract Sphinx's main content while skipping navigation and page chrome."""

    _VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }
    _BLOCK_TAGS = {
        "address", "article", "blockquote", "br", "dd", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5",
        "h6", "hr", "li", "ol", "p", "pre", "section", "table", "tbody",
        "td", "tfoot", "th", "thead", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_main = False
        self.main_depth = 0
        self.skip_depth = 0
        self.parts: list[str] = []
        self.main_found = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        classes = set((attrs_dict.get("class") or "").split())
        if (
            not self.in_main
            and tag == "div"
            and attrs_dict.get("role") == "main"
            and "body" in classes
        ):
            self.in_main = True
            self.main_found = True
            self.main_depth = 1
            self.parts.append("\n\n")
            return
        if not self.in_main:
            return

        if tag not in self._VOID_TAGS:
            self.main_depth += 1

        if self.skip_depth:
            if tag not in self._VOID_TAGS:
                self.skip_depth += 1
            return

        ignored = (
            tag in {"nav", "script", "style", "noscript"}
            or "headerlink" in classes
            or "viewcode-link" in classes
            or "related" in classes
        )
        if ignored:
            if tag not in self._VOID_TAGS:
                self.skip_depth = 1
            return

        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if not self.in_main:
            return

        if self.skip_depth:
            self.skip_depth -= 1
            if tag not in self._VOID_TAGS:
                self.main_depth -= 1
            if self.main_depth <= 0:
                self.in_main = False
            return

        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        if tag not in self._VOID_TAGS:
            self.main_depth -= 1
        if self.main_depth <= 0:
            self.in_main = False

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.in_main and not self.skip_depth:
            self.parts.append(data)


def normalize_text(text: str) -> str:
    """Normalize Unicode and whitespace while retaining paragraph/code lines."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    lines = [re.sub(r"[\t\f\v ]+", " ", line).strip() for line in text.split("\n")]
    normalized = "\n".join(lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def load_pdf(path: Path, document_id: str, source: str) -> list[DocumentPage]:
    """Extract every physical PDF page, including empty-page warnings."""
    pages: list[DocumentPage] = []
    reader = PdfReader(str(path))
    for page_number, pdf_page in enumerate(reader.pages, start=1):
        try:
            raw_text = pdf_page.extract_text() or ""
            warning = None
        except Exception as exc:  # Keep the failed location visible to the caller.
            raw_text = ""
            warning = f"text extraction failed: {type(exc).__name__}: {exc}"
        text = normalize_text(raw_text)
        metadata: dict[str, Any] = {"file_path": str(path), "physical_page": page_number}
        if not text:
            metadata["extraction_warning"] = warning or "page contains no extractable text; OCR may be required"
        pages.append(
            DocumentPage(
                document_id=document_id,
                source=source,
                page=page_number,
                unit_id=f"page-{page_number}",
                text=text,
                metadata=metadata,
            )
        )
    return pages


def load_html(
    path: Path,
    document_id: str,
    source: str,
    unit_id: str,
    metadata: dict[str, Any] | None = None,
) -> DocumentPage:
    """Extract the main body of one HTML page into a traceable source unit."""
    raw_html = path.read_text(encoding="utf-8", errors="replace")
    parser = _MainContentParser()
    parser.feed(raw_html)
    parser.close()
    if not parser.main_found:
        raise ValueError(f"No main content container found in HTML source: {path}")
    text = normalize_text("".join(parser.parts))
    page_metadata = dict(metadata or {})
    page_metadata["file_path"] = str(path)
    if not text:
        page_metadata["extraction_warning"] = "HTML main-content area produced no text"
    return DocumentPage(
        document_id=document_id,
        source=source,
        page=None,
        unit_id=unit_id,
        text=text,
        metadata=page_metadata,
    )


def load_html_manifest(manifest_path: Path) -> tuple[dict[str, Any], list[DocumentPage]]:
    """Load each manifest unit, checking IDs, paths, and meaningful content."""
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    units = manifest.get("units")
    if not isinstance(units, list):
        raise ValueError(f"Manifest must contain a 'units' list: {manifest_path}")
    if len(units) < 30:
        raise ValueError(f"Corpus has {len(units)} manifest units; the lab requires at least 30")

    pages: list[DocumentPage] = []
    seen_ids: set[str] = set()
    for unit in units:
        unit_id = str(unit.get("unit_id", "")).strip()
        source_path = str(unit.get("source_path", "")).strip()
        source_url = str(unit.get("source_url", "")).strip()
        if not unit_id or not source_path or not source_url:
            raise ValueError(f"Manifest unit needs unit_id, source_path, and source_url: {unit}")
        if unit_id in seen_ids:
            raise ValueError(f"Duplicate manifest unit_id: {unit_id}")
        seen_ids.add(unit_id)
        path = (manifest_path.parent / source_path).resolve()
        if not path.is_relative_to(manifest_path.parent):
            raise ValueError(f"Manifest source path escapes the data directory: {source_path}")
        if not path.is_file():
            raise FileNotFoundError(f"Manifest source does not exist: {path}")
        metadata = {
            key: unit[key]
            for key in ("title", "section", "version", "normalized_characters")
            if key in unit
        }
        metadata["manifest_source_path"] = source_path
        page = load_html(
            path=path,
            document_id=str(manifest.get("corpus_id", "corpus")),
            source=source_url,
            unit_id=unit_id,
            metadata=metadata,
        )
        if len(page.text) < 1_500:
            raise ValueError(
                f"Manifest unit {unit_id} has only {len(page.text)} extracted characters; expected >= 1,500"
            )
        pages.append(page)
    return manifest, pages
