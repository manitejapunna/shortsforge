from __future__ import annotations

import asyncio

import pytest

from shortsforge.preview.jobs import JobManager


@pytest.mark.asyncio
async def test_cancel_running_job_marks_cancelled() -> None:
    manager = JobManager()

    def factory(_job):
        async def _coro():
            await asyncio.sleep(5)
            return {"ok": True}

        return _coro()

    job = manager.submit(factory)
    await asyncio.sleep(0)

    cancelled = manager.cancel(job.job_id)
    await asyncio.sleep(0)

    assert cancelled is True
    assert job.state == "cancelled"
    assert job.error == "Cancelled by user"


@pytest.mark.asyncio
async def test_cancel_done_job_is_noop() -> None:
    manager = JobManager()

    def factory(_job):
        async def _coro():
            await asyncio.sleep(0)
            return {"ok": True}

        return _coro()

    job = manager.submit(factory)
    await asyncio.sleep(0.05)

    cancelled = manager.cancel(job.job_id)

    assert job.state == "done"
    assert cancelled is False
