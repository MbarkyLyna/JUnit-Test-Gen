from __future__ import annotations

import asyncio
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from app.models.schemas import FileNode

WORKSPACES_DIR = Path(__file__).resolve().parents[2] / "workspaces"
WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)

CLONE_TIMEOUT_SECONDS = int(os.environ.get("GIT_CLONE_TIMEOUT_SECONDS", "120"))

_sessions: dict[str, Path] = {}


def create_session() -> str:
    import uuid

    session_id = str(uuid.uuid4())
    session_dir = WORKSPACES_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    _sessions[session_id] = session_dir
    return session_id


def get_session_path(session_id: str) -> Path | None:
    if session_id in _sessions:
        return _sessions[session_id]
    candidate = WORKSPACES_DIR / session_id
    if candidate.exists():
        _sessions[session_id] = candidate
        return candidate
    return None


def _prepare_project_dir(session_id: str) -> Path:
    session_dir = get_session_path(session_id)
    if session_dir is None:
        raise ValueError(f"Unknown session: {session_id}")

    project_dir = session_dir / "project"
    if project_dir.exists():
        import shutil

        shutil.rmtree(project_dir)
    project_dir.mkdir(parents=True)
    return project_dir


def _finalize_project_root(project_dir: Path) -> Path:
    root = find_maven_root(project_dir)
    from app.services import docker_runner

    docker_runner.ensure_workspace_writable(root)
    return root


def extract_zip(session_id: str, zip_bytes: bytes) -> Path:
    import shutil
    import zipfile

    session_dir = get_session_path(session_id)
    if session_dir is None:
        raise ValueError(f"Unknown session: {session_id}")

    project_dir = _prepare_project_dir(session_id)
    zip_path = session_dir / "upload.zip"
    zip_path.write_bytes(zip_bytes)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(project_dir)

    return _finalize_project_root(project_dir)


def _normalize_github_url(repo_url: str) -> str:
    url = repo_url.strip()
    if not url:
        raise ValueError("Repository URL is required")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https GitHub URLs are supported")

    host = (parsed.netloc or "").lower()
    if host not in {"github.com", "www.github.com"}:
        raise ValueError("Only github.com repository URLs are supported")

    path = parsed.path.strip("/")
    if not path or path.count("/") < 1:
        raise ValueError("Invalid GitHub repository URL")

    return f"https://github.com/{path.removesuffix('.git')}.git"


def clone_github_repo(session_id: str, repo_url: str, branch: str | None = None) -> Path:
    clone_url = _normalize_github_url(repo_url)
    project_dir = _prepare_project_dir(session_id)

    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd.extend(["--branch", branch])
    cmd.extend([clone_url, str(project_dir)])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        raise ValueError(
            f"Git clone timed out after {CLONE_TIMEOUT_SECONDS}s. Try a smaller repository."
        ) from e
    except FileNotFoundError as e:
        raise ValueError("git is not installed or not on PATH") from e

    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        lower = stderr.lower()
        if "authentication" in lower or "could not read username" in lower:
            raise ValueError("Private repository — authentication required. Use a public repo or upload a ZIP.")
        if "not found" in lower or "repository not found" in lower:
            raise ValueError("Repository not found. Check the URL and that it is public.")
        raise ValueError(f"Git clone failed: {stderr[:500]}")

    return _finalize_project_root(project_dir)


def find_maven_root(extract_dir: Path) -> Path:
    """Locate pom.xml, handling common zip layouts (single root folder)."""
    if (extract_dir / "pom.xml").exists():
        return extract_dir

    subdirs = [p for p in extract_dir.iterdir() if p.is_dir()]
    if len(subdirs) == 1 and (subdirs[0] / "pom.xml").exists():
        return subdirs[0]

    for pom in extract_dir.rglob("pom.xml"):
        return pom.parent

    return extract_dir


def build_file_tree(root: Path, base: Path | None = None) -> FileNode:
    base = base or root
    rel = root.relative_to(base).as_posix() if root != base else ""
    name = root.name if root != base else root.name

    if root.is_file():
        return FileNode(name=name, path=rel, type="file")

    children: list[FileNode] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        entries = []

    for entry in entries:
        if entry.name.startswith(".") or entry.name in {"target", "node_modules", ".git"}:
            continue
        children.append(build_file_tree(entry, base))

    return FileNode(name=name, path=rel, type="directory", children=children)


def cleanup_session(session_id: str) -> None:
    import shutil

    session_dir = get_session_path(session_id)
    if session_dir and session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)
    _sessions.pop(session_id, None)
