from __future__ import annotations

from pathlib import Path

from app.services import java_parser

LARGE_CLASS_CHAR_THRESHOLD = java_parser.LARGE_CLASS_CHAR_THRESHOLD


def _is_in_project_type(
    type_simple: str, class_index: dict[str, list[Path]], project_root: Path
) -> bool:
    """Mirror of external_bean classification in resolve_dependencies — inverse check."""
    if type_simple in class_index:
        return True
    return bool(java_parser.find_implementations(project_root, type_simple))


def _locate_in_project_class(
    type_simple: str, class_index: dict[str, list[Path]], project_root: Path
) -> Path | None:
    """Find a source file for an in-project type (same strategy as DI resolution)."""
    if type_simple in class_index:
        return class_index[type_simple][0]
    implementors = java_parser.find_implementations(project_root, type_simple)
    if implementors:
        return implementors[0][0]
    return None


def _build_referenced_class_entry(path: Path, project_root: Path, type_simple: str) -> dict:
    content = java_parser.read_class_content(path)
    fqn = java_parser.get_fqn(path, project_root)
    if len(content) > LARGE_CLASS_CHAR_THRESHOLD:
        context = java_parser.extract_public_api_summary(content)
        context_kind = "signatures"
    else:
        context = content[:4000]
        context_kind = "full_source"
    return {
        "class_name": type_simple,
        "fqn": fqn,
        "file_path": str(path.relative_to(project_root)).replace("\\", "/"),
        "context_kind": context_kind,
        "source": context,
    }


def resolve_referenced_classes(
    target_path: Path,
    project_root: Path,
    dependencies: list[dict],
) -> list[dict]:
    """
    Find in-project classes referenced in the target's fields/methods but not
    already listed as DI-injected dependencies.
    """
    content = java_parser.read_class_content(target_path)
    target_class = java_parser.get_class_name(content) or target_path.stem
    class_index = java_parser.index_project_classes(project_root)
    injected_types = {dep["dependency_type"] for dep in dependencies}

    referenced: list[dict] = []
    seen_fqcn: set[str] = set()

    for type_simple in java_parser.extract_referenced_types(content):
        if type_simple == target_class or type_simple in injected_types:
            continue
        if not _is_in_project_type(type_simple, class_index, project_root):
            continue

        class_path = _locate_in_project_class(type_simple, class_index, project_root)
        if class_path is None:
            continue

        fqn = java_parser.get_fqn(class_path, project_root)
        if fqn in seen_fqcn:
            continue
        seen_fqcn.add(fqn)
        referenced.append(_build_referenced_class_entry(class_path, project_root, type_simple))

    return referenced


def resolve_dependencies(
    target_path: Path, project_root: Path
) -> list[dict]:
    """
    Static DI resolution for a target class.
    Returns list of resolved dependency dicts with implementation details.
    """
    content = java_parser.read_class_content(target_path)
    imports = java_parser.parse_imports(content)
    raw_deps = java_parser.extract_dependencies(content)
    class_index = java_parser.index_project_classes(project_root)

    resolved: list[dict] = []

    for dep in raw_deps:
        type_simple = dep["type"]
        fqn = imports.get(type_simple, type_simple)

        injection_qualifier = dep.get("qualifier")

        entry: dict = {
            "dependency_type": type_simple,
            "dependency_fqn": fqn,
            "field_name": dep["name"],
            "injection_source": dep["source"],
            "injection_qualifier": injection_qualifier,
            "resolution": "unknown",
            "implementations": [],
            "ambiguity_note": None,
        }

        # Direct concrete class in project
        if type_simple in class_index:
            candidates = class_index[type_simple]
            impls = []
            for cp in candidates:
                impl_content = java_parser.read_class_content(cp)
                impls.append(
                    {
                        "class_name": java_parser.get_class_name(impl_content),
                        "fqn": java_parser.get_fqn(cp, project_root),
                        "file_path": str(cp.relative_to(project_root)),
                        "annotations": java_parser.get_class_annotations(impl_content),
                        "source": impl_content[:4000],
                    }
                )
            if len(impls) == 1:
                entry["resolution"] = "concrete"
                entry["implementations"] = impls
            elif len(impls) > 1:
                entry["resolution"] = "multiple_concrete"
                entry["implementations"] = impls
                entry["ambiguity_note"] = (
                    f"Multiple classes named {type_simple} found; "
                    "check @Primary/@Profile/@Qualifier for disambiguation."
                )
            else:
                entry["resolution"] = "concrete"
                entry["implementations"] = impls
            resolved.append(entry)
            continue

        # Interface / abstract: find implementors
        implementors = java_parser.find_implementations(project_root, type_simple)
        impl_details = []
        for impl_path, impl_cls in implementors:
            impl_content = java_parser.read_class_content(impl_path)
            impl_details.append(
                {
                    "class_name": impl_cls,
                    "fqn": java_parser.get_fqn(impl_path, project_root),
                    "file_path": str(impl_path.relative_to(project_root)),
                    "annotations": java_parser.get_class_annotations(impl_content),
                    "source": impl_content[:4000],
                }
            )

        if len(impl_details) == 0:
            entry["resolution"] = "external_bean"
            entry["is_external_bean"] = True
            entry["ambiguity_note"] = (
                f"No implementation found in source for '{type_simple}' (framework or external bean "
                "e.g., RestTemplate, ObjectMapper, JdbcTemplate, EntityManager, Clock). "
                "Treat as an opaque external type and mock it using @Mock or @MockBean."
            )
        elif len(impl_details) == 1:
            entry["resolution"] = "single_implementation"
            entry["implementations"] = impl_details
        else:
            # Disambiguation via @Qualifier on injection site
            if injection_qualifier:
                qualified = [
                    i
                    for i in impl_details
                    if i["annotations"].get("qualifier") == injection_qualifier
                ]
                if len(qualified) == 1:
                    entry["resolution"] = "qualifier"
                    entry["implementations"] = qualified
                    entry["ambiguity_note"] = (
                        f"Resolved via @Qualifier(\"{injection_qualifier}\")."
                    )
                    resolved.append(entry)
                    continue
                if len(qualified) > 1:
                    entry["resolution"] = "ambiguous_qualifier"
                    entry["implementations"] = qualified
                    entry["ambiguity_note"] = (
                        f"Multiple beans match @Qualifier(\"{injection_qualifier}\")."
                    )
                    resolved.append(entry)
                    continue

            # Disambiguation via @Primary
            primaries = [i for i in impl_details if i["annotations"].get("primary")]
            if len(primaries) == 1:
                entry["resolution"] = "primary"
                entry["implementations"] = primaries
                entry["ambiguity_note"] = "Resolved via @Primary."
            else:
                # Group by profile
                profiles = {}
                for impl in impl_details:
                    prof = impl["annotations"].get("profile", "default")
                    profiles.setdefault(str(prof), []).append(impl)

                if len(profiles) == 1 and len(impl_details) > 1:
                    entry["resolution"] = "ambiguous"
                    entry["implementations"] = impl_details
                    entry["ambiguity_note"] = (
                        f"Multiple implementations of {type_simple} with no @Primary. "
                        "All candidates included for LLM context."
                    )
                else:
                    entry["resolution"] = "profile_variants"
                    entry["implementations"] = impl_details
                    entry["ambiguity_note"] = (
                        "Multiple profile-specific implementations; "
                        "all candidates included with profile annotations."
                    )

        resolved.append(entry)

    return resolved
