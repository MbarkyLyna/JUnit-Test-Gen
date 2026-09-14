from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

from app.models.schemas import (
    AnalyzeResponse,
    CloneRequest,
    GenerateJobStartResponse,
    GenerateJobStatus,
    GenerateRequest,
    GenerateResponse,
    ProjectStats,
    SessionStatusResponse,
    UploadResponse,
)
from app.services import generation_jobs, jacoco, test_generator
from app.services.generation_jobs import abort_job, create_job, get_job, job_to_dict, run_generation_job
from app.services.java_parser import compute_static_stats
from app.services.ollama_client import DEFAULT_MODEL
from app.services.resource_guard import check_host_memory
from app.services.session_tracker import get_session_status
from app.services.workspace import (
    build_file_tree,
    clone_github_repo,
    create_session,
    extract_zip,
    get_project_info,
)

router = APIRouter(prefix="/api")

_session_meta: dict[str, dict] = {}


def _get_project_root(session_id: str) -> Path:
    meta = _session_meta.get(session_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Session not found")
    return Path(meta["project_root"])


def _build_upload_response(session_id: str, project_root: Path) -> UploadResponse:
    static = compute_static_stats(project_root)
    stats = ProjectStats(static=static, coverage=None, analyzed=False)
    tree = build_file_tree(project_root)
    return UploadResponse(
        session_id=session_id,
        tree=tree,
        stats=stats,
        project_root=str(project_root),
        project_info=get_project_info(project_root),
    )


async def _ensure_coverage(session_id: str, project_root: Path):
    meta = _session_meta[session_id]
    coverage = None
    if meta.get("analyzed"):
        report = jacoco.find_jacoco_report(project_root)
        if report:
            coverage = jacoco.parse_jacoco_report(report)
    else:
        pom = project_root / "pom.xml"
        if pom.exists():
            jacoco.inject_jacoco_plugin(pom)
            jacoco.run_maven_tests(
                project_root,
                session_id=session_id,
                activity_message="Running Maven tests for coverage baseline",
            )
            report = jacoco.find_jacoco_report(project_root)
            if report:
                coverage = jacoco.parse_jacoco_report(report)
            meta["analyzed"] = True
    return coverage


def _validate_generate_scope(body: GenerateRequest) -> None:
    if body.scope not in {"class", "classes", "project"}:
        raise HTTPException(
            status_code=400,
            detail="scope must be 'class', 'classes', or 'project'",
        )
    if body.scope == "class" and not body.class_path:
        raise HTTPException(status_code=400, detail="class_path required for class scope")
    if body.scope == "classes" and not body.class_paths:
        raise HTTPException(status_code=400, detail="class_paths required for classes scope")


def _require_ram_for_batch(scope: str) -> None:
    if scope in {"classes", "project"}:
        ok, msg, _ = check_host_memory()
        if not ok:
            raise HTTPException(status_code=503, detail=msg)


@router.post("/upload", response_model=UploadResponse)
async def upload_project(file: UploadFile = File(...)) -> UploadResponse:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Please upload a .zip file")

    session_id = create_session()
    content = await file.read()
    project_root = extract_zip(session_id, content)

    _session_meta[session_id] = {
        "project_root": str(project_root),
        "analyzed": False,
    }

    return _build_upload_response(session_id, project_root)


@router.post("/clone", response_model=UploadResponse)
async def clone_project(body: CloneRequest) -> UploadResponse:
    session_id = create_session()
    try:
        project_root = clone_github_repo(session_id, body.repo_url, body.branch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    _session_meta[session_id] = {
        "project_root": str(project_root),
        "analyzed": False,
    }

    return _build_upload_response(session_id, project_root)


@router.post("/analyze/{session_id}", response_model=AnalyzeResponse)
async def analyze_project(session_id: str) -> AnalyzeResponse:
    if session_id not in _session_meta:
        raise HTTPException(status_code=404, detail="Session not found")

    project_root = Path(_session_meta[session_id]["project_root"])
    pom = project_root / "pom.xml"
    if not pom.exists():
        raise HTTPException(status_code=400, detail="No pom.xml found in project")

    jacoco.inject_jacoco_plugin(pom)
    exit_code, maven_out = jacoco.run_maven_tests(
        project_root,
        session_id=session_id,
        activity_message="Running Maven tests for coverage analysis",
    )

    report = jacoco.find_jacoco_report(project_root)
    coverage = jacoco.parse_jacoco_report(report) if report else None

    static = compute_static_stats(project_root)
    stats = ProjectStats(static=static, coverage=coverage, analyzed=True)
    _session_meta[session_id]["analyzed"] = True

    tail = maven_out[-4000:]
    if exit_code != 0:
        tail = f"[Maven exit code: {exit_code}]\n" + tail

    return AnalyzeResponse(
        session_id=session_id,
        stats=stats,
        maven_output_tail=tail,
        project_info=get_project_info(project_root),
    )


@router.post("/generate/{session_id}", response_model=GenerateResponse)
async def generate_tests(session_id: str, body: GenerateRequest) -> GenerateResponse:
    if session_id not in _session_meta:
        raise HTTPException(status_code=404, detail="Session not found")

    _validate_generate_scope(body)
    _require_ram_for_batch(body.scope)

    meta = _session_meta[session_id]
    project_root = Path(meta["project_root"])
    coverage = await _ensure_coverage(session_id, project_root)

    results, breakdown, maven_tail = await test_generator.generate_tests_for_scope(
        project_root,
        body.scope,
        body.class_path,
        coverage,
        class_paths=body.class_paths,
        session_id=session_id,
    )

    stats = await test_generator.build_project_stats(project_root, analyzed=True)

    return GenerateResponse(
        session_id=session_id,
        results=results,
        breakdown=breakdown,
        stats=stats,
        maven_output_tail=maven_tail,
    )


@router.post("/generate/{session_id}/start", response_model=GenerateJobStartResponse)
async def start_generate_job(
    session_id: str,
    body: GenerateRequest,
    background_tasks: BackgroundTasks,
) -> GenerateJobStartResponse:
    if session_id not in _session_meta:
        raise HTTPException(status_code=404, detail="Session not found")

    _validate_generate_scope(body)
    _require_ram_for_batch(body.scope)

    meta = _session_meta[session_id]
    project_root = Path(meta["project_root"])
    coverage = await _ensure_coverage(session_id, project_root)

    job = create_job(session_id, body.scope)
    if body.scope == "class":
        job.total = 1
    elif body.scope == "classes":
        job.total = len(body.class_paths or [])
    elif body.scope == "project":
        from app.services import java_parser

        # NOTE: changed from list_main_classes() to list_generation_targets().
        # test_generator.generate_tests_for_scope() was updated to actually
        # SKIP Swing/AWT classes, package-info/module-info, and pure
        # interfaces for scope="project" (see java_parser.py Step 3 changes).
        # Leaving this as list_main_classes() would have inflated job.total
        # with classes that never actually get processed, making the
        # progress bar in the UI wrong (it would stall short of 100%).
        job.total = len(java_parser.list_generation_targets(project_root))
    job.message = "Job queued"

    background_tasks.add_task(
        run_generation_job,
        job,
        project_root,
        body.scope,
        body.class_path,
        body.class_paths,
        coverage,
        DEFAULT_MODEL,
    )

    return GenerateJobStartResponse(
        job_id=job.job_id,
        session_id=session_id,
        scope=body.scope,
        message="Generation job started",
    )


@router.get("/generate/jobs/{job_id}", response_model=GenerateJobStatus)
async def get_generate_job(job_id: str) -> GenerateJobStatus:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    data = job_to_dict(job)
    return GenerateJobStatus(**data)


@router.post("/generate/jobs/{job_id}/abort")
async def abort_generate_job(job_id: str) -> dict:
    if not abort_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found or already finished")
    return {"job_id": job_id, "status": "abort_requested"}


@router.get("/status/{session_id}", response_model=SessionStatusResponse)
async def session_status(session_id: str) -> SessionStatusResponse:
    if session_id not in _session_meta:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionStatusResponse(**get_session_status(session_id))


@router.get("/health")
async def health() -> dict:
    from app.services.ollama_client import check_ollama_available

    ollama_ok = await check_ollama_available()
    ram_ok, ram_msg, avail_gb = check_host_memory()
    return {
        "status": "ok",
        "ollama_available": ollama_ok,
        "model": DEFAULT_MODEL,
        "ram_ok": ram_ok,
        "ram_message": ram_msg,
        "available_ram_gb": round(avail_gb, 2),
    }