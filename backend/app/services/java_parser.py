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

# Types/keywords that are never in-project domain classes
_NON_TYPE_TOKENS = frozenset({
    "void", "boolean", "byte", "char", "short", "int", "long", "float", "double",
    "var", "final", "null", "true", "false", "new", "return", "if", "else", "for",
    "while", "switch", "case", "default", "try", "catch", "throw", "class", "interface",
    "enum", "record", "extends", "implements", "import", "package", "public",
    "protected", "private", "static", "abstract", "synchronized", "native", "strictfp",
    "this", "super", "else", "do", "break", "continue", "instanceof", "assert",
})

FIELD_DECL_RE = re.compile(
    r"(?:private|protected|public)\s+(?:static\s+)?(?:final\s+)?([\w.<>,?\s\[\]]+?)\s+(\w+)\s*(?:=|;)",
    re.MULTILINE,
)
METHOD_DECL_RE = re.compile(
    r"(?:public|protected|private)\s+(?:static\s+)?(?:synchronized\s+)?(?:<[^>]+>\s+)?"
    r"([\w.<>,?\s\[\]]+?)\s+(\w+)\s*\(([^)]*)\)",
    re.MULTILINE,
)
LOCAL_VAR_RE = re.compile(
    r"(?<![\w.])(?:final\s+)?([\w.<>,?\s\[\]]+?)\s+(\w+)\s*=",
)
FOR_EACH_RE = re.compile(
    r"for\s*\(\s*(?:final\s+)?([\w.<>,?\s\[\]]+?)\s+(\w+)\s*:",
)
NEW_EXPR_RE = re.compile(
    r"\bnew\s+([\w.]+(?:<[^>]*>)?)\s*\(",
)
PUBLIC_METHOD_SIG_RE = re.compile(
    r"^\s*(?:public|protected)\s+(?!class|interface|enum|record)"
    r"(?:static\s+)?(?:<[^>]+>\s+)?([\w.<>,?\s\[\]?]+)\s+(\w+)\s*\([^;{]*\)",
    re.MULTILINE,
)
PUBLIC_FIELD_SIG_RE = re.compile(
    r"^\s*(?:public|protected)\s+(?:static\s+)?(?:final\s+)?([\w.<>,?\s\[\]?]+)\s+(\w+)\s*[;=]",
    re.MULTILINE,
)

# --- Generic structural-detection helpers used by check_common_compile_errors ---
# These replace what used to be hardcoded checks against one specific domain
# model (Owner/Pet/addVisit/setDate) so the same checks work on any Spring
# Boot project's own classes.
ID_ANNOTATION_FIELD_RE = re.compile(
    r"@Id\b[\s\S]{0,200}?(?:private|protected|public)\s+([\w.<>]+)\s+(\w+)\s*;",
    re.MULTILINE,
)
PLAIN_ID_FIELD_RE = re.compile(
    r"(?:private|protected|public)\s+([\w.<>]+)\s+id\s*;",
    re.MULTILINE,
)
SETTER_PARAM_RE = re.compile(
    r"(?:public|protected)\s+\w+\s+(set\w+)\s*\(\s*([\w.<>\[\]]+)\s+\w+\s*\)",
    re.MULTILINE,
)
METHOD_SIG_START_RE = re.compile(
    r"(?:public|protected)\s+(?:static\s+)?(?:synchronized\s+)?(?:<[^>]+>\s+)?"
    r"[\w.<>,?\[\]]+\s+(\w+)\s*\([^;{]*\)\s*(?:throws\s+[\w.,\s]+)?\{",
    re.MULTILINE,
)

LARGE_CLASS_CHAR_THRESHOLD = 4000

ENTITY_RE = re.compile(r"@Entity\b")
SERVICE_RE = re.compile(r"@Service\b")
CONTROLLER_RE = re.compile(r"@(?:RestController|Controller)\b")
REPOSITORY_RE = re.compile(r"@Repository\b")
COMPONENT_RE = re.compile(r"@Component\b")

