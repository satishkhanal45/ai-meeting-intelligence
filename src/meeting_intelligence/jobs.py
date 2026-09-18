"""In-process job registry for transcript processing.

``POST /api/process`` used to run the whole pipeline inline, holding an HTTP
connection open for minutes. Any reverse proxy's default timeout would kill
it, the work could not be polled or cancelled, and the client had nothing to
show but a placeholder progress bar.

Jobs here run as asyncio tasks in the API process and are observable while
they run. This is deliberately not a distributed queue: it removes the
long-held connection without adding a broker. Swapping the store for Redis
and the task for a worker is a contained change when multiple workers are
needed -- the API surface would not move.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional

from meeting_intelligence.logger import get_logger
from meeting_intelligence.pipeline import Progress, overall_fraction
from meeting_intelligence.utils import generate_id

logger = get_logger(__name__)

#: Finished jobs are kept this long so a client that reconnects can still read
#: the outcome, then reaped to bound memory.
JOB_RETENTION_SECONDS = 3600

#: Upper bound on retained jobs, whatever their age.
MAX_JOBS = 500


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    """One transcript-processing run."""

    id: str
    status: JobStatus = JobStatus.QUEUED
    stage: str = "queued"
    message: str = ""
    completed: int = 0
    total: int = 0
    fraction: float = 0.0
    meeting_id: Optional[str] = None
    error: Optional[str] = None
    error_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: _utc_now())
    finished_at: Optional[str] = None
    _created_monotonic: float = field(default_factory=time.monotonic)
    _task: Optional[asyncio.Task] = field(default=None, repr=False)
    #: Bumped on every change so listeners can wait for the next update
    #: instead of polling on a timer.
    _revision: int = 0
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def terminal(self) -> bool:
        return self.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "status": str(self.status),
            "stage": self.stage,
            "message": self.message,
            "completed": self.completed,
            "total": self.total,
            "fraction": round(self.fraction, 4),
            "meeting_id": self.meeting_id,
            "error": self.error,
            "error_id": self.error_id,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }

    def _touch(self) -> None:
        self._revision += 1
        self._event.set()
        self._event = asyncio.Event()

    def apply_progress(self, progress: Progress) -> None:
        self.status = JobStatus.RUNNING
        self.stage = progress.stage
        self.message = progress.message
        self.completed = progress.completed
        self.total = progress.total

        # A progress bar must never run backwards, so the reported fraction is
        # clamped to its high-water mark. Any decrease is a bug in the stage
        # accounting, so log it rather than hiding it entirely.
        computed = overall_fraction(progress.stage, progress.fraction)
        if computed < self.fraction:
            logger.debug(
                "Progress decreased; clamping",
                extra={
                    "job_id": self.id,
                    "stage": progress.stage,
                    "computed": round(computed, 4),
                    "held": round(self.fraction, 4),
                },
            )
        self.fraction = max(self.fraction, computed)
        self._touch()

    def finish_success(self, meeting_id: str) -> None:
        self.status = JobStatus.SUCCEEDED
        self.stage = "done"
        self.message = "Processing complete"
        self.fraction = 1.0
        self.meeting_id = meeting_id
        self.finished_at = _utc_now()
        self._touch()

    def finish_failure(self, message: str, error_id: Optional[str] = None) -> None:
        self.status = JobStatus.FAILED
        self.stage = "failed"
        self.message = "Processing failed"
        self.error = message
        self.error_id = error_id
        self.finished_at = _utc_now()
        self._touch()

    def finish_cancelled(self) -> None:
        self.status = JobStatus.CANCELLED
        self.stage = "cancelled"
        self.message = "Cancelled"
        self.finished_at = _utc_now()
        self._touch()

    async def wait_for_change(self, timeout: float) -> bool:
        """Block until this job changes, or *timeout* elapses."""
        event = self._event
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return True
        except (asyncio.TimeoutError, TimeoutError):
            return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class JobRegistry:
    """Holds jobs for the lifetime of the process."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self) -> Job:
        self._reap()
        job = Job(id=generate_id())
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    async def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.terminal:
            return False
        if job._task is not None:
            job._task.cancel()
        job.finish_cancelled()
        return True

    def _reap(self) -> None:
        """Drop finished jobs that are older than the retention window."""
        now = time.monotonic()
        stale = [
            job_id
            for job_id, job in self._jobs.items()
            if job.terminal and now - job._created_monotonic > JOB_RETENTION_SECONDS
        ]
        for job_id in stale:
            del self._jobs[job_id]

        if len(self._jobs) > MAX_JOBS:
            finished = sorted(
                (j for j in self._jobs.values() if j.terminal),
                key=lambda j: j._created_monotonic,
            )
            for job in finished[: len(self._jobs) - MAX_JOBS]:
                self._jobs.pop(job.id, None)

    async def shutdown(self) -> None:
        """Cancel everything still running, for a clean process exit."""
        running = [j for j in self._jobs.values() if not j.terminal and j._task is not None]
        for job in running:
            job._task.cancel()
        for job in running:
            try:
                await job._task
            except (asyncio.CancelledError, Exception):  # noqa: B014 - best effort
                pass


registry = JobRegistry()
