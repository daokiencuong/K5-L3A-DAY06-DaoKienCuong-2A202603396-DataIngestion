"""Run fixed, paragraph, and paragraph-plus-metadata-filter retrieval experiments."""
from __future__ import annotations

import json
import os
import platform
import time
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .chunking import by_paragraph, fixed_size
from .embeddings import MyEmbedder
from .evaluation import evaluate_query, summarize
from .ingestion import load_html_manifest
from .store import PgVectorStore


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _chunk_length_stats(chunks: list[Any]) -> dict[str, Any]:
    """Summarize character lengths without adding tokenization dependencies."""
    lengths = sorted(len(chunk.text) for chunk in chunks)
    if not lengths:
        return {"count": 0, "total_characters": 0}

    def percentile(fraction: float) -> int:
        index = round((len(lengths) - 1) * fraction)
        return lengths[index]

    return {
        "count": len(lengths),
        "total_characters": sum(lengths),
        "min_characters": lengths[0],
        "mean_characters": round(sum(lengths) / len(lengths), 2),
        "p50_characters": percentile(0.50),
        "p90_characters": percentile(0.90),
        "max_characters": lengths[-1],
    }


def main() -> None:
    """Load the corpus and benchmark, index three configurations, and save traces."""
    starter_dir = Path(__file__).resolve().parents[1]
    project_dir = starter_dir.parent
    load_dotenv(starter_dir / ".env")

    manifest_path = Path(os.getenv("CORPUS_MANIFEST", starter_dir / "data" / "manifest.json"))
    benchmark_path = Path(os.getenv("BENCHMARK_PATH", project_dir / "benchmark" / "python_docs_questions.json"))
    if not manifest_path.is_absolute():
        manifest_path = (starter_dir / manifest_path).resolve()
    if not benchmark_path.is_absolute():
        benchmark_path = (project_dir / benchmark_path).resolve()
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Corpus manifest not found: {manifest_path}")
    if not benchmark_path.is_file():
        raise FileNotFoundError(f"Benchmark not found: {benchmark_path}")

    manifest, pages = load_html_manifest(manifest_path)
    if len(pages) < 30 or any(not page.text.strip() for page in pages):
        raise ValueError("The corpus must contain at least 30 non-empty source units.")
    benchmark = _read_json(benchmark_path)
    questions = benchmark.get("questions")
    if not isinstance(questions, list) or len(questions) < 20:
        raise ValueError("Benchmark must contain at least 20 questions.")
    question_ids = [str(question.get("id", "")) for question in questions]
    if not all(question_ids) or len(question_ids) != len(set(question_ids)):
        raise ValueError("Every benchmark question needs a unique, non-empty id.")
    split_counts = {name: sum(question.get("split") == name for question in questions)
                    for name in ("dev", "test")}
    if split_counts["dev"] < 15 or split_counts["test"] < 5:
        raise ValueError(f"Benchmark requires at least 15 dev and 5 test questions; found {split_counts}.")

    fixed_size_chars = int(os.getenv("FIXED_CHUNK_SIZE", "500"))
    overlap = int(os.getenv("FIXED_CHUNK_OVERLAP", "50"))
    paragraph_limit = int(os.getenv("PARAGRAPH_CHUNK_SIZE", "900"))
    strategies = {
        "A_fixed": [chunk for page in pages for chunk in fixed_size(page, fixed_size_chars, overlap)],
        "B_paragraph": [chunk for page in pages for chunk in by_paragraph(page, paragraph_limit)],
    }
    # Strategy C changes only retrieval filtering, so it reuses B's exact chunks/vectors.
    strategies["C_paragraph_filter"] = strategies["B_paragraph"]
    if any(not chunks for chunks in strategies.values()):
        raise ValueError("Chunking produced no chunks; check that source text is extractable.")

    model_name = os.getenv("EMBEDDING_MODEL", "")
    embedder = MyEmbedder(model_name)
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError("DATABASE_URL is missing. Configure starter/.env first.")

    started = time.perf_counter()
    vectors_by_strategy: dict[str, list[list[float]]] = {}
    vectors_by_strategy["A_fixed"] = embedder.embed_documents([c.text for c in strategies["A_fixed"]])
    vectors_by_strategy["B_paragraph"] = embedder.embed_documents([c.text for c in strategies["B_paragraph"]])
    vectors_by_strategy["C_paragraph_filter"] = vectors_by_strategy["B_paragraph"]
    embedding_seconds = time.perf_counter() - started

    store = PgVectorStore(database_url, embedder.dimensions)
    store.create_schema()
    ingest_started = time.perf_counter()
    for strategy, chunks in strategies.items():
        store.upsert(strategy, chunks, vectors_by_strategy[strategy])
    ingest_seconds = time.perf_counter() - ingest_started

    expected_counts = {strategy: len(chunks) for strategy, chunks in strategies.items()}
    first_load_counts = {strategy: store.count_chunks(strategy) for strategy in strategies}
    if first_load_counts != expected_counts:
        raise RuntimeError(f"Persisted chunk counts do not match input: {first_load_counts} vs {expected_counts}")

    # Exercise the same-version replace path once to verify that re-ingestion
    # does not duplicate persisted chunks. Reuse vectors; this makes no API calls.
    reingest_started = time.perf_counter()
    for strategy, chunks in strategies.items():
        store.upsert(strategy, chunks, vectors_by_strategy[strategy])
    reingest_seconds = time.perf_counter() - reingest_started
    reingest_counts = {strategy: store.count_chunks(strategy) for strategy in strategies}
    if reingest_counts != expected_counts:
        raise RuntimeError(f"Re-ingestion changed persisted chunk counts: {reingest_counts} vs {expected_counts}")

    traces: list[dict[str, Any]] = []
    search_seconds_by_strategy = {strategy: [] for strategy in strategies}
    for question in questions:
        query_vector = embedder.embed_query(question["question"])
        for strategy in strategies:
            filters = question.get("filter") if strategy == "C_paragraph_filter" else None
            search_started = time.perf_counter()
            hits = store.search(strategy, query_vector, top_k=3, filters=filters)
            search_seconds = time.perf_counter() - search_started
            search_seconds_by_strategy[strategy].append(search_seconds)
            traces.append({"strategy": strategy, "search_seconds": round(search_seconds, 6),
                           **evaluate_query(question, hits)})

    output_dir = Path(os.getenv("RESULTS_DIR", starter_dir / "results"))
    if not output_dir.is_absolute():
        output_dir = (starter_dir / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "corpus_id": manifest.get("corpus_id"),
        "manifest": str(manifest_path),
        "benchmark": str(benchmark_path),
        "embedding_model": embedder.model_name,
        "embedding_dimensions": embedder.dimensions,
        "embedding_api_usage": {
            "requests": embedder.request_count,
            "responses_with_token_usage": embedder.usage_responses_with_tokens,
            "responses_without_token_usage": embedder.usage_responses_without_tokens,
            "input_tokens": (embedder.input_tokens
                             if embedder.usage_responses_without_tokens == 0 else None),
        },
        "vector_store": "PostgreSQL + pgvector cosine distance",
        "search_method": "Exact cosine distance scan using ORDER BY embedding <=> query; no approximate vector index is created.",
        "runtime": {
            "python": platform.python_version(),
            "packages": {
                name: importlib_metadata.version(name)
                for name in ("psycopg", "psycopg-binary", "python-dotenv", "pypdf")
            },
        },
        "corpus_stats": {
            "source_units": len(pages),
            "normalized_source_characters": sum(len(page.text) for page in pages),
        },
        "storage_verification": {
            "expected_counts": expected_counts,
            "first_load_counts": first_load_counts,
            "same_version_reingest_counts": reingest_counts,
            "reingest_has_no_duplicates": reingest_counts == expected_counts,
        },
        "chunking": {
            "A_fixed": {"size": fixed_size_chars, "overlap": overlap,
                        "chunks": len(strategies["A_fixed"]),
                        "length_stats": _chunk_length_stats(strategies["A_fixed"])},
            "B_paragraph": {"max_size": paragraph_limit,
                            "chunks": len(strategies["B_paragraph"]),
                            "length_stats": _chunk_length_stats(strategies["B_paragraph"])},
            "C_paragraph_filter": {"max_size": paragraph_limit,
                                   "chunks": len(strategies["C_paragraph_filter"]),
                                   "length_stats": _chunk_length_stats(strategies["C_paragraph_filter"])},
        },
        "timing_seconds": {"embedding": round(embedding_seconds, 3),
                           "database_ingest": round(ingest_seconds, 3),
                           "same_version_reingest_verification": round(reingest_seconds, 3),
                           "search_by_strategy": {
                               strategy: {
                                   "queries": len(values),
                                   "total": round(sum(values), 6),
                                   "mean": round(sum(values) / len(values), 6) if values else 0,
                               }
                               for strategy, values in search_seconds_by_strategy.items()
                           }},
        "split_counts": split_counts,
        "summary": summarize(traces),
        "manual_review_samples": {
            strategy: [trace for trace in traces
                       if trace["strategy"] == strategy and trace["split"] == "test"][:5]
            for strategy in strategies
        },
        "traces": traces,
    }
    result_path = output_dir / "retrieval_results.json"
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"results": str(result_path), "summary": report["summary"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