# --- Step 3: exclusion detection ---
# Swing/AWT and other desktop-GUI code is not relevant to a Spring Boot
# unit-test generator: it isn't wired through DI, has no meaningful line
# coverage target in this context, and generated Mockito tests for it are
# almost always nonsensical (mocking a JButton achieves nothing).
_SWING_AWT_IMPORT_RE = re.compile(r"^\s*import\s+(?:javax\.swing|java\.awt)\.", re.MULTILINE)
_SWING_SUPERCLASS_RE = re.compile(
    r"\bextends\s+(?:J?Frame|J?Panel|J?Dialog|Applet|Canvas|Window|JApplet)\b"
)
_INTERFACE_DECL_RE = re.compile(r"^\s*(?:public\s+)?interface\s+\w+", re.MULTILINE)
_DEFAULT_OR_STATIC_METHOD_RE = re.compile(
    r"\b(?:default|static)\s+[\w.<>\[\]]+\s+\w+\s*\([^;{]*\)\s*\{"
)


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


def classify_exclusion(path: Path, content: str) -> str | None:
    """
    Return a human-readable reason if this file should be EXCLUDED from test
    generation, or None if it's a valid target. This is Step 3's actual
    filtering — grouping is meaningless without first removing files that
    aren't real, testable classes.
    """
    name = path.name

    if name in {"package-info.java", "module-info.java"}:
        return "package-info/module-info — not a class declaration"

    if _SWING_AWT_IMPORT_RE.search(content) or _SWING_SUPERCLASS_RE.search(content):
        return "Swing/AWT desktop-GUI class — irrelevant to Spring Boot unit test generation"

    if not CLASS_RE.search(content):
        return "no class/interface/enum/record declaration found in file"

    if _INTERFACE_DECL_RE.search(content) and not _DEFAULT_OR_STATIC_METHOD_RE.search(content):
        return "pure interface with no default/static method bodies — nothing to unit test directly"

    return None


def list_generation_targets(project_root: Path) -> list[Path]:
    """Main classes eligible for test generation: excludes package-info/
    module-info, Swing/AWT GUI classes, and pure interfaces with no
    implemented method bodies. Use this instead of list_main_classes()
    anywhere generation actually happens."""
    targets: list[Path] = []
    for f in list_main_classes(project_root):
        content = read_class_content(f)
        if classify_exclusion(f, content) is None:
            targets.append(f)
    return targets


def list_excluded_classes(project_root: Path) -> dict[str, str]:
    """Map excluded file stem -> reason, for reporting to the user/UI so the
    exclusion isn't silent."""
    excluded: dict[str, str] = {}
    for f in list_main_classes(project_root):
        content = read_class_content(f)
        reason = classify_exclusion(f, content)
        if reason:
            excluded[f.stem] = reason
    return excluded


def group_project_classes(project_root: Path) -> dict[str, list[str]]:
    """Group eligible (non-excluded) class simple names by their Java
    package, for orderly, report-friendly processing and display."""
    groups: dict[str, list[str]] = {}
    for f in list_generation_targets(project_root):
        content = read_class_content(f)
        pkg = get_package_name(content) or "(default package)"
        cls = get_class_name(content) or f.stem
        groups.setdefault(pkg, []).append(cls)
    for pkg in groups:
        groups[pkg].sort()
    return groups


def empty_stats() -> ProjectStats:
    return ProjectStats(static=StaticStats(), coverage=None, analyzed=False)


TRIVIAL_ANNOTATION_RE = re.compile(
    r"@(Getter|Setter|Data|EqualsAndHashCode|ToString|Value|Builder|NoArgsConstructor|AllArgsConstructor|RequiredArgsConstructor)\b"
)
GETTER_SETTER_SIG_RE = re.compile(r"\b(get[A-Z]\w*|set[A-Z]\w*|is[A-Z]\w*)\s*\(")
EQUALS_HASHCODE_TOSTRING_RE = re.compile(r"\b(equals|hashCode|toString)\s*\(")


def extract_nested_type_names(type_str: str) -> list[str]:
    """Extract simple class names from a type string, including generic parameters."""
    names: list[str] = []
    t = type_str.strip()
    if not t:
        return names

    base = resolve_type_simple(t.rstrip("[]"))
    if base and base[0].isupper() and base not in _NON_TYPE_TOKENS:
        names.append(base)

    for inner in re.findall(r"<([^<>]+(?:<[^<>]*>)?)>", t):
        for part in inner.split(","):
            part = part.strip()
            if part:
                names.extend(extract_nested_type_names(part))

    if t.endswith("[]"):
        names.extend(extract_nested_type_names(t[:-2]))

    return names


