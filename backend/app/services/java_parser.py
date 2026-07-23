from __future__ import annotations

import re
from pathlib import Path

from app.models.schemas import ProjectStats, StaticStats

PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
CLASS_RE = re.compile(
    r"(?:public\s+|protected\s+|private\s+|)(?:abstract\s+|final\s+|)"
    r"(?:class|interface|enum|record)\s+(\w+)"
)
TEST_ANNOTATION_RE = re.compile(r"@Test\b")
AUTOWIRED_FIELD_RE = re.compile(
    r"@Autowired\s*(?:\([^)]*\))?\s*(?:private|protected|public)?\s*([\w.<>,\s\[\]]+?)\s+(\w+)\s*;",
    re.MULTILINE | re.DOTALL,
)
CONSTRUCTOR_INJECT_RE = re.compile(
    r"(?:public|protected)\s+\w+\s*\(\s*([^)]*)\)",
    re.MULTILINE,
)
IMPLEMENTS_RE = re.compile(r"implements\s+([\w\s,<>.\[\]]+?)(?:\s*\{|\s+extends)")
EXTENDS_RE = re.compile(r"extends\s+([\w\s,<>.\[\]]+?)(?:\s+implements|\s*\{)")
PRIMARY_RE = re.compile(r"@Primary\b")
PROFILE_RE = re.compile(r'@Profile\s*\(\s*"([^"]+)"\s*\)')
QUALIFIER_RE = re.compile(r'@Qualifier\s*\(\s*"([^"]+)"\s*\)')
IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", re.MULTILINE)


