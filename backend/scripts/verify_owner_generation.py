"""Generate tests for Owner.java and check for known compile-error patterns."""
from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import jacoco, test_generator
from app.services.java_parser import compute_static_stats

PETCLINIC_REPO = "https://github.com/spring-projects/spring-petclinic.git"
OWNER_REL = "src/main/java/org/springframework/samples/petclinic/owner/Owner.java"


def clone_petclinic(dest: Path) -> Path:
    if (dest / "pom.xml").exists():
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", PETCLINIC_REPO, str(dest)],
        check=True,
        capture_output=True,
    )
    return dest


def check_generated_test(test_source: str) -> list[str]:
    issues: list[str] = []
    if "setDate(\"" in test_source or "setDate('" in test_source:
        issues.append("visit.setDate() appears to use String instead of LocalDate")
    if "getVisits().get(" in test_source:
        issues.append("pet.getVisits().get(...) — invalid on Collection return type")
    return issues


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project_root = clone_petclinic(Path(tmp) / "spring-petclinic")
        owner_path = project_root / OWNER_REL

        pom = project_root / "pom.xml"
        jacoco.inject_jacoco_plugin(pom)
        print("Running baseline Maven (Docker)…")
        jacoco.run_maven_tests(
            project_root,
            activity_message="Baseline coverage for Owner generation test",
        )
        report = jacoco.find_jacoco_report(project_root)
        coverage = jacoco.parse_jacoco_report(report) if report else None

        print("Generating tests for Owner.java via Ollama…")
        result = await test_generator.generate_for_class(
            owner_path, project_root, coverage, session_id=None
        )

        print(f"Status: {result.status} | passed: {result.tests_passed}")
        print(f"Message: {result.message}")

        if not result.test_source:
            raise SystemExit("No test source returned")

        issues = check_generated_test(result.test_source)
        if issues:
            print("REGRESSION patterns found in generated test:")
            for i in issues:
                print(f"  - {i}")
            print("--- test excerpt ---")
            for line in result.test_source.splitlines()[:60]:
                print(line)
            sys.exit(1)

        if not result.tests_passed:
            print("Test did not pass Maven verification (may be unrelated compile errors).")
            print(result.test_source[:2000])
            sys.exit(1)

        print("Owner.java generation passed — no known type/collection regressions.")


if __name__ == "__main__":
    asyncio.run(main())