def _types_from_parameter_declaration(param: str) -> list[str]:
    cleaned = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", param).strip()
    if not cleaned:
        return []
    parts = cleaned.split()
    if len(parts) < 2:
        return []
    type_part = " ".join(parts[:-1])
    return extract_nested_type_names(type_part)


def _split_parameter_list(params: str) -> list[str]:
    if not params.strip():
        return []
    result: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in params + ",":
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            chunk = "".join(current).strip()
            if chunk:
                result.append(chunk)
            current = []
            continue
        current.append(ch)
    return result


def extract_referenced_types(content: str) -> list[str]:
    """
    Scan fields, method signatures (return/params), and method bodies for
    referenced Java type names.
    """
    found: set[str] = set()

    for m in FIELD_DECL_RE.finditer(content):
        found.update(extract_nested_type_names(m.group(1)))

    for m in METHOD_DECL_RE.finditer(content):
        return_type = m.group(1).strip()
        if return_type not in _NON_TYPE_TOKENS:
            found.update(extract_nested_type_names(return_type))
        for param in _split_parameter_list(m.group(3)):
            found.update(_types_from_parameter_declaration(param))

    for m in LOCAL_VAR_RE.finditer(content):
        found.update(extract_nested_type_names(m.group(1)))

    for m in FOR_EACH_RE.finditer(content):
        found.update(extract_nested_type_names(m.group(1)))

    for m in NEW_EXPR_RE.finditer(content):
        found.update(extract_nested_type_names(m.group(1)))

    return sorted(found)


def detect_class_kind(content: str) -> str:
    """Classify target for test strategy: entity, service, controller, repository, component, plain."""
    if ENTITY_RE.search(content):
        return "entity"
    if SERVICE_RE.search(content):
        return "service"
    if CONTROLLER_RE.search(content):
        return "controller"
    if REPOSITORY_RE.search(content):
        return "repository"
    if COMPONENT_RE.search(content):
        return "component"
    return "plain"


def extract_public_void_methods(content: str) -> list[tuple[str, str]]:
    """Return (method_name, param_type) for public void methods with one parameter."""
    results: list[tuple[str, str]] = []
    pattern = re.compile(
        r"public\s+void\s+(\w+)\s*\(\s*([\w.<>,?\s\[\]]+?)\s+\w+\s*\)",
        re.MULTILINE,
    )
    for m in pattern.finditer(content):
        param_type = resolve_type_simple(m.group(2).strip())
        if param_type and param_type[0].isupper():
            results.append((m.group(1), param_type))
    return results


def extract_public_method_return_types(content: str) -> list[tuple[str, str]]:
    """Return (method_name, return_type) for public/protected instance methods."""
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in PUBLIC_METHOD_SIG_RE.finditer(content):
        return_type = re.sub(r"\s+", " ", m.group(1).strip())
        method_name = m.group(2)
        if method_name in seen or return_type in _NON_TYPE_TOKENS:
            continue
        seen.add(method_name)
        results.append((method_name, return_type))
    return results


def detect_id_type(content: str) -> str | None:
    """
    Find the real type of this class's id field — via @Id (JPA) first, then a
    plain field literally named `id` — so id-related checks work on any
    entity's actual type instead of assuming Integer.
    """
    m = ID_ANNOTATION_FIELD_RE.search(content)
    if m:
        return resolve_type_simple(m.group(1))
    m2 = PLAIN_ID_FIELD_RE.search(content)
    if m2:
        return resolve_type_simple(m2.group(1))
    return None


def extract_setter_param_types(content: str) -> dict[str, str]:
    """Map setter method name -> its parameter's simple type, from the real class source."""
    return {
        m.group(1): resolve_type_simple(m.group(2))
        for m in SETTER_PARAM_RE.finditer(content)
    }


def _extract_method_bodies(content: str) -> dict[str, str]:
    """Map method name -> brace-matched body text, for public/protected methods."""
    bodies: dict[str, str] = {}
    for m in METHOD_SIG_START_RE.finditer(content):
        name = m.group(1)
        start = m.end() - 1  # position of the opening '{'
        depth = 0
        i = start
        while i < len(content):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        bodies[name] = content[start : i + 1]
    return bodies


