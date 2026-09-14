from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

# Legacy single-image fallback, kept for any caller that doesn't specify a
# java-version-specific image (e.g. the plain write-permission probe).
DOCKER_IMAGE = "spring-test-gen-sandbox:latest"
DEFAULT_TIMEOUT = int(os.environ.get("MAVEN_TIMEOUT_SECONDS", "180"))
DEFAULT_JAVA_VERSION = 17

# One sandbox image per JDK major version. These images must actually be built
# (see docker/Dockerfile.sandbox — build with --build-arg JAVA_VERSION=<n> and
# tag accordingly). If the exact major isn't available, resolve_docker_image()
# picks the closest image at or above the project's requirement.
DOCKER_IMAGES: dict[int, str] = {
    8: "spring-test-gen-sandbox:jdk8",
    11: "spring-test-gen-sandbox:jdk11",
    17: "spring-test-gen-sandbox:jdk17",
    21: "spring-test-gen-sandbox:jdk21",
}

JAVA_VERSION_TAG_RE = re.compile(
    r"<maven\.compiler\.(?:source|release)>\s*(1\.)?(\d+)\s*</maven\.compiler\.(?:source|release)>"
)
JAVA_VERSION_PROPERTY_RE = re.compile(r"<java\.version>\s*(1\.)?(\d+)\s*</java\.version>")


# ===== Java version / image selection =====
def detect_java_version(project_root: Path) -> int:
    """Read the project's declared Java version from pom.xml. Defaults to
    DEFAULT_JAVA_VERSION if nothing is declared (also true for a project
    missing a pom entirely, since ensure_runnable_pom will inject one)."""
    pom = project_root / "pom.xml"
    if not pom.exists():
        return DEFAULT_JAVA_VERSION
    content = pom.read_text(encoding="utf-8", errors="ignore")
    for pattern in (JAVA_VERSION_TAG_RE, JAVA_VERSION_PROPERTY_RE):
        m = pattern.search(content)
        if m:
            return int(m.group(2))
    return DEFAULT_JAVA_VERSION


def resolve_docker_image(java_version: int) -> str:
    """Pick the closest sandbox image built for a JDK at or above the
    project's required version (a newer JDK can compile older source)."""
    available = sorted(DOCKER_IMAGES.keys())
    for v in available:
        if java_version <= v:
            return DOCKER_IMAGES[v]
    return DOCKER_IMAGES[available[-1]]


# ===== Project validation (kept for diagnostics/reporting) =====
def validate_maven_project(project_root: Path) -> list[str]:
    """Return a list of warnings/errors; empty means all clear. Non-fixing —
    use ensure_runnable_pom() when you need enforcement, not just a report."""
    issues = []
    pom = project_root / "pom.xml"
    if not pom.exists():
        issues.append("pom.xml not found")
        return issues

    content = pom.read_text(encoding="utf-8")

    if "<packaging>" not in content:
        issues.append("No <packaging> element found; assuming jar packaging.")

    if "jacoco-maven-plugin" not in content:
        issues.append("JaCoCo plugin not found; will be injected automatically.")

    m = re.search(r"<maven\.compiler\.source>(.*?)</maven\.compiler\.source>", content)
    if m:
        java_version = m.group(1).strip()
        if java_version.startswith("1."):
            java_version = java_version[2:]
        try:
            ver = int(java_version)
            if ver < 8:
                issues.append(f"Java version {ver} < 8; JaCoCo may not work correctly.")
        except ValueError:
            pass

    return issues


def _inject_packaging(content: str) -> tuple[str, bool]:
    """
    Insert <packaging>jar</packaging> as a direct child of <project>.

    IMPORTANT: a naive "replace the first </version>" is wrong whenever the
    pom has a <parent> block (e.g. spring-boot-starter-parent) — that block
    also contains a </version> tag, and it comes BEFORE the project's own
    </version>. Blindly replacing the first occurrence drops <packaging>
    *inside* <parent>, which is not a valid child there and breaks the pom
    ("Unrecognised tag: 'packaging'"). Anchor on </parent> when present, and
    only fall back to the </version> heuristic when there's no parent block
    to get confused by.
    """
    if "<packaging>" in content:
        return content, False

    parent_match = re.search(r"</parent>", content)
    if parent_match:
        insert_at = parent_match.end()
        new_content = (
            content[:insert_at]
            + "\n    <packaging>jar</packaging>"
            + content[insert_at:]
        )
        return new_content, True

    if "</version>" in content:
        new_content = content.replace(
            "</version>", "</version>\n    <packaging>jar</packaging>", 1
        )
        return new_content, True

    return content, False


