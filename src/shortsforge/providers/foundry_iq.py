"""Foundry IQ provider — Azure AI Foundry agentic retrieval (Microsoft IQ integration)."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any

import httpx
import structlog
from pydantic import BaseModel

from shortsforge.security.http import SSRFError, safe_get
from shortsforge.security.prompt_guard import wrap_foundry_iq

logger = structlog.get_logger(__name__)

_CACHE_TTL_S = 900  # 15 minutes
_cache: dict[str, tuple[float, Any]] = {}


class Citation(BaseModel):
    source: str
    snippet: str
    url: str | None = None
    page: int | None = None


class RetrievalResult(BaseModel):
    answer: str
    citations: list[Citation]
    confidence: float
    grounded_text: str  # wrapped with Foundry IQ delimiters


class FoundryIQ:
    """Thin async client over Azure AI Foundry agentic retrieval."""

    def __init__(self, endpoint: str, api_key: str, *, timeout: float = 30.0) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=self._endpoint,
            headers={
                "api-key": self._api_key,
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    @classmethod
    def from_env(cls) -> "FoundryIQ":
        """Construct from AZURE_FOUNDRY_ENDPOINT and AZURE_FOUNDRY_KEY env vars."""
        endpoint = os.environ.get("AZURE_FOUNDRY_ENDPOINT", "")
        api_key = os.environ.get("AZURE_FOUNDRY_KEY", "")
        if not endpoint or not api_key:
            raise RuntimeError(
                "AZURE_FOUNDRY_ENDPOINT and AZURE_FOUNDRY_KEY must be set for Foundry IQ"
            )
        return cls(endpoint, api_key)

    async def kb_create(self, name: str) -> str:
        """Create a knowledge base and return its ID."""
        resp = await self._client.post(
            "/knowledgebases",
            json={"name": name},
        )
        resp.raise_for_status()
        data = resp.json()
        kb_id: str = data["id"]
        logger.info("foundry_iq.kb_created", name=name, kb_id=kb_id)
        return kb_id

    async def kb_ingest(self, kb_id: str, source: str | Path) -> str:
        """Ingest a document or URL into a knowledge base.

        Supports: PDF, markdown, txt, html (local files) and https:// URLs.
        SSRF-protected for URL sources.
        """
        source_str = str(source)

        if source_str.startswith("http://") or source_str.startswith("https://"):
            # SSRF check: use safe_get to validate destination
            try:
                await safe_get(source_str)
            except SSRFError as exc:
                raise ValueError(f"URL rejected by SSRF guard: {exc}") from exc
            payload: dict[str, Any] = {"kb_id": kb_id, "url": source_str}
        else:
            file_path = Path(source_str)
            if not file_path.exists():
                raise FileNotFoundError(f"Source file not found: {file_path}")
            allowed_exts = {".pdf", ".md", ".txt", ".html", ".htm"}
            if file_path.suffix.lower() not in allowed_exts:
                raise ValueError(
                    f"Unsupported file type {file_path.suffix!r}. "
                    f"Allowed: {allowed_exts}"
                )
            with open(file_path, "rb") as fh:
                content = fh.read()
            payload = {
                "kb_id": kb_id,
                "filename": file_path.name,
                "content_b64": __import__("base64").b64encode(content).decode(),
            }

        resp = await self._client.post("/knowledgebases/ingest", json=payload)
        resp.raise_for_status()
        job_id: str = resp.json()["job_id"]
        logger.info("foundry_iq.ingest_started", kb_id=kb_id, job_id=job_id)
        return job_id

    async def kb_query(
        self,
        kb_id: str,
        question: str,
        *,
        top_k: int = 8,
    ) -> RetrievalResult:
        """Query a knowledge base and return grounded results with citations."""
        cache_key = hashlib.sha256(f"{kb_id}:{question}".encode()).hexdigest()

        # Check cache
        if cache_key in _cache:
            ts, result = _cache[cache_key]
            if time.time() - ts < _CACHE_TTL_S:
                logger.debug("foundry_iq.cache_hit", kb_id=kb_id)
                return result

        resp = await self._client.post(
            "/knowledgebases/query",
            json={"kb_id": kb_id, "question": question, "top_k": top_k},
        )
        resp.raise_for_status()
        data = resp.json()

        citations = [
            Citation(
                source=c.get("source", ""),
                snippet=c.get("snippet", ""),
                url=c.get("url"),
                page=c.get("page"),
            )
            for c in data.get("citations", [])
        ]

        raw_answer = data.get("answer", "")

        result = RetrievalResult(
            answer=raw_answer,
            citations=citations,
            confidence=float(data.get("confidence", 0.0)),
            grounded_text=wrap_foundry_iq(raw_answer),
        )

        _cache[cache_key] = (time.time(), result)
        logger.info(
            "foundry_iq.query_done",
            kb_id=kb_id,
            citations=len(citations),
            confidence=result.confidence,
        )
        return result

    async def close(self) -> None:
        await self._client.aclose()
