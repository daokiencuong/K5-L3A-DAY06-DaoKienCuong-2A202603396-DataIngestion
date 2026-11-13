"""OpenAI-compatible embedding API adapter (OpenAI or OpenRouter)."""
from __future__ import annotations

import json
import os
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, question: str) -> list[float]: ...


class MyEmbedder:
    """Create real embeddings using an OpenAI-compatible embeddings endpoint."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = (model_name or os.getenv("EMBEDDING_MODEL") or "").strip()
        if not self.model_name:
            raise ValueError("Set EMBEDDING_MODEL to a real embedding model name.")

        openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        router_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        self.api_key = openai_key or router_key
        if not self.api_key:
            raise ValueError("Set OPENAI_API_KEY or OPENROUTER_API_KEY; mock embeddings are not supported.")

        default_url = (
            "https://api.openai.com/v1/embeddings"
            if openai_key
            else "https://openrouter.ai/api/v1/embeddings"
        )
        self.endpoint = os.getenv("EMBEDDING_API_URL", default_url).strip()
        dimensions = os.getenv("EMBEDDING_DIMENSIONS", "").strip()
        known_dimensions = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072,
                            "text-embedding-ada-002": 1536}
        self.dimensions = int(dimensions) if dimensions else known_dimensions.get(self.model_name, 0)
        if dimensions and self.dimensions <= 0:
            raise ValueError("EMBEDDING_DIMENSIONS must be a positive integer.")
        self.request_count = 0
        self.usage_responses_with_tokens = 0
        self.usage_responses_without_tokens = 0
        self.input_tokens = 0

    def _request(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, Any] = {"model": self.model_name, "input": texts}
        if self.dimensions:
            payload["dimensions"] = self.dimensions
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"Embedding API returned HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Could not reach embedding API: {exc}") from exc

        rows = body.get("data")
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise RuntimeError("Embedding API response has an unexpected number of vectors.")
        # The API may return rows out of order when an index is supplied.
        rows = sorted(rows, key=lambda row: row.get("index", 0))
        vectors = [row.get("embedding") for row in rows]
        if any(not isinstance(vector, list) or not vector for vector in vectors):
            raise RuntimeError("Embedding API returned a missing or empty vector.")
        parsed = [[float(value) for value in vector] for vector in vectors]
        dimensions = len(parsed[0])
        if any(len(vector) != dimensions for vector in parsed):
            raise RuntimeError("Embedding API returned inconsistent vector dimensions.")
        if self.dimensions and dimensions != self.dimensions:
            raise RuntimeError(f"Model returned {dimensions} dimensions; configured for {self.dimensions}.")
        self.dimensions = dimensions
        self.request_count += 1
        usage = body.get("usage") or {}
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        if isinstance(input_tokens, int) and input_tokens >= 0:
            self.usage_responses_with_tokens += 1
            self.input_tokens += input_tokens
        else:
            self.usage_responses_without_tokens += 1
        return parsed

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 64):
            vectors.extend(self._request(texts[start:start + 64]))
        return vectors

    def embed_query(self, question: str) -> list[float]:
        if not question.strip():
            raise ValueError("Query text must not be empty.")
        return self._request([question])[0]
