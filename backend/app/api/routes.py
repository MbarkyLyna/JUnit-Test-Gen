from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.models.schemas import (
    AnalyzeResponse,
    GenerateRequest,
    GenerateResponse,
    ProjectStats,
    UploadResponse,
)
from app.services import jacoco, test_generator
from app.services.java_parser import compute_static_stats
from app.services.workspace import (
    build_file_tree,
    create_session,
    extract_zip,
    get_session_path,
)

router = APIRouter(prefix="/api")

# In-memory session metadata
_session_meta: dict[str, dict] = {}


def _get_project_root(session_id: str) -> Path:
    meta = _session_meta.get(session_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Session not found")
    return Path(meta["project_root"])


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

    static = compute_static_stats(project_root)
    stats = ProjectStats(static=static, coverage=None, analyzed=False)
    tree = build_file_tree(project_root)

    return UploadResponse(
        session_id=session_id,
        tree=tree,
        stats=stats,
        project_root=str(project_root),
    )


@router.post("/analyze/{session_id}", response_model=AnalyzeResponse)
async def analyze_project(session_id: str) -> AnalyzeResponse:
    if session_id not in _session_meta:
        raise HTTPException(status_code=404, detail="Session not found")

    project_root = Path(_session_meta[session_id]["project_root"])
    pom = project_root / "pom.xml"
    if not pom.exists():
        raise HTTPException(status_code=400, detail="No pom.xml found in project")

    jacoco.inject_jacoco_plugin(pom)
    exit_code, maven_out = jacoco.run_maven_tests(project_root)

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
    )


@router.post("/generate/{session_id}", response_model=GenerateResponse)
async def generate_tests(session_id: str, body: GenerateRequest) -> GenerateResponse:
    if session_id not in _session_meta:
        raise HTTPException(status_code=404, detail="Session not found")

    meta = _session_meta[session_id]
    project_root = Path(meta["project_root"])

    if body.scope not in {"class", "project"}:
        raise HTTPException(status_code=400, detail="scope must be 'class' or 'project'")

    if body.scope == "class" and not body.class_path:
        raise HTTPException(status_code=400, detail="class_path required for class scope")

    # Ensure analyzed for coverage data
    coverage = None
    if meta.get("analyzed"):
        report = jacoco.find_jacoco_report(project_root)
        if report:
            coverage = jacoco.parse_jacoco_report(report)
    else:
        pom = project_root / "pom.xml"
        if pom.exists():
            jacoco.inject_jacoco_plugin(pom)
            jacoco.run_maven_tests(project_root)
            report = jacoco.find_jacoco_report(project_root)
            if report:
                coverage = jacoco.parse_jacoco_report(report)
            meta["analyzed"] = True

    results, breakdown, maven_tail = await test_generator.generate_tests_for_scope(
        project_root, body.scope, body.class_path, coverage
    )

    stats = await test_generator.build_project_stats(project_root, analyzed=True)

    return GenerateResponse(
        session_id=session_id,
        results=results,
        breakdown=breakdown,
        stats=stats,
        maven_output_tail=maven_tail,
    )


@router.get("/health")
async def health() -> dict:
    from app.services.ollama_client import check_ollama_available

    ollama_ok = await check_ollama_available()
    return {"status": "ok", "ollama_available": ollama_ok}
