"""Async job manager — track long-running pipeline jobs with SSE streaming."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from pydantic import BaseModel


class JobStatus(BaseModel):
    job_id: str
    state: str  # pending | running | done | error
    progress: float = 0.0
    message: str = ""
    result: dict | None = None
    error: str | None = None
    created_at: float
    updated_at: float


@dataclass
class Job:
    job_id: str
    state: str = "pending"
    progress: float = 0.0
    message: str = ""
    result: dict | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    log: list[str] = field(default_factory=list)
    _event: asyncio.Event = field(default_factory=asyncio.Event)

    def update(self, *, progress: float | None = None, message: str | None = None) -> None:
        if progress is not None:
            self.progress = progress
        if message is not None:
            self.message = message
            self.log.append(f"[{time.strftime('%H:%M:%S')}] {message}")
        self.updated_at = time.time()
        self._event.set()
        self._event.clear()

    def finish(self, result: dict) -> None:
        self.state = "done"
        self.progress = 1.0
        self.result = result
        self.updated_at = time.time()
        self.log.append(f"[{time.strftime('%H:%M:%S')}] ✓ Done")
        self._event.set()

    def fail(self, error: str) -> None:
        self.state = "error"
        self.error = error
        self.updated_at = time.time()
        self.log.append(f"[{time.strftime('%H:%M:%S')}] ✗ {error}")
        self._event.set()

    def to_status(self) -> JobStatus:
        return JobStatus(
            job_id=self.job_id,
            state=self.state,
            progress=self.progress,
            message=self.message,
            result=self.result,
            error=self.error,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class JobManager:
    """In-process job manager."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def submit(
        self,
        coro_factory: Callable[[Job], Coroutine[Any, Any, dict]],
    ) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(job_id=job_id, state="running")
        self._jobs[job_id] = job

        async def _runner() -> None:
            try:
                result = await coro_factory(job)
                job.finish(result)
            except Exception as exc:
                job.fail(str(exc))

        asyncio.create_task(_runner())
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_recent(self, limit: int = 20) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)[:limit]


# Singleton
manager = JobManager()
