from __future__ import annotations

import asyncio
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from app.services import docker_runner, jacoco   
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
    from app.services import docker_runner, jacoco, java_parser

    # Step 1: ENFORCE a runnable pom.xml — auto-fixes what it safely can
    # (packaging, missing Java version) and actually runs `mvn validate`
    # inside the sandbox to confirm the project builds. Raises ValueError
    # (not just a printed warning) if it's genuinely not runnable, so the
    # caller can surface a clear error instead of proceeding into a doomed
    # generation run.
    fixes = docker_runner.ensure_runnable_pom(root)
    for f in fixes:
        print("pom fix:", f)

    # Step 2: JaCoCo version is chosen to match THIS project's actual Java
    # version, not a single hardcoded version for every project.
    java_version = docker_runner.detect_java_version(root)
    pom = root / "pom.xml"
    if jacoco.inject_jacoco_plugin(pom, java_version=java_version):
        print(f"JaCoCo plugin injected/updated in pom.xml for Java {java_version}")

    docker_runner.ensure_workspace_writable(root)

    # Step 3: report what will actually be targeted vs excluded, instead of
    # silently feeding every .java file (including Swing/AWT, package-info,
    # pure interfaces) into generation.
    targets = java_parser.list_generation_targets(root)
    excluded = java_parser.list_excluded_classes(root)
    groups = java_parser.group_project_classes(root)
    print(f"Generation targets: {len(targets)} class(es) across {len(groups)} package(s)")
    if excluded:
        print(f"Excluded {len(excluded)} file(s): {excluded}")

    # Step 4: naming-convention analysis is computed here for visibility, and
    # is now ALSO consumed downstream in test_generator._project_context()
    # so it actually influences generated code instead of being discarded.
    naming = java_parser.analyze_naming_conventions(root)
    print(f"Naming summary: {naming}")

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
    
def _first_pom_tag(tag: str, xml_text: str) -> str | None:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", xml_text, re.DOTALL)
    return m.group(1).strip() if m else None


def get_project_info(project_root: Path) -> dict | None:
    """
    Lightweight pom.xml summary shown in the UI after upload/analyze
    (artifact coordinates, detected Java version, class/package/test counts).
    Returns None if there's no pom.xml (upload response still renders, just
    without this panel).
    """
    pom = project_root / "pom.xml"
    if not pom.exists():
        return None

    content = pom.read_text(encoding="utf-8", errors="ignore")

    # Pull <parent> out first so its groupId/version/artifactId don't get
    # mistaken for the project's own — Spring Boot poms almost always
    # inherit groupId/version from spring-boot-starter-parent.
    parent_match = re.search(r"<parent>(.*?)</parent>", content, re.DOTALL)
    parent_group_id = parent_artifact_id = parent_version = None
    without_parent = content
    if parent_match:
        parent_block = parent_match.group(1)
        parent_group_id = _first_pom_tag("groupId", parent_block)
        parent_artifact_id = _first_pom_tag("artifactId", parent_block)
        parent_version = _first_pom_tag("version", parent_block)
        without_parent = content[: parent_match.start()] + content[parent_match.end() :]

    group_id = _first_pom_tag("groupId", without_parent) or parent_group_id
    artifact_id = _first_pom_tag("artifactId", without_parent)
    version = _first_pom_tag("version", without_parent) or parent_version
    name = _first_pom_tag("name", without_parent)
    packaging = _first_pom_tag("packaging", without_parent) or "jar"

    is_spring_boot = bool(
        parent_artifact_id and "spring-boot-starter-parent" in parent_artifact_id
    ) or "spring-boot" in without_parent

    from app.services import java_parser

    java_version = docker_runner.detect_java_version(project_root)
    static_stats = java_parser.compute_static_stats(project_root)

    return {
        "name": name or artifact_id,
        "group_id": group_id,
        "artifact_id": artifact_id,
        "version": version,
        "packaging": packaging,
        "java_version": java_version,
        "is_spring_boot": is_spring_boot,
        "spring_boot_version": parent_version if is_spring_boot else None,
        "class_count": static_stats.class_count,
        "package_count": static_stats.package_count,
        "test_count": static_stats.test_count,
    }