def find_id_guarded_add_methods(content: str) -> list[str]:
    """
    Find add*-style methods whose own body guards insertion on an isNew()-style
    or null-id check. Any such method requires callers to add the item BEFORE
    setting its id — this is detected from the real method body, not assumed
    for specific hardcoded method names.
    """
    guarded: list[str] = []
    for name, body in _extract_method_bodies(content).items():
        if not name.lower().startswith("add"):
            continue
        if re.search(r"isNew\s*\(\s*\)", body) or re.search(r"getId\s*\(\s*\)\s*==\s*null", body):
            guarded.append(name)
    return guarded


def find_undefined_target_method_calls(test_source: str, target_source: str) -> list[str]:
    """
    Heuristically catch hallucinated no-arg getter/is calls on a variable that
    looks like an instance of the target class (e.g. `owner` for class Owner),
    where that method isn't actually defined on the target class.
    """
    class_name = get_class_name(target_source)
    if not class_name:
        return []
    var_hint = class_name[0].lower() + class_name[1:]
    known = {name for name, _ in extract_public_method_return_types(target_source)}
    known |= {name for name, _ in extract_public_void_methods(target_source)}
    calls = re.findall(rf"\b{re.escape(var_hint)}\.((?:get|is)\w*)\s*\(\s*\)", test_source)
    return sorted({c for c in calls if c not in known})


def check_common_compile_errors(
    test_source: str,
    target_source: str,
    *,
    has_di_dependencies: bool = False,
) -> list[str]:
    """
    Static checks for frequent LLM compile mistakes, derived dynamically from
    the target class's own structure (real id type, real setter param types,
    real guarded add-methods, real method names) so they generalize to any
    Spring Boot project instead of one specific hardcoded domain model.
    """
    issues: list[str] = []
    class_kind = detect_class_kind(target_source)

    if class_kind == "entity" and not has_di_dependencies:
        if "@InjectMocks" in test_source:
            issues.append(
                "Do not use @InjectMocks on a JPA entity/domain object; use `new ClassName()` instead"
            )
        if re.search(r"\bwhen\s*\(\s*\w+\.\w+\(", test_source) and "@InjectMocks" in test_source:
            issues.append(
                "Do not call when()/verify() on the @InjectMocks object itself — "
                "it is the real object under test, not a mock. Remove all Mockito "
                "usage for this entity and call its real methods directly."
            )
        if "@ExtendWith(MockitoExtension.class)" in test_source:
            issues.append(
                "Do not use MockitoExtension for a plain entity; use `new ClassName()` and plain JUnit 5"
            )

    for method_name, return_type in extract_public_method_return_types(target_source):
        base_return = resolve_type_simple(return_type)
        if base_return not in {"Collection", "Iterable"}:
            continue
        narrow_assign = re.search(
            rf"(?:Set|List)<[^>]+>\s+\w+\s*=\s*\w+\.{method_name}\s*\(\s*\)",
            test_source,
        )
        if narrow_assign:
            issues.append(
                f"{method_name}() returns {return_type}; do not assign to Set or List without a cast"
            )
        indexed_access = re.search(rf"\.{method_name}\s*\(\s*\)\s*\.\s*get\s*\(", test_source)
        if indexed_access:
            issues.append(
                f"{method_name}() returns {return_type}; Collection has no get(index)"
            )

    id_type = detect_id_type(target_source)
    if id_type in {"Integer", "int"}:
        if re.search(r"\.set\w*[Ii]d\w*\s*\(\s*\d+L\s*\)", test_source) or re.search(
            r"\.(?:get|find)\w*\s*\(\s*\d+L\s*(?:,|\))", test_source
        ):
            issues.append(
                f"IDs on this class are {id_type}, not long/Long — do not use L-suffixed "
                "long literals (e.g. 1L) for id arguments; use plain int literals (e.g. 1)"
            )

    for call in find_undefined_target_method_calls(test_source, target_source):
        issues.append(
            f"{call}() does not exist on {get_class_name(target_source) or 'the target class'} "
            "— check the real class for the correct method or field before calling it"
        )

    for setter, ptype in extract_setter_param_types(target_source).items():
        if ptype in {"LocalDate", "LocalDateTime", "LocalTime", "Date", "Instant"}:
            if re.search(rf"\.{setter}\s*\(\s*[\"']", test_source):
                issues.append(f"{setter}() expects {ptype}, not a String literal")

    if class_kind == "entity":
        for method_name in find_id_guarded_add_methods(target_source):
            pattern = re.compile(
                rf"\.setId\s*\([^)]*\)\s*;[\s\S]{{0,300}}?\.{method_name}\s*\("
            )
            if pattern.search(test_source):
                issues.append(
                    f"Do not call setId() before {method_name}() — its real implementation "
                    "only adds the item while its id is still null (an isNew()-style guard). "
                    f"Call {method_name}() first, then set the id afterward if you need it."
                )

    return issues


