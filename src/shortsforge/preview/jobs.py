"""Async job manager — track long-running pipeline jobs with SSE streaming."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


class JobStatus(BaseModel):
    job_id: str
    state: str  # pending | running | done | error | cancelled
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
    _task: asyncio.Task[None] | None = field(default=None, repr=False)

    def update(
        self, *, progress: float | None = None, message: str | None = None
    ) -> None:
        if progress is not None:
            self.progress = progress
        if message is not None:
            self.message = message
            self.log.append(f"[{time.strftime('%H:%M:%S')}] {message}")
        self.updated_at = time.time()
        self._event.set()
        self._event.clear()

    def finish(self, result: dict) -> None:
        if self.state == "cancelled":
            return
        self.state = "done"
        self.progress = 1.0
        self.result = result
        self.updated_at = time.time()
        self.log.append(f"[{time.strftime('%H:%M:%S')}] ✓ Done")
        self._event.set()

    def fail(self, error: str) -> None:
        if self.state == "cancelled":
            return
        self.state = "error"
        self.error = error
        self.updated_at = time.time()
        self.log.append(f"[{time.strftime('%H:%M:%S')}] ✗ {error}")
        self._event.set()

    def mark_cancelled(self, reason: str = "Cancelled by user") -> None:
        self.state = "cancelled"
        self.message = reason
        self.error = reason
        self.updated_at = time.time()
        self.log.append(f"[{time.strftime('%H:%M:%S')}] ⏹ {reason}")
        self._event.set()

    def cancel(self, reason: str = "Cancelled by user") -> bool:
        if self.state not in {"pending", "running"}:
            return False
        if self._task and not self._task.done():
            self._task.cancel()
        self.mark_cancelled(reason)
        return True

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
            except asyncio.CancelledError:
                if job.state != "cancelled":
                    job.mark_cancelled("Cancelled")
            except Exception as exc:
                job.fail(str(exc))

        job._task = asyncio.create_task(_runner())
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str, reason: str = "Cancelled by user") -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        return job.cancel(reason)

    def list_recent(self, limit: int = 20) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)[
            :limit
        ]


# Singleton
manager = JobManager()
