from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models.schemas import (
    CoverageStats,
    GenerationBreakdown,
    GenerationResult,
    GenerateResponse,
    ProjectStats,
)
from app.services import test_generator
from app.services.resource_guard import check_host_memory


@dataclass
class GenerationJob:
    job_id: str
    session_id: str
    scope: str
    status: str = "pending"  # pending | running | completed | aborted | failed
    current_index: int = 0
    total: int = 0
    current_class: str = ""
    message: str = ""
    results: list[GenerationResult] = field(default_factory=list)
    breakdown: GenerationBreakdown | None = None
    stats: ProjectStats | None = None
    maven_output_tail: str = ""
    error: str | None = None
    abort_requested: bool = False


_jobs: dict[str, GenerationJob] = {}


def create_job(session_id: str, scope: str) -> GenerationJob:
    job_id = str(uuid.uuid4())
    job = GenerationJob(job_id=job_id, session_id=session_id, scope=scope)
    _jobs[job_id] = job
    return job


def get_job(job_id: str) -> GenerationJob | None:
    return _jobs.get(job_id)


def abort_job(job_id: str) -> bool:
    job = _jobs.get(job_id)
    if not job or job.status in {"completed", "aborted", "failed"}:
        return False
    job.abort_requested = True
    job.message = "Abort requested — stopping after current class…"
    return True


def job_to_dict(job: GenerationJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "session_id": job.session_id,
        "scope": job.scope,
        "status": job.status,
        "current_index": job.current_index,
        "total": job.total,
        "current_class": job.current_class,
        "message": job.message,
        "results": [r.model_dump() for r in job.results],
        "breakdown": job.breakdown.model_dump() if job.breakdown else None,
        "stats": job.stats.model_dump() if job.stats else None,
        "maven_output_tail": job.maven_output_tail,
        "error": job.error,
    }


async def run_generation_job(
    job: GenerationJob,
    project_root: Path,
    scope: str,
    class_path_str: str | None,
    class_paths: list[str] | None,
    coverage: CoverageStats | None,
    model: str,
) -> None:
    job.status = "running"

    def on_progress(index: int, total: int, class_path: str, message: str) -> None:
        job.current_index = index
        job.total = total
        job.current_class = class_path
        job.message = message

    def should_abort() -> bool:
        return job.abort_requested

    try:
        results, breakdown, maven_tail = await test_generator.generate_tests_for_scope(
            project_root,
            scope,
            class_path_str,
            coverage,
            model=model,
            class_paths=class_paths,
            on_progress=on_progress,
            should_abort=should_abort,
        )
        job.results = results
        job.breakdown = breakdown
        job.maven_output_tail = maven_tail
        job.stats = await test_generator.build_project_stats(project_root, analyzed=True)
        if job.abort_requested:
            job.status = "aborted"
            job.message = f"Aborted after {len(results)} of {job.total} class(es)."
        else:
            job.status = "completed"
            job.message = f"Completed {len(results)} class(es)."
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        job.message = str(e)


async def start_job_background(
    job: GenerationJob,
    project_root: Path,
    scope: str,
    class_path_str: str | None,
    class_paths: list[str] | None,
    coverage: CoverageStats | None,
    model: str,
) -> None:
    asyncio.create_task(
        run_generation_job(
            job, project_root, scope, class_path_str, class_paths, coverage, model
        )
    )