def extract_public_api_summary(content: str) -> str:
    """Compact public/protected field types and method signatures for large classes."""
    lines: list[str] = []
    pkg = get_package_name(content)
    cls = get_class_name(content)
    if pkg:
        lines.append(f"package {pkg};")
    if cls:
        lines.append(f"// class {cls}")
    lines.append("// Public / protected API:")

    for m in PUBLIC_FIELD_SIG_RE.finditer(content):
        lines.append(f"{m.group(1).strip()} {m.group(2)};")

    for m in PUBLIC_METHOD_SIG_RE.finditer(content):
        sig = re.sub(r"\s+", " ", m.group(0).strip())
        lines.append(f"{sig};")

    return "\n".join(lines)


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

# ===== NAMING CONVENTION ANALYSIS (Step 4) =====
# Concept-suffix pairs where the same architectural role is named two
# different ways in the same codebase (a real semantic inconsistency, not
# just a style preference) — e.g. one class ends in "Manager", another in
# "Mgr", for what should be a consistent layer-naming convention.
_ABBREVIATION_PAIRS = [
    ("Manager", "Mgr"),
    ("Controller", "Ctrl"),
    ("Repository", "Repo"),
    ("Service", "Svc"),
    ("Configuration", "Config"),
    ("Utility", "Util"),
    ("Implementation", "Impl"),
]


def analyze_naming_conventions(project_root: Path) -> dict:
    """
    Scan all main classes and return BOTH a lexical style summary (camelCase
    vs snake_case ratio) AND semantic inconsistencies: mixed abbreviation
    styles for the same architectural concept, and individual class names
    that break the codebase's dominant casing convention.
    """
    classes = list_main_classes(project_root)
    names = []
    for cls_path in classes:
        content = read_class_content(cls_path)
        name = get_class_name(content) or cls_path.stem
        names.append(name)

    total = len(names)
    camel = sum(1 for n in names if re.match(r'^[A-Z][a-zA-Z0-9]*$', n))
    snake = sum(1 for n in names if '_' in n)
    other = total - camel - snake

    abbreviation_inconsistencies: list[str] = []
    for full, abbrev in _ABBREVIATION_PAIRS:
        has_full = any(n.endswith(full) for n in names)
        has_abbrev = any(n.endswith(abbrev) for n in names)
        if has_full and has_abbrev:
            abbreviation_inconsistencies.append(
                f"Mixed use of '{full}' and '{abbrev}' suffixes across classes in this project"
            )

    naming_outliers: list[str] = []
    if total:
        dominant_is_camel = camel >= snake
        for n in names:
            is_camel = bool(re.match(r'^[A-Z][a-zA-Z0-9]*$', n))
            is_snake = '_' in n
            if dominant_is_camel and is_snake:
                naming_outliers.append(n)
            elif not dominant_is_camel and is_camel and snake > camel:
                naming_outliers.append(n)

    return {
        "total": total,
        "camel_case": camel,
        "snake_case": snake,
        "other": other,
        "ratio_camel": round(camel / total, 2) if total else 0,
        "abbreviation_inconsistencies": abbreviation_inconsistencies,
        "naming_outliers": naming_outliers,
    }


def naming_conventions_prompt_note(summary: dict) -> str | None:
    """
    Turn the naming summary into a short instruction block for the LLM
    prompt, so the analysis actually influences generated code instead of
    being printed and discarded. Returns None if nothing is worth flagging.
    """
    notes: list[str] = []
    notes.extend(summary.get("abbreviation_inconsistencies", []))
    outliers = summary.get("naming_outliers", [])
    if outliers:
        notes.append(
            "These classes break the project's dominant naming style: "
            + ", ".join(outliers[:10])
        )
    if not notes:
        return None
    return (
        "Project naming-convention notes (match the DOMINANT existing style for "
        "any new identifiers you introduce, e.g. local variable and helper names):\n- "
        + "\n- ".join(notes)
    )