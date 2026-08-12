"""Verify prompt/compile checks for Pet.java (Spring PetClinic entity)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import di_resolver, java_parser, ollama_client

PETCLINIC_REPO = "https://github.com/spring-projects/spring-petclinic.git"
PET_REL = "src/main/java/org/springframework/samples/petclinic/owner/Pet.java"

FAILING_PET_TEST = """package org.springframework.samples.petclinic.owner;

import static org.junit.jupiter.api.Assertions.*;

import java.time.LocalDate;
import java.util.Set;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
public class PetTest {

    @Mock
    private PetType petType;

    @InjectMocks
    private Pet pet;

    @BeforeEach
    public void setUp() {
        pet.setBirthDate(LocalDate.now());
        pet.setType(petType);
    }

    @Test
    public void testGetVisits() {
        Set<Visit> visits = pet.getVisits();
        assertNotNull(visits);
        assertTrue(visits.isEmpty());
    }
}"""


def clone_petclinic(dest: Path) -> Path:
    import subprocess

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
        pet_path = project_root / PET_REL
        if not pet_path.exists():
            raise SystemExit(f"Pet.java not found at {pet_path}")

        pet_source = java_parser.read_class_content(pet_path)
        assert java_parser.detect_class_kind(pet_source) == "entity"

        returns = dict(java_parser.extract_public_method_return_types(pet_source))
        assert returns.get("getVisits") == "Collection<Visit>"

        issues = java_parser.check_common_compile_errors(
            FAILING_PET_TEST, pet_source, has_di_dependencies=False
        )
        assert issues, "Expected compile issues for known-bad PetTest"
        assert any("InjectMocks" in i for i in issues)
        assert any("getVisits" in i for i in issues)

        deps = di_resolver.resolve_dependencies(pet_path, project_root)
        refs = di_resolver.resolve_referenced_classes(pet_path, project_root, deps)
        prompt = ollama_client.build_test_generation_prompt(
            "Pet",
            pet_source,
            deps,
            uncovered_lines=[101, 102],
            coverage_pct=100.0,
            referenced_classes=refs,
            class_kind="entity",
            method_return_types=java_parser.extract_public_method_return_types(pet_source),
        )
        assert "JPA entity" in prompt
        assert "getVisits() returns Collection<Visit>" in prompt
        assert "do NOT assign to Set/List" in prompt

        print("All Pet.java prompt/compile checks passed.")


if __name__ == "__main__":
    main()