def _is_test_file(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    return "test" in parts or path.name.endswith("Test.java") or path.name.endswith("Tests.java")


def _is_main_source(path: Path) -> bool:
    parts = path.parts
    return "src" in parts and "main" in parts and path.suffix == ".java"


def collect_java_files(project_root: Path) -> list[Path]:
    return sorted(project_root.rglob("*.java"))


def compute_static_stats(project_root: Path) -> StaticStats:
    java_files = collect_java_files(project_root)
    main_files = [f for f in java_files if _is_main_source(f)]
    test_files = [f for f in java_files if _is_test_file(f) and not _is_main_source(f)]

    packages: set[str] = set()
    for f in main_files:
        content = f.read_text(encoding="utf-8", errors="ignore")
        m = PACKAGE_RE.search(content)
        if m:
            packages.add(m.group(1))

    test_count = 0
    for f in test_files:
        content = f.read_text(encoding="utf-8", errors="ignore")
        test_count += len(TEST_ANNOTATION_RE.findall(content))

    return StaticStats(
        class_count=len(main_files),
        package_count=len(packages),
        test_count=test_count,
    )


def read_class_content(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def get_package_name(content: str) -> str | None:
    m = PACKAGE_RE.search(content)
    return m.group(1) if m else None


def get_class_name(content: str) -> str | None:
    m = CLASS_RE.search(content)
    return m.group(1) if m else None


def get_fqn(path: Path, project_root: Path) -> str:
    content = read_class_content(path)
    pkg = get_package_name(content)
    cls = get_class_name(content)
    if pkg and cls:
        return f"{pkg}.{cls}"
    return path.stem


def parse_imports(content: str) -> dict[str, str]:
    """Map simple name -> fully qualified name."""
    imports: dict[str, str] = {}
    for m in IMPORT_RE.finditer(content):
        fqn = m.group(1)
        simple = fqn.rsplit(".", 1)[-1]
        imports[simple] = fqn
    return imports


def resolve_type_simple(type_str: str) -> str:
    """Extract base type name from generic/list syntax."""
    t = type_str.strip()
    t = re.sub(r"<.*?>", "", t)
    t = t.replace("[]", "").strip()
    return t.split(".")[-1]


def _qualifier_before(content: str, start: int) -> str | None:
    """Extract @Qualifier value from annotations immediately preceding a declaration."""
    prefix = content[max(0, start - 300) : start]
    qm = QUALIFIER_RE.search(prefix)
    return qm.group(1) if qm else None


def extract_dependencies(content: str) -> list[dict[str, str]]:
    """Extract injected dependencies from @Autowired fields and constructor."""
    deps: list[dict[str, str]] = []
    seen: set[str] = set()

    for m in AUTOWIRED_FIELD_RE.finditer(content):
        type_name = resolve_type_simple(m.group(1))
        field_name = m.group(2)
        if field_name not in seen:
            qualifier = _qualifier_before(content, m.start())
            dep: dict[str, str] = {
                "type": type_name,
                "name": field_name,
                "source": "field",
            }
            if qualifier:
                dep["qualifier"] = qualifier
            deps.append(dep)
            seen.add(field_name)

    ctor_match = CONSTRUCTOR_INJECT_RE.search(content)
    if ctor_match and "@Autowired" in content.split("(")[0][-200:]:
        params = ctor_match.group(1)
        if params.strip():
            for param in params.split(","):
                param = param.strip()
                if not param:
                    continue
                parts = param.split()
                if len(parts) >= 2:
                    field_name = parts[-1]
                    type_name = resolve_type_simple(" ".join(parts[:-1]))
                    if field_name not in seen:
                        qm = QUALIFIER_RE.search(param)
                        dep = {
                            "type": type_name,
                            "name": field_name,
                            "source": "constructor",
                        }
                        if qm:
                            dep["qualifier"] = qm.group(1)
                        deps.append(dep)
                        seen.add(field_name)

    # Spring often uses constructor injection without explicit @Autowired on ctor
    if not deps:
        ctor_match = CONSTRUCTOR_INJECT_RE.search(content)
        if ctor_match:
            params = ctor_match.group(1)
            for param in params.split(","):
                param = param.strip()
                if not param or param in {"final"}:
                    continue
                parts = param.split()
                if len(parts) >= 2:
                    field_name = parts[-1]
                    type_name = resolve_type_simple(" ".join(parts[:-1]))
                    if field_name not in seen and type_name[0].isupper():
                        qm = QUALIFIER_RE.search(param)
                        dep = {
                            "type": type_name,
                            "name": field_name,
                            "source": "constructor",
                        }
                        if qm:
                            dep["qualifier"] = qm.group(1)
                        deps.append(dep)
                        seen.add(field_name)

    return deps


def index_project_classes(project_root: Path) -> dict[str, list[Path]]:
    """Index simple class name -> list of file paths."""
    index: dict[str, list[Path]] = {}
    for java_file in collect_java_files(project_root):
        if not _is_main_source(java_file):
            continue
        content = read_class_content(java_file)
        cls = get_class_name(content)
        if cls:
            index.setdefault(cls, []).append(java_file)
    return index


def find_implementations(
    project_root: Path, interface_simple_name: str
) -> list[tuple[Path, str]]:
    """Find classes that implement or extend the given type."""
    results: list[tuple[Path, str]] = []
    for java_file in collect_java_files(project_root):
        if not _is_main_source(java_file):
            continue
        content = read_class_content(java_file)
        cls = get_class_name(content)
        if not cls or cls == interface_simple_name:
            continue

        implements = IMPLEMENTS_RE.search(content)
        extends = EXTENDS_RE.search(content)
        targets: list[str] = []
        if implements:
            targets.extend(t.strip() for t in implements.group(1).split(","))
        if extends:
            targets.extend(t.strip() for t in extends.group(1).split(","))

        for target in targets:
            simple = resolve_type_simple(target)
            if simple == interface_simple_name:
                results.append((java_file, cls))
                break

    return results


def get_class_annotations(content: str) -> dict[str, str | bool]:
    ann: dict[str, str | bool] = {}
    if PRIMARY_RE.search(content):
        ann["primary"] = True
    pm = PROFILE_RE.search(content)
    if pm:
        ann["profile"] = pm.group(1)
    qm = QUALIFIER_RE.search(content)
    if qm:
        ann["qualifier"] = qm.group(1)
    return ann


def list_main_classes(project_root: Path) -> list[Path]:
    return [f for f in collect_java_files(project_root) if _is_main_source(f)]


def empty_stats() -> ProjectStats:
    return ProjectStats(static=StaticStats(), coverage=None, analyzed=False)


TRIVIAL_ANNOTATION_RE = re.compile(
    r"@(Getter|Setter|Data|EqualsAndHashCode|ToString|Value|Builder|NoArgsConstructor|AllArgsConstructor|RequiredArgsConstructor)\b"
)
GETTER_SETTER_SIG_RE = re.compile(r"\b(get[A-Z]\w*|set[A-Z]\w*|is[A-Z]\w*)\s*\(")
EQUALS_HASHCODE_TOSTRING_RE = re.compile(r"\b(equals|hashCode|toString)\s*\(")


def identify_trivial_lines(content: str) -> set[int]:
    """
    Identify 1-based line numbers for Lombok annotations, standard getters/setters,
    equals/hashCode/toString methods, and DTO field declarations.
    """
    lines = content.splitlines()
    trivial_lines: set[int] = set()
    is_dto_class = bool(TRIVIAL_ANNOTATION_RE.search(content))

    in_trivial_block = False
    brace_depth = 0

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()

        if TRIVIAL_ANNOTATION_RE.search(stripped):
            trivial_lines.add(idx)

        if GETTER_SETTER_SIG_RE.search(stripped) or EQUALS_HASHCODE_TOSTRING_RE.search(stripped):
            in_trivial_block = True
            trivial_lines.add(idx)
            brace_depth += stripped.count("{") - stripped.count("}")
            if brace_depth <= 0 and "{" in stripped and "}" in stripped:
                in_trivial_block = False
                brace_depth = 0
            continue

        if in_trivial_block:
            trivial_lines.add(idx)
            brace_depth += stripped.count("{") - stripped.count("}")
            if brace_depth <= 0:
                in_trivial_block = False
                brace_depth = 0

        if is_dto_class and (stripped.startswith("private ") or stripped.startswith("protected ")) and stripped.endswith(";"):
            trivial_lines.add(idx)

    return trivial_lines

