"""
MCP server exposing the JUnit test generation pipeline as tools, so any
MCP-compatible client (Claude Desktop, Claude Code, or any other MCP client)
can point it at an arbitrary Java/Spring Boot (Maven) project and
generate/verify JUnit tests — without needing this project's own React
frontend or FastAPI HTTP routes.

Built on the official open-source MCP Python SDK (Apache-2.0):
https://github.com/modelcontextprotocol/python-sdk

Install:
    pip install mcp

Run (stdio transport, for Claude Desktop / Claude Code):
    python -m app.mcp_server

Run (SSE transport, for remote/network clients):
    change mcp.run() at the bottom to mcp.run(transport="sse")

Requirements on the machine running this server (unchanged from the web app):
    - Docker installed and running (sandboxed build/test execution)
    - Ollama installed and running, with at least one model pulled
    - The sandbox Docker image already built (see docker_runner.DOCKER_IMAGE)
"""
from __future__ import annotations

import subprocess
import uuid
import zipfile
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.services import jacoco, java_parser, ollama_client, test_generator

WORKSPACES_ROOT = Path("backend/workspaces_mcp")
WORKSPACES_ROOT.mkdir(parents=True, exist_ok=True)

# Session store is in-memory and per-process, same limitation as the web
# app's session handling — restarting this server clears all sessions.
_sessions: dict[str, Path] = {}

mcp = FastMCP("junit-test-generator")


def _get_project_root(session_id: str) -> Path:
    if session_id not in _sessions:
        raise ValueError(
            f"Unknown session_id: {session_id}. Call clone_project or "
            "upload_project first to create a session."
        )
    return _sessions[session_id]


def _find_pom_root(search_dir: Path) -> Path:
    pom = next(search_dir.glob("**/pom.xml"), None)
    if pom is None:
        raise ValueError(
            "No pom.xml found — this tool currently only supports Maven-based "
            "Java/Spring Boot projects."
        )
    return pom.parent


@mcp.tool()
def clone_project(repo_url: str, branch: str = "main") -> dict[str, Any]:
    """
    Clone a public Git repository containing a Maven-based Java/Spring Boot
    project. Returns a session_id to use with the other tools, plus basic
    structural stats (class count, package count, existing @Test count).
    """
    session_id = str(uuid.uuid4())
    workspace = WORKSPACES_ROOT / session_id / "project"
    workspace.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        ["git", "clone", "--branch", branch, "--depth", "1", repo_url, str(workspace)],
        check=True,
        capture_output=True,
        text=True,
    )

    project_root = _find_pom_root(workspace)
    _sessions[session_id] = project_root

    stats = java_parser.compute_static_stats(project_root)
    return {
        "session_id": session_id,
        "class_count": stats.class_count,
        "package_count": stats.package_count,
        "test_count": stats.test_count,
    }


@mcp.tool()
def upload_project(zip_path: str) -> dict[str, Any]:
    """
    Extract a local .zip file containing a Maven-based Java/Spring Boot
    project. zip_path must be a filesystem path accessible to this server
    (not the calling client's machine, if they differ). Returns a
    session_id, same shape as clone_project.
    """
    session_id = str(uuid.uuid4())
    workspace = WORKSPACES_ROOT / session_id / "project"
    workspace.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(workspace)

    project_root = _find_pom_root(workspace)
    _sessions[session_id] = project_root

    stats = java_parser.compute_static_stats(project_root)
    return {
        "session_id": session_id,
        "class_count": stats.class_count,
        "package_count": stats.package_count,
        "test_count": stats.test_count,
    }


@mcp.tool()
def analyze_coverage(session_id: str) -> dict[str, Any]:
    """
    Run the project's existing test suite inside a sandboxed Docker
    container with JaCoCo instrumentation, and return real line/branch
    coverage percentages, overall and per class. Always run this before
    generate_test_for_class / generate_tests_for_project, so generation is
    grounded in real, current coverage data.
    """
    project_root = _get_project_root(session_id)
    pom_path = project_root / "pom.xml"
    jacoco.inject_jacoco_plugin(pom_path)

    exit_code, output = jacoco.run_maven_tests(project_root, session_id=session_id)
    report = jacoco.find_jacoco_report(project_root)
    if not report:
        return {
            "analyzed": False,
            "build_succeeded": exit_code == 0,
            "error": "No JaCoCo report produced.",
            "maven_tail": output[-2000:],
        }

    coverage = jacoco.parse_jacoco_report(report)
    return {
        "analyzed": True,
        "build_succeeded": exit_code == 0,
        "line_coverage_pct": coverage.line_coverage_pct,
        "branch_coverage_pct": coverage.branch_coverage_pct,
        "per_class": coverage.per_class,
    }


@mcp.tool()
async def generate_test_for_class(
    session_id: str,
    class_path: str,
    model: str = "phi4-mini",
) -> dict[str, Any]:
    """
    Generate a JUnit 5 test for a single class in the project. class_path
    can be a path relative to the project root (e.g.
    'src/main/java/com/example/Owner.java') or a fully qualified class name
    (e.g. 'com.example.Owner'). Runs the full generate -> verify -> retry
    loop (up to 3 attempts, each fed the real Maven compiler error on
    failure) and returns whether it ultimately compiled/passed, plus
    before/after coverage for that class. Call analyze_coverage first for
    accurate before/after numbers.
    """
    project_root = _get_project_root(session_id)
    report = jacoco.find_jacoco_report(project_root)
    coverage = jacoco.parse_jacoco_report(report) if report else None

    target = test_generator._resolve_class_path(project_root, class_path)
    result = await test_generator.generate_for_class(
        target, project_root, coverage, model=model, session_id=session_id
    )
    return {
        "class_name": result.class_name,
        "status": result.status,
        "tests_passed": result.tests_passed,
        "initial_coverage_pct": result.initial_coverage_pct,
        "final_coverage_pct": result.final_coverage_pct,
        "coverage_delta": result.coverage_delta,
        "message": result.message,
        "test_source": result.test_source,
    }


@mcp.tool()
async def generate_tests_for_project(
    session_id: str,
    model: str = "phi4-mini",
) -> dict[str, Any]:
    """
    Generate JUnit tests across the whole project, prioritizing the
    lowest-coverage classes first, stopping once 80% overall line coverage
    is reached or every class has been attempted. Returns a per-class
    breakdown of how many classes reached full/partial coverage or failed
    to compile.
    """
    project_root = _get_project_root(session_id)
    report = jacoco.find_jacoco_report(project_root)
    coverage = jacoco.parse_jacoco_report(report) if report else None

    results, breakdown, _ = await test_generator.generate_tests_for_scope(
        project_root,
        scope="project",
        class_path_str=None,
        coverage=coverage,
        model=model,
    )
    return {
        "summary": breakdown.summary_headline,
        "full_coverage_count": breakdown.full_coverage_count,
        "partial_coverage_count": breakdown.partial_coverage_count,
        "failed_compile_count": breakdown.failed_compile_count,
        "results": [
            {
                "class_name": r.class_name,
                "status": r.status,
                "final_coverage_pct": r.final_coverage_pct,
            }
            for r in results
        ],
    }


@mcp.tool()
async def list_available_models() -> list[str]:
    """List Ollama models currently pulled and available on this machine."""
    return await ollama_client.list_models()


if __name__ == "__main__":
    mcp.run()