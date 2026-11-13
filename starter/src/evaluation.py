"""Evidence-aware retrieval metrics and auditable per-query traces."""
from __future__ import annotations

import re
from typing import Any


def _normal(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _gold_locations(question: dict[str, Any]) -> list[dict[str, Any]]:
    locations = question.get("acceptable_gold") or question.get("gold_alternatives") or []
    if isinstance(locations, dict):
        locations = [locations]
    result = [dict(item) for item in locations if isinstance(item, dict)]
    result.append({
        "source_id": question.get("gold_source_id"),
        "page": question.get("gold_page"),
        "evidence_quote": question.get("evidence_quote"),
    })
    return result


def _matches_location(hit: dict[str, Any], location: dict[str, Any]) -> bool:
    metadata = hit.get("metadata") or {}
    source_id = location.get("gold_source_id", location.get("source_id", location.get("document_id")))
    if source_id is not None:
        source_values = {hit.get("document_id"), hit.get("unit_id"), hit.get("source"),
                         metadata.get("unit_id"), metadata.get("manifest_source_path")}
        if _normal(source_id) not in {_normal(value) for value in source_values if value is not None}:
            return False
    page = location.get("gold_page", location.get("page", location.get("unit_id")))
    if page is not None:
        candidates = {hit.get("page"), hit.get("unit_id"), metadata.get("unit_id")}
        if _normal(page) not in {_normal(value) for value in candidates if value is not None}:
            return False
    return source_id is not None or page is not None


def _contains_evidence(hit: dict[str, Any], quote: Any) -> bool:
    if not quote:
        return True
    text = _normal(hit.get("text"))
    return _normal(quote) in text


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Mark a hit only when one chunk matches a gold location and contains its evidence."""
    gold = _gold_locations(question)
    hit_trace = []
    for hit in hits:
        matched = []
        for location in gold:
            if _matches_location(hit, location) and _contains_evidence(
                hit, location.get("evidence_quote", location.get("evidence"))
            ):
                matched.append(location)
        hit_trace.append({**hit, "gold_match": bool(matched), "matched_gold": matched})
    return {
        "question_id": question.get("id"),
        "split": question.get("split", "unspecified"),
        "question": question.get("question", ""),
        "expected_answer": question.get("expected_answer"),
        "filter": question.get("filter") or {},
        "hit_at_1": bool(hit_trace and hit_trace[0]["gold_match"]),
        "evidence_hit_at_3": any(hit["gold_match"] for hit in hit_trace[:3]),
        "hits": hit_trace[:3],
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate counts and rates by strategy and benchmark split."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for trace in traces:
        groups.setdefault((str(trace.get("strategy", "unknown")),
                          str(trace.get("split", "unspecified"))), []).append(trace)
    summary: dict[str, Any] = {}
    for (strategy, split), rows in sorted(groups.items()):
        n = len(rows)
        summary.setdefault(strategy, {})[split] = {
            "questions": n,
            "hit_at_1": sum(bool(row.get("hit_at_1")) for row in rows),
            "hit_at_1_rate": sum(bool(row.get("hit_at_1")) for row in rows) / n,
            "evidence_hit_at_3": sum(bool(row.get("evidence_hit_at_3")) for row in rows),
            "evidence_hit_at_3_rate": sum(bool(row.get("evidence_hit_at_3")) for row in rows) / n,
        }
    return summary
