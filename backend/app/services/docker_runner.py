from __future__ import annotations

import os
import subprocess
from pathlib import Path

DOCKER_IMAGE = "spring-test-gen-sandbox:latest"
DEFAULT_TIMEOUT = int(os.environ.get("MAVEN_TIMEOUT_SECONDS", "180"))


def ensure_workspace_writable(project_root: Path) -> None:
    """
    Ensure the host's mounted project directory and all subdirectories/files
    are writable by UID 1000 running inside the container.
    """
    try:
        project_root.mkdir(parents=True, exist_ok=True)
        # Attempt to recursively set read/write/execute permissions (0o777)
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


def verify_container_write_permission(project_root: Path) -> tuple[bool, str]:
    """
    Test that the container running as UID 1000 can actually write a generated file back
    to the mounted volume. Returns (success: bool, detail_message: str).
    """
    ensure_workspace_writable(project_root)
    abs_path = project_root.resolve().as_posix()
    test_file_relative = "target/.permission_test_marker"
    test_file_host = project_root / "target" / ".permission_test_marker"

    # Ensure parent target dir exists
    (project_root / "target").mkdir(parents=True, exist_ok=True)

    cmd = [
        "docker",
        "run",
        "--rm",
        "--entrypoint",
        "sh",
        "--user",
        "1000:1000",
        "-v",
        f"{abs_path}:/workspace",
        "-w",
        "/workspace",
        DOCKER_IMAGE,
        "-c",
        f"echo 'write_test' > /workspace/{test_file_relative}",
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
) -> tuple[int, str]:
    """
    Run Maven command strictly inside an isolated non-root Docker container sandbox.
    Resource limits: 2 CPUs, 2GB RAM, non-root user (1000:1000), container destroyed after run (--rm).
    Returns (exit_code, combined_output).
    Handles OOM kills (exit code 137) and timeouts with clear, explicit error messages.
    """
    ensure_workspace_writable(project_root)
    args = args or ["test", "-DskipTests=false", "-q"]
    abs_path = project_root.resolve().as_posix()

    cmd = [
        "docker",
        "run",
        "--rm",
        "--cpus=2",
        "--memory=2g",
        "--user",
        "1000:1000",
        "-v",
        "spring-test-gen-m2-cache:/home/appuser/.m2",
        "-v",
        f"{abs_path}:/workspace",
        "-w",
        "/workspace",
        DOCKER_IMAGE,
    ] + args

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout or "") + (result.stderr or "")

        # Exit code 137 indicates OOM killer (SIGKILL) inside container
        if result.returncode == 137:
            oom_msg = (
                f"\n[DOCKER OOM FAILURE] Docker container exceeded memory limit (2GB) and was killed by OOM killer. "
                f"Peak memory pressure exceeded the 2GB container RAM limit. Output snapshot:\n{output[-1000:]}"
            )
            return 137, oom_msg

        return result.returncode, output
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "")
        timeout_msg = (
            f"{out}\n[DOCKER TIMEOUT FAILURE] Maven execution exceeded the strict limit of {timeout} seconds inside the container sandbox. "
            f"Execution terminated to preserve host resources."
        )
        return -1, timeout_msg
    except FileNotFoundError:
        return -1, "Docker executable ('docker') not found on system PATH. Docker is strictly required to execute untrusted code."
    except Exception as e:
        return -1, f"Docker daemon execution error: {str(e)}. Sandboxed execution aborted."
