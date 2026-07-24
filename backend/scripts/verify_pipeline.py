"""Staged pipeline verification — clone, single-class, multi-select (no full project)."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:8000/api"
CLONE_URL = "https://github.com/spring-guides/gs-serving-web-content"
POLL_INTERVAL = 2.0
JOB_TIMEOUT = 900  # 15 min max for batch


def req(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        f"{API}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(r, timeout=600) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        try:
            parsed = json.loads(detail)
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise RuntimeError(f"{method} {path} -> {e.code}: {detail}") from e


def find_java_classes(node: dict, out: list[str]) -> None:
    if node.get("type") == "file" and node.get("name", "").endswith(".java"):
        p = node.get("path", "")
        if "/main/" in p.replace("\\", "/") or "\\main\\" in p:
            if "/test/" not in p and "\\test\\" not in p:
                out.append(p)
    for child in node.get("children") or []:
        find_java_classes(child, out)


def wait_job(job_id: str) -> dict:
    deadline = time.time() + JOB_TIMEOUT
    while time.time() < deadline:
        job = req("GET", f"/generate/jobs/{job_id}")
        print(f"  job {job['status']}: {job.get('message', '')} ({job.get('current_index', 0)}/{job.get('total', 0)})")
        if job["status"] in {"completed", "aborted", "failed"}:
            return job
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Job {job_id} did not finish within {JOB_TIMEOUT}s")


def summarize_results(results: list[dict]) -> str:
    passed = sum(1 for r in results if r.get("tests_passed"))
    return f"{len(results)} class(es), {passed} passed Maven verification"


def main() -> None:
    results: dict[str, str] = {}

    print("=== 1. Health ===")
    health = req("GET", "/health")
    print(json.dumps(health, indent=2))
    if not health.get("ollama_available"):
        print("FAIL: Ollama not available")
        sys.exit(1)
    results["health"] = "ok"

    print("\n=== 2. GitHub clone ===")
    clone = req("POST", "/clone", {"repo_url": CLONE_URL})
    session_id = clone["session_id"]
    classes: list[str] = []
    find_java_classes(clone["tree"], classes)
    print(f"  session={session_id}, classes={len(classes)}")
    if not classes:
        raise RuntimeError("Clone succeeded but no main Java classes found")
    results["clone"] = f"ok — {len(classes)} main classes"

    print("\n=== 3. Analyze ===")
    analyze = req("POST", f"/analyze/{session_id}")
    cov = analyze["stats"].get("coverage") or {}
    print(f"  line coverage: {cov.get('line_coverage_pct', '?')}%")
    results["analyze"] = "ok"

    target = classes[0]
    print(f"\n=== 4. Single-class generate ({target}) ===")
    single = req("POST", f"/generate/{session_id}", {"scope": "class", "class_path": target})
    print(f"  {summarize_results(single['results'])}")
    for r in single["results"]:
        print(f"    {r['class_path']}: {r['status']} — {r['message'][:80]}")
    results["single_class"] = summarize_results(single["results"])

    batch = classes[: min(2, len(classes))]
    print(f"\n=== 5. Multi-select batch ({len(batch)} classes) ===")
    if not health.get("ram_ok"):
        print(f"  RAM guard active: {health.get('ram_message', '').replace(chr(8805), '>=')}")
        try:
            req("POST", f"/generate/{session_id}/start", {"scope": "classes", "class_paths": batch})
            results["multi_select"] = "unexpected — batch started despite low RAM"
        except RuntimeError as e:
            if "503" in str(e) or "Insufficient RAM" in str(e):
                print(f"  blocked as expected: {e}")
                results["multi_select"] = f"ram_guard_ok — {e}"
            else:
                raise
    else:
        start = req("POST", f"/generate/{session_id}/start", {"scope": "classes", "class_paths": batch})
        job = wait_job(start["job_id"])
        print(f"  {summarize_results(job.get('results') or [])}")
        for r in job.get("results") or []:
            print(f"    {r['class_path']}: {r['status']}")
        results["multi_select"] = f"{job['status']} — {summarize_results(job.get('results') or [])}"

    print("\n=== Summary ===")
    for k, v in results.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
