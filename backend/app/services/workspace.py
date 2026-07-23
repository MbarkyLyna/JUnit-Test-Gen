from __future__ import annotations

import shutil
import uuid
import zipfile
from pathlib import Path

from app.models.schemas import FileNode

WORKSPACES_DIR = Path(__file__).resolve().parents[2] / "workspaces"
WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)

_sessions: dict[str, Path] = {}


def create_session() -> str:
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


def extract_zip(session_id: str, zip_bytes: bytes) -> Path:
    session_dir = get_session_path(session_id)
    if session_dir is None:
        raise ValueError(f"Unknown session: {session_id}")

    extract_dir = session_dir / "project"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)

    zip_path = session_dir / "upload.zip"
    zip_path.write_bytes(zip_bytes)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    root = find_maven_root(extract_dir)
    from app.services import docker_runner
    docker_runner.ensure_workspace_writable(root)
    return root


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
    session_dir = get_session_path(session_id)
    if session_dir and session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)
    _sessions.pop(session_id, None)