def ensure_runnable_pom(project_root: Path) -> list[str]:
    """
    ENFORCES a runnable pom.xml instead of just warning:
      1. Fails loudly (ValueError) if pom.xml is missing entirely — this tool
         does not guess a project's groupId/dependencies from nothing.
      2. Auto-injects <packaging>jar</packaging> if absent.
      3. Auto-injects a default Java version property if none is declared.
      4. Actually runs `mvn validate` inside the matching sandbox image to
         confirm the pom parses and dependencies resolve — a real build
         check, not a text-pattern guess.
    Returns the list of fixes/notes applied. Raises ValueError if the pom is
    missing or fails validation even after auto-fixes.
    """
    pom = project_root / "pom.xml"
    if not pom.exists():
        raise ValueError(
            "pom.xml not found at project root. This tool requires an existing "
            "Maven project — it does not scaffold one from scratch. Upload a "
            "project that includes pom.xml."
        )

    content = pom.read_text(encoding="utf-8", errors="ignore")
    fixes: list[str] = []

    content, packaging_injected = _inject_packaging(content)
    if packaging_injected:
        fixes.append("Injected missing <packaging>jar</packaging>.")

    if not JAVA_VERSION_TAG_RE.search(content) and not JAVA_VERSION_PROPERTY_RE.search(content):
        block = (
            f"\n        <maven.compiler.source>{DEFAULT_JAVA_VERSION}</maven.compiler.source>"
            f"\n        <maven.compiler.target>{DEFAULT_JAVA_VERSION}</maven.compiler.target>"
        )
        if "<properties>" in content:
            content = content.replace("<properties>", f"<properties>{block}", 1)
        else:
            content = content.replace(
                "</project>",
                f"    <properties>{block}\n    </properties>\n</project>",
                1,
            )
        fixes.append(f"No Java version declared; defaulted to Java {DEFAULT_JAVA_VERSION}.")

    if fixes:
        pom.write_text(content, encoding="utf-8")

    java_version = detect_java_version(project_root)
    image = resolve_docker_image(java_version)

    ensure_workspace_writable(project_root)
    exit_code, output = run_in_docker(
        project_root,
        args=["validate", "-q"],
        timeout=90,
        docker_image=image,
        activity_message="Validating pom.xml is buildable",
    )
    if exit_code != 0:
        raise ValueError(
            "pom.xml failed Maven validation inside the sandbox (project is not "
            f"runnable as-is). Exit code {exit_code}. Output tail:\n{output[-2000:]}"
        )

    fixes.append(f"Detected Java {java_version}; using sandbox image '{image}'.")
    return fixes


# ===== Existing functions (workspace prep, execution) =====
def ensure_workspace_writable(project_root: Path) -> None:
    try:
        project_root.mkdir(parents=True, exist_ok=True)
        for root, dirs, files in os.walk(project_root):
            for d in dirs:
                try:
                    os.chmod(os.path.join(root, d), 0o777)
                except Exception:
                    pass
            for f in files:
                try:
                    os.chmod(os.path.join(root, f), 0o666)
                except Exception:
                    pass
        try:
            os.chmod(project_root, 0o777)
        except Exception:
            pass
    except Exception:
        pass


def verify_container_write_permission(
    project_root: Path, docker_image: str | None = None
) -> tuple[bool, str]:
    ensure_workspace_writable(project_root)
    abs_path = project_root.resolve().as_posix()
    test_file_relative = "target/.permission_test_marker"
    test_file_host = project_root / "target" / ".permission_test_marker"
    (project_root / "target").mkdir(parents=True, exist_ok=True)

    cmd = [
        "docker", "run", "--rm", "--entrypoint", "sh",
        "--user", "1000:1000",
        "-v", f"{abs_path}:/workspace",
        "-w", "/workspace",
        docker_image or DOCKER_IMAGE,
        "-c", f"echo 'write_test' > /workspace/{test_file_relative}",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if res.returncode == 0 and test_file_host.exists():
            content = test_file_host.read_text(encoding="utf-8").strip()
            try:
                test_file_host.unlink()
            except Exception:
                pass
            if content == "write_test":
                return True, "Container write permission verified successfully."
        err_msg = (res.stderr or res.stdout or "").strip()
        return False, f"Container failed to write to volume (Exit code {res.returncode}): {err_msg}"
    except Exception as e:
        return False, f"Permission check execution failed: {str(e)}"


def run_in_docker(
    project_root: Path,
    args: list[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    session_id: str | None = None,
    activity_message: str = "Running Maven tests inside Docker container sandbox",
    docker_image: str | None = None,
) -> tuple[int, str]:
    if session_id:
        from app.services.session_tracker import end_activity, start_activity
        start_activity(session_id, "docker_maven", activity_message)

    ensure_workspace_writable(project_root)
    args = args or ["test", "-DskipTests=false", "-q"]
    abs_path = project_root.resolve().as_posix()
    image = docker_image or DOCKER_IMAGE

    cmd = [
        "docker", "run", "--rm",
        "--cpus=2", "--memory=2g",
        "--user", "1000:1000",
        "-v", "spring-test-gen-m2-cache:/home/appuser/.m2",
        "-v", f"{abs_path}:/workspace",
        "-w", "/workspace",
        image,
    ] + args

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout or "") + (result.stderr or "")

        with open("full_maven_output.log", "w", encoding="utf-8") as f:
            f.write(output)

        if result.returncode == 137:
            oom_msg = (
                f"\n[DOCKER OOM FAILURE] Docker container exceeded memory limit (2GB) and was killed by OOM killer. "
                f"Peak memory pressure exceeded the 2GB container RAM limit. Output snapshot:\n{output[-1000:]}"
            )
            return 137, oom_msg

        return result.returncode, output
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "")
        with open("full_maven_output.log", "w", encoding="utf-8") as f:
            f.write(out)
        timeout_msg = (
            f"{out}\n[DOCKER TIMEOUT FAILURE] Maven execution exceeded the strict limit of {timeout} seconds inside the container sandbox. "
            f"Execution terminated to preserve host resources."
        )
        return -1, timeout_msg
    except FileNotFoundError:
        return -1, "Docker executable ('docker') not found on system PATH. Docker is strictly required to execute untrusted code."
    except Exception as e:
        return -1, f"Docker daemon execution error: {str(e)}. Sandboxed execution aborted."
    finally:
        if session_id:
            from app.services.session_tracker import end_activity
            end_activity(session_id)