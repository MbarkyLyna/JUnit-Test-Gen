from __future__ import annotations

from pathlib import Path

from app.services import java_parser


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
