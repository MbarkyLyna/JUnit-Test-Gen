"""Verify in-project referenced-class resolution for Owner.java (Spring PetClinic)."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import di_resolver, java_parser, ollama_client

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


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project_root = clone_petclinic(Path(tmp) / "spring-petclinic")
        owner_path = project_root / OWNER_REL
        if not owner_path.exists():
            raise SystemExit(f"Owner.java not found at {owner_path}")

        deps = di_resolver.resolve_dependencies(owner_path, project_root)
        refs = di_resolver.resolve_referenced_classes(owner_path, project_root, deps)
        ref_names = {r["class_name"] for r in refs}

        print("DI dependencies:", [d["dependency_type"] for d in deps])
        print("Referenced in-project classes:", sorted(ref_names))

        assert "Pet" in ref_names, "Expected Pet among referenced classes"
        assert "Visit" in ref_names, "Expected Visit among referenced classes"
        assert "String" not in ref_names, "String must not be included"
        assert "List" not in ref_names, "List must not be included"

        pet_ref = next(r for r in refs if r["class_name"] == "Pet")
        visit_ref = next(r for r in refs if r["class_name"] == "Visit")
        assert "Collection" in pet_ref["source"] or "getVisits" in pet_ref["source"]
        assert "LocalDate" in visit_ref["source"] or "setDate" in visit_ref["source"]

        owner_source = java_parser.read_class_content(owner_path)
        prompt = ollama_client.build_test_generation_prompt(
            "Owner",
            owner_source,
            deps,
            uncovered_lines=[120, 121, 122],
            coverage_pct=55.0,
            referenced_classes=refs,
        )
        assert "Referenced in-project classes (not injected, but used internally):" in prompt
        assert "Collection" in prompt or "getVisits" in prompt
        assert "LocalDate" in prompt or "setDate" in prompt

        empty_prompt = ollama_client.build_test_generation_prompt(
            "Owner", owner_source, deps, [], 0.0, referenced_classes=[]
        )
        assert "Referenced in-project classes" not in empty_prompt

        print("All referenced-class checks passed.")


if __name__ == "__main__":
    main()
