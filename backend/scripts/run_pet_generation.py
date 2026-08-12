"""Generate tests for Pet.java and verify Maven compilation in Docker."""
from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import jacoco, java_parser, test_generator

PETCLINIC_REPO = "https://github.com/spring-projects/spring-petclinic.git"
PET_REL = "src/main/java/org/springframework/samples/petclinic/owner/Pet.java"


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


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project_root = clone_petclinic(Path(tmp) / "spring-petclinic")
        pet_path = project_root / PET_REL

        jacoco.inject_jacoco_plugin(project_root / "pom.xml")
        print("Running baseline Maven (Docker)…")
        jacoco.run_maven_tests(
            project_root,
            activity_message="Baseline coverage for Pet generation test",
        )
        report = jacoco.find_jacoco_report(project_root)
        coverage = jacoco.parse_jacoco_report(report) if report else None

        print("Generating tests for Pet.java via Ollama…")
        result = await test_generator.generate_for_class(
            pet_path, project_root, coverage, session_id=None
        )

        print(f"Status: {result.status}")
        print(f"Passed: {result.tests_passed}")
        print(f"Message: {result.message}")

        if result.test_source:
            issues = java_parser.check_common_compile_errors(
                result.test_source,
                java_parser.read_class_content(pet_path),
                has_di_dependencies=False,
            )
            if issues:
                print("Remaining static compile issues:")
                for issue in issues:
                    print(f"  - {issue}")

            print("--- generated test ---")
            print(result.test_source)

        if not result.tests_passed:
            sys.exit(1)

        print("Pet.java generation compiled and passed Maven verification.")


if __name__ == "__main__":
    asyncio.run(main())
