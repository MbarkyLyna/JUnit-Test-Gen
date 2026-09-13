from __future__ import annotations

from pathlib import Path


def build_existing_test_map(project_root: Path) -> dict[str, Path]:
    """
    Scan src/test/java for Java test files and map each to its corresponding
    main class name (simple name). Returns dict {class_simple_name: test_file_path}.
    """
    test_root = project_root / "src" / "test" / "java"
    if not test_root.exists():
        return {}

    mapping: dict[str, Path] = {}
    # Standard test naming: FooTest.java, FooTests.java
    for test_file in test_root.rglob("*.java"):
        # Skip if not a test (e.g., helper classes) – we can check for @Test annotation
        content = test_file.read_text(encoding="utf-8", errors="ignore")
        if "@Test" not in content:
            continue
        # Derive main class name: remove "Test" or "Tests" suffix
        name = test_file.stem
        for suffix in ("Test", "Tests"):
            if name.endswith(suffix):
                main_name = name[:-len(suffix)]
                break
        else:
            # Fallback: use whole name (maybe it's a parameterized test class?)
            main_name = name
        mapping[main_name] = test_file
    return mapping


def find_existing_test_for_class(class_name: str, project_root: Path) -> Path | None:
    """Return the test file path for a given main class name, or None."""
    mapping = build_existing_test_map(project_root)
    return mapping.get(class_name)