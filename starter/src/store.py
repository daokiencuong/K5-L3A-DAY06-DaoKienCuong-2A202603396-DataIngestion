"""Persistent cosine search and JSON metadata filtering with PostgreSQL/pgvector."""
from __future__ import annotations

import math
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .models import Chunk


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int) -> None:
        if not database_url.strip():
            raise ValueError("DATABASE_URL must not be empty.")
        if dimensions <= 0:
            raise ValueError("Embedding dimensions must be positive.")
        self.database_url = database_url
        self.dimensions = dimensions

    def create_schema(self) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            connection.execute(
                f"""CREATE TABLE IF NOT EXISTS retrieval_chunks (
                    strategy TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    page INTEGER,
                    unit_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    embedding vector({self.dimensions}) NOT NULL,
                    PRIMARY KEY (strategy, chunk_id)
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS retrieval_chunks_strategy_idx ON retrieval_chunks (strategy)"
            )

    @staticmethod
    def _vector_literal(vector: list[float], dimensions: int) -> str:
        if len(vector) != dimensions:
            raise ValueError(f"Expected {dimensions} embedding dimensions, got {len(vector)}.")
        if any(not math.isfinite(float(value)) for value in vector):
            raise ValueError("Embedding vectors must contain only finite numbers.")
        return "[" + ",".join(format(float(value), ".9g") for value in vector) + "]"

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if not strategy.strip():
            raise ValueError("Strategy name must not be empty.")
        if len(chunks) != len(vectors):
            raise ValueError("There must be exactly one vector per chunk.")
        ids = [chunk.chunk_id for chunk in chunks]
        if len(ids) != len(set(ids)):
            raise ValueError("Chunk IDs must be unique within a strategy.")
        rows = []
        for chunk, vector in zip(chunks, vectors):
            rows.append((strategy, chunk.chunk_id, chunk.document_id, chunk.source, chunk.page,
                         chunk.unit_id, chunk.text, Jsonb(chunk.metadata),
                         self._vector_literal(vector, self.dimensions)))

        # Replace this strategy's snapshot in one transaction so removed source
        # chunks disappear and a failed re-ingest leaves the previous snapshot intact.
        with psycopg.connect(self.database_url) as connection:
            connection.execute("DELETE FROM retrieval_chunks WHERE strategy = %s", (strategy,))
            if rows:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """INSERT INTO retrieval_chunks
                           (strategy, chunk_id, document_id, source, page, unit_id, text, metadata, embedding)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)""",
                        rows,
                    )

    def count_chunks(self, strategy: str) -> int:
        """Return the persisted chunk count for one strategy."""
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT count(*) FROM retrieval_chunks WHERE strategy = %s", (strategy,)
            ).fetchone()
        return int(row[0])

    def search(self, strategy: str, query_vector: list[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if top_k <= 0:
            raise ValueError("top_k must be positive.")
        query = self._vector_literal(query_vector, self.dimensions)
        allowed_fields = {
            "document_id", "source", "page", "unit_id", "section", "year", "version",
            "category", "audience", "language", "effective_date", "document_type", "title",
        }
        clauses = ["strategy = %s"]
        where_params: list[Any] = [strategy]
        for key, value in (filters or {}).items():
            if key not in allowed_fields:
                raise ValueError(f"Unsupported metadata filter field: {key}")
            if value is None:
                raise ValueError(f"Metadata filter {key!r} must not be null.")
            if key in {"document_id", "source", "page", "unit_id"}:
                clauses.append(f"{key} = %s")
                where_params.append(value)
            else:
                clauses.append("metadata ->> %s = %s")
                where_params.extend((key, str(value)))
        sql = f"""SELECT chunk_id, document_id, source, page, unit_id, text,
                         metadata, 1 - (embedding <=> %s::vector) AS score
                  FROM retrieval_chunks
                  WHERE {' AND '.join(clauses)}
                  ORDER BY embedding <=> %s::vector
                  LIMIT %s"""
        # The distance vector is bound in SELECT and ORDER BY; SQL placeholders
        # are consumed before those in the WHERE clause.
        params = [query, *where_params, query, top_k]
        with psycopg.connect(self.database_url) as connection:
            rows = connection.execute(sql, params).fetchall()
        return [
            {"chunk_id": row[0], "document_id": row[1], "source": row[2], "page": row[3],
             "unit_id": row[4], "text": row[5], "metadata": row[6], "score": float(row[7])}
            for row in rows
        ]
