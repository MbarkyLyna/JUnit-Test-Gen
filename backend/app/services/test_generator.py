from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Callable
from pathlib import Path

from app.models.schemas import (
    CoverageStats,
    GenerationBreakdown,
    GenerationResult,
    ProjectStats,
)
from app.services import di_resolver, docker_runner, jacoco, java_parser, neo4j_client, ollama_client, test_retriever
from app.services.java_parser import compute_static_stats

COOLDOWN_SECONDS = float(os.environ.get("GENERATION_COOLDOWN_SECONDS", "3"))

# How many total attempts to give the model per class before accepting
# failure/partial coverage. Attempt 1 is the normal generation. Each
# subsequent attempt feeds back either the REAL Maven compiler error (if it
# didn't compile) or the REAL remaining uncovered lines (if it compiled but
# missed the coverage target) — a genuine act -> observe -> retry loop
# instead of a single generate-and-hope shot.
MAX_GENERATION_ATTEMPTS = int(os.environ.get("MAX_GENERATION_ATTEMPTS", "3"))
TARGET_COVERAGE_PCT = 80.0


def _test_path_for_class(class_path: Path, project_root: Path) -> Path:
    """Map src/main/java/.../Foo.java -> src/test/java/.../FooTest.java"""
    rel = class_path.relative_to(project_root)
    parts = list(rel.parts)

    if "main" in parts:
        idx = parts.index("main")
        parts[idx] = "test"

    stem = class_path.stem
    test_name = f"{stem}Test.java" if not stem.endswith("Test") else f"{stem}s.java"
    parts[-1] = test_name

    return project_root / Path(*parts)


def _infer_test_package(test_content: str) -> str | None:
    m = re.search(r"^\s*package\s+([\w.]+)\s*;", test_content, re.MULTILINE)
    return m.group(1) if m else None


def _write_test_file(test_path: Path, content: str, fallback_package: str | None) -> Path:
    test_path.parent.mkdir(parents=True, exist_ok=True)

    if "package " not in content and fallback_package:
        content = f"package {fallback_package};\n\n{content}"

    test_path.write_text(content, encoding="utf-8")
    return test_path


def _class_name_from_path(class_path: Path) -> str:
    content = java_parser.read_class_content(class_path)
    return java_parser.get_class_name(content) or class_path.stem


def _rel_path(path: Path, project_root: Path) -> str:
    return str(path.relative_to(project_root)).replace("\\", "/")


def _resolve_class_path(project_root: Path, class_path_str: str) -> Path:
    normalized = class_path_str.replace("\\", "/")
    if (
        "." in normalized
        and "/" not in normalized
        and not normalized.endswith(".java")
    ):
        fqcn_path = project_root / "src/main/java" / normalized.replace(".", "/")
        fqcn_file = fqcn_path.with_suffix(".java")
        if fqcn_file.exists():
            return fqcn_file

    target = project_root / class_path_str.replace("/", "\\")
    if not target.exists():
        target = project_root / class_path_str
    if not target.exists():
        raise FileNotFoundError(f"Class not found: {class_path_str}")
    return target


def _extract_compile_errors(maven_output: str, max_lines: int = 40) -> str:
    """
    Pull out just the [ERROR] lines from raw Maven output, so the retry
    prompt contains the real, specific compiler error instead of noise.
    """
    lines = [ln for ln in maven_output.splitlines() if "[ERROR]" in ln]
    if not lines:
        tail = maven_output.strip().splitlines()
        return "\n".join(tail[-max_lines:])
    return "\n".join(lines[:max_lines])


def _apply_spring_formatting(
    project_root: Path, session_id: str | None, docker_image: str | None = None
) -> tuple[int, str]:
    """
    Auto-format the currently written test file with Spring's formatter, since
    this project enforces formatting via spring-javaformat-maven-plugin
    independent of whether the code is otherwise correct. Result is logged to
    its own file since the shared full_maven_output.log gets overwritten by
    whatever Maven call runs after this one.
    """
    fmt_exit_code, fmt_output = docker_runner.run_in_docker(
        project_root,
        args=["spring-javaformat:apply", "-q"],
        session_id=session_id,
        activity_message="Auto-formatting generated test file",
        docker_image=docker_image,
    )
    with open("format_apply_output.log", "w", encoding="utf-8") as f:
        f.write(f"EXIT CODE: {fmt_exit_code}\n\n{fmt_output}")
    return fmt_exit_code, fmt_output


def _class_interfaces(content: str) -> list[str]:
    m = java_parser.IMPLEMENTS_RE.search(content)
    if not m:
        return []
    return [java_parser.resolve_type_simple(t.strip()) for t in m.group(1).split(",")]


def _class_superclass(content: str) -> str | None:
    m = java_parser.EXTENDS_RE.search(content)
    if not m:
        return None
    parts = [p.strip() for p in m.group(1).split(",") if p.strip()]
    return java_parser.resolve_type_simple(parts[0]) if parts else None


async def _try_existing_test(
    class_name: str,
    project_root: Path,
    initial_cov: float,
    class_fqcn: str,
    rel: str,
    session_id: str | None,
    docker_image: str | None,
) -> GenerationResult | None:
    """
    test_retriever.py was imported by the original test_generator.py but
    never actually called anywhere — this closes that gap. If the project
    already ships a hand-written test for this class, verify it with real
    Maven before trusting it, and skip LLM generation entirely only if it
    both passes AND already meets the coverage target. Otherwise return
    None so the caller proceeds to the knowledge graph / generation path —
    a passing-but-insufficient existing test is still useful evidence for
    initial_cov, but not a reason to skip improving it.
    """
    existing_path = test_retriever.find_existing_test_for_class(class_name, project_root)
    if existing_path is None:
        return None

    exit_code, _ = jacoco.run_maven_tests(
        project_root,
        session_id=session_id,
        activity_message="Verifying existing hand-written test",
        test_filter=f"{class_name}Test",
        docker_image=docker_image,
    )
    if exit_code != 0:
        # Existing test doesn't even pass on its own — don't trust it as a
        # skip condition, fall through to generation.
        return None

    final_cov = initial_cov
    report = jacoco.find_jacoco_report(project_root)
    if report:
        final_cov = jacoco.parse_jacoco_report(report).per_class.get(class_name, initial_cov)

    if final_cov < TARGET_COVERAGE_PCT:
        return None

    test_source = existing_path.read_text(encoding="utf-8", errors="ignore")
    return GenerationResult(
        class_name=class_name,
        class_fqcn=class_fqcn,
        class_path=rel,
        test_path=_rel_path(existing_path, project_root),
        test_source=test_source,
        success=True,
        message=f"Existing hand-written test already passes and meets target coverage ({final_cov}%). Generation skipped.",
        tests_passed=True,
        initial_coverage_pct=initial_cov,
        final_coverage_pct=final_cov,
        coverage_delta=0.0,
        status="full_coverage",
    )


async def _try_knowledge_graph_hit(
    class_name: str,
    class_path: Path,
    project_root: Path,
    test_path: Path,
    target_source: str,
    initial_cov: float,
    class_fqcn: str,
    rel: str,
    session_id: str | None,
    docker_image: str | None,
) -> GenerationResult | None:
    """
    Step 5, done correctly: look for an exact-name match in the graph, WRITE
    it, but then ACTUALLY VERIFY it compiles and passes with real Maven
    inside the sandbox before ever calling it a success. If it fails
    verification, return None so the caller falls through to normal LLM
    generation instead of reporting a false pass.
    """
    existing_test_code = await neo4j_client.get_test_for_class_async(class_name)
    if existing_test_code is None:
        return None

    pkg = java_parser.get_package_name(target_source)
    _write_test_file(test_path, existing_test_code, pkg)
    _apply_spring_formatting(project_root, session_id, docker_image=docker_image)
    test_source = test_path.read_text(encoding="utf-8", errors="ignore")

    exit_code, maven_out = jacoco.run_maven_tests(
        project_root,
        session_id=session_id,
        activity_message="Verifying knowledge-graph test template",
        test_filter=f"{class_name}Test",
        docker_image=docker_image,
    )
    passed = exit_code == 0

    final_cov = initial_cov
    report = jacoco.find_jacoco_report(project_root)
    if report:
        final_cov = jacoco.parse_jacoco_report(report).per_class.get(class_name, initial_cov)

    if not passed:
        # Do NOT report success on an unverified template. Let the caller
        # fall through to normal generation for this class.
        return None

    delta = round(final_cov - initial_cov, 2)
    if final_cov >= TARGET_COVERAGE_PCT:
        status = "full_coverage"
    elif delta > 0:
        status = "partial_coverage"
    else:
        status = "no_change"

    return GenerationResult(
        class_name=class_name,
        class_fqcn=class_fqcn,
        class_path=rel,
        test_path=_rel_path(test_path, project_root),
        test_source=test_source,
        success=True,
        message=(
            "Test retrieved from knowledge graph and verified against this "
            f"project (exact match). Coverage: {final_cov}%."
        ),
        tests_passed=True,
        initial_coverage_pct=initial_cov,
        final_coverage_pct=final_cov,
        coverage_delta=delta,
        status=status,
    )


async def generate_for_class(
    class_path: Path,
    project_root: Path,
    coverage: CoverageStats | None,
    model: str = ollama_client.DEFAULT_MODEL,
    session_id: str | None = None,
    docker_image: str | None = None,
    naming_note: str | None = None,
) -> GenerationResult:
    rel = _rel_path(class_path, project_root)
    class_name = _class_name_from_path(class_path)
    class_fqcn = java_parser.get_fqn(class_path, project_root)
    test_path = _test_path_for_class(class_path, project_root)

    # Everything the graph-hit path and the generation path both need is
    # computed up front, ONCE, before either branch runs. (The previous
    # version referenced target_source/initial_cov before they existed on
    # the graph-hit branch — a guaranteed UnboundLocalError. Fixed here by
    # simply computing them first.)
    target_source = java_parser.read_class_content(class_path)
    initial_cov = 0.0
    if coverage and class_name in coverage.per_class:
        initial_cov = coverage.per_class[class_name]

    existing_result = await _try_existing_test(
        class_name, project_root, initial_cov, class_fqcn, rel, session_id, docker_image
    )
    if existing_result is not None:
        return existing_result

    graph_result = await _try_knowledge_graph_hit(
        class_name, class_path, project_root, test_path, target_source,
        initial_cov, class_fqcn, rel, session_id, docker_image,
    )
    if graph_result is not None:
        return graph_result

    try:
        dependencies = di_resolver.resolve_dependencies(class_path, project_root)
        referenced_classes = di_resolver.resolve_referenced_classes(
            class_path, project_root, dependencies
        )

        trivial_lines = java_parser.identify_trivial_lines(target_source)
        class_kind = java_parser.detect_class_kind(target_source)
        method_return_types = java_parser.extract_public_method_return_types(target_source)
        guarded_add_methods = (
            java_parser.find_id_guarded_add_methods(target_source)
            if class_kind == "entity"
            else []
        )

        # Structural fallback (Step 5, generalized): no exact name match, but
        # maybe a structurally similar class (shared superclass/interface)
        # exists in the graph. Used ONLY as a style example in the prompt —
        # never written to disk directly, since a different class will not
        # compile against another class's template.
        pattern_example: tuple[str, str] | None = None
        superclass = _class_superclass(target_source)
        interfaces = _class_interfaces(target_source)
        if superclass or interfaces:
            pattern_code, pattern_reason = await neo4j_client.find_similar_test_async(
                superclass, interfaces
            )
            if pattern_code and pattern_reason:
                pattern_example = (pattern_code, pattern_reason)

        uncovered: list[int] = []
        if coverage:
            raw_uncovered = jacoco.get_uncovered_for_class(coverage, class_name)
            uncovered = [ln for ln in raw_uncovered if ln not in trivial_lines]

        base_prompt = ollama_client.build_test_generation_prompt(
            class_name,
            target_source,
            dependencies,
            uncovered,
            initial_cov,
            referenced_classes=referenced_classes or None,
            class_kind=class_kind,
            method_return_types=method_return_types or None,
            guarded_add_methods=guarded_add_methods or None,
            naming_note=naming_note,
            pattern_example=pattern_example,
        )
        pkg = java_parser.get_package_name(target_source)

        current_prompt = base_prompt
        test_code = ""
        test_source = ""
        passed = False
        maven_out = ""
        attempts_used = 0
        current_cov = initial_cov

        for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
            attempts_used = attempt

            test_code = await ollama_client.generate_tests(
                current_prompt,
                model=model,
                target_source=target_source,
                has_di_dependencies=bool(dependencies),
            )

            static_issues = java_parser.check_common_compile_errors(
                test_code, target_source, has_di_dependencies=bool(dependencies)
            )
            if static_issues:
                fix_prompt = (
                    base_prompt
                    + "\n\nFIX THESE SPECIFIC ISSUES in your output:\n"
                    + "\n".join(f"- {i}" for i in static_issues)
                )
                test_code = await ollama_client.generate_tests(
                    fix_prompt,
                    model=model,
                    target_source=target_source,
                    has_di_dependencies=bool(dependencies),
                )

            _write_test_file(test_path, test_code, pkg)
            _apply_spring_formatting(project_root, session_id, docker_image=docker_image)
            test_source = test_path.read_text(encoding="utf-8", errors="ignore")

            exit_code, maven_out = jacoco.run_maven_tests(
                project_root,
                session_id=session_id,
                activity_message=f"Verifying generated class (attempt {attempt}/{MAX_GENERATION_ATTEMPTS})",
                test_filter=f"{class_name}Test",
                docker_image=docker_image,
            )
            passed = exit_code == 0

            if not passed:
                if attempt < MAX_GENERATION_ATTEMPTS:
                    real_errors = _extract_compile_errors(maven_out)
                    current_prompt = (
                        base_prompt
                        + "\n\nYour previous attempt failed to compile/verify with this exact "
                        "Maven output. Fix these specific errors — do not repeat them:\n\n"
                        + real_errors
                        + "\n\nOutput the complete corrected test class, following all the "
                        "original requirements above."
                    )
                continue

            # Compiled and passed. Check the REAL coverage result before
            # declaring done — this is the ReAct step that was missing
            # entirely before: react to the coverage goal, not just to
            # compile/fail.
            report = jacoco.find_jacoco_report(project_root)
            current_cov = initial_cov
            if report:
                current_cov = jacoco.parse_jacoco_report(report).per_class.get(class_name, initial_cov)

            if current_cov >= TARGET_COVERAGE_PCT or attempt == MAX_GENERATION_ATTEMPTS:
                break

            remaining_uncovered: list[int] = []
            if report:
                remaining_uncovered = [
                    ln for ln in jacoco.get_uncovered_for_class(
                        jacoco.parse_jacoco_report(report), class_name
                    )
                    if ln not in trivial_lines
                ]

            if not remaining_uncovered:
                # Nothing further to target — accept the coverage we got.
                break

            current_prompt = (
                base_prompt
                + f"\n\nYour previous test compiled and PASSED, but class line coverage is "
                f"only {current_cov}% (target {TARGET_COVERAGE_PCT}%). These specific lines "
                "are STILL uncovered: "
                + ", ".join(str(l) for l in remaining_uncovered[:80])
                + "\n\nAdd additional @Test methods to the SAME test class covering these "
                "exact lines, in addition to everything you already have. Output the "
                "complete, updated test class (not just the new methods)."
            )

        final_cov = current_cov
        delta = round(final_cov - initial_cov, 2)
        if not passed:
            status = "failed_to_compile"
            msg = (
                f"Test generation failed after {attempts_used} attempt(s), including "
                f"{attempts_used - 1} retry(ies) with real compiler feedback. "
                "Final Maven compilation/execution still failed inside the Docker sandbox."
            )
        elif final_cov >= TARGET_COVERAGE_PCT:
            status = "full_coverage"
            msg = (
                f"Test generated successfully in {attempts_used} attempt(s). "
                f"Coverage reached {final_cov}% (+{delta}%)."
            )
        elif delta > 0:
            status = "partial_coverage"
            msg = (
                f"Test generated successfully in {attempts_used} attempt(s). "
                f"Coverage improved to {final_cov}% (+{delta}%)."
            )
        else:
            status = "no_change"
            msg = f"Test generated and compiled in {attempts_used} attempt(s), but coverage remained unchanged."

        return GenerationResult(
            class_name=class_name,
            class_fqcn=class_fqcn,
            class_path=rel,
            test_path=_rel_path(test_path, project_root),
            test_source=test_source,
            success=True,
            message=msg,
            tests_passed=passed,
            initial_coverage_pct=initial_cov,
            final_coverage_pct=final_cov,
            coverage_delta=delta,
            status=status,
        )
    except Exception as e:
        return GenerationResult(
            class_name=class_name,
            class_fqcn=class_fqcn,
            class_path=rel,
            test_path=str(test_path.relative_to(project_root)) if test_path else None,
            test_source=None,
            success=False,
            message=str(e),
            tests_passed=False,
            initial_coverage_pct=initial_cov,
            final_coverage_pct=initial_cov,
            coverage_delta=0.0,
            status="failed_to_compile",
        )


def _prioritize_classes(
    classes: list[Path], coverage: CoverageStats | None
) -> list[Path]:
    """Order classes by lowest coverage first to maximize impact toward 80%."""
    if not coverage:
        return classes

    def sort_key(p: Path) -> float:
        name = _class_name_from_path(p)
        return coverage.per_class.get(name, 0.0)

    return sorted(classes, key=sort_key)


def build_generation_breakdown(results: list[GenerationResult]) -> GenerationBreakdown:
    total = len(results)
    full = sum(1 for r in results if r.status == "full_coverage")
    partial = sum(1 for r in results if r.status == "partial_coverage")
    failed = sum(1 for r in results if r.status == "failed_to_compile")
    no_change = sum(1 for r in results if r.status == "no_change")

    full_pct = round((full / total) * 100, 1) if total else 0.0
    partial_pct = round((partial / total) * 100, 1) if total else 0.0
    failed_pct = round((failed / total) * 100, 1) if total else 0.0

    headline = (
        f"{full} of {total} classes ({full_pct}%) reached target coverage (>={TARGET_COVERAGE_PCT}%), "
        f"{partial} ({partial_pct}%) partial, {failed} ({failed_pct}%) failed to compile"
    )

    return GenerationBreakdown(
        total_targeted=total,
        full_coverage_count=full,
        partial_coverage_count=partial,
        failed_compile_count=failed,
        no_change_count=no_change,
        summary_headline=headline,
    )


async def _cooldown_between_classes() -> None:
    if COOLDOWN_SECONDS > 0:
        await asyncio.sleep(COOLDOWN_SECONDS)


async def _process_class_batch(
    targets: list[Path],
    project_root: Path,
    coverage: CoverageStats | None,
    model: str,
    *,
    skip_above_80: bool = False,
    stop_at_project_80: bool = False,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    should_abort: Callable[[], bool] | None = None,
    session_id: str | None = None,
    docker_image: str | None = None,
    naming_note: str | None = None,
) -> tuple[list[GenerationResult], CoverageStats | None]:
    results: list[GenerationResult] = []
    total = len(targets)

    for idx, cls_path in enumerate(targets, start=1):
        if should_abort and should_abort():
            break

        rel = _rel_path(cls_path, project_root)
        cls_name = _class_name_from_path(cls_path)

        if skip_above_80 and coverage and coverage.per_class.get(cls_name, 0.0) >= TARGET_COVERAGE_PCT:
            continue

        if on_progress:
            on_progress(idx, total, rel, f"Generating class {idx} of {total}: {cls_name}")

        result = await generate_for_class(
            cls_path, project_root, coverage, model,
            session_id=session_id, docker_image=docker_image, naming_note=naming_note,
        )
        results.append(result)

        report = jacoco.find_jacoco_report(project_root)
        if report:
            coverage = jacoco.parse_jacoco_report(report)

        if stop_at_project_80 and coverage and coverage.line_coverage_pct >= TARGET_COVERAGE_PCT:
            break

        if idx < total and not (should_abort and should_abort()):
            await _cooldown_between_classes()

    return results, coverage


def _project_context(project_root: Path) -> tuple[int, str, str | None]:
    """Compute once per scope call: the project's Java version, the matching
    sandbox image (Step 2), and a naming-convention prompt note (Step 4)."""
    java_version = docker_runner.detect_java_version(project_root)
    docker_image = docker_runner.resolve_docker_image(java_version)
    naming_summary = java_parser.analyze_naming_conventions(project_root)
    naming_note = java_parser.naming_conventions_prompt_note(naming_summary)
    return java_version, docker_image, naming_note


async def generate_tests_for_scope(
    project_root: Path,
    scope: str,
    class_path_str: str | None,
    coverage: CoverageStats | None,
    model: str = ollama_client.DEFAULT_MODEL,
    class_paths: list[str] | None = None,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    should_abort: Callable[[], bool] | None = None,
    session_id: str | None = None,
) -> tuple[list[GenerationResult], GenerationBreakdown, str]:
    results: list[GenerationResult] = []
    _, docker_image, naming_note = _project_context(project_root)

    if scope == "class":
        if not class_path_str:
            raise ValueError("class_path required for scope=class")
        target = _resolve_class_path(project_root, class_path_str)
        if on_progress:
            on_progress(1, 1, _rel_path(target, project_root), f"Generating class 1 of 1")
        result = await generate_for_class(
            target, project_root, coverage, model,
            session_id=session_id, docker_image=docker_image, naming_note=naming_note,
        )
        results.append(result)
        _, maven_tail = jacoco.run_maven_tests(
            project_root,
            session_id=session_id,
            activity_message="Running final Maven verification",
            docker_image=docker_image,
        )
        breakdown = build_generation_breakdown(results)
        return results, breakdown, maven_tail[-3000:]

    if scope == "classes":
        paths = class_paths or ([class_path_str] if class_path_str else [])
        if not paths:
            raise ValueError("class_paths required for scope=classes")

        targets = [_resolve_class_path(project_root, p) for p in paths]
        results, _ = await _process_class_batch(
            targets,
            project_root,
            coverage,
            model,
            on_progress=on_progress,
            should_abort=should_abort,
            session_id=session_id,
            docker_image=docker_image,
            naming_note=naming_note,
        )
        _, maven_tail = jacoco.run_maven_tests(
            project_root,
            session_id=session_id,
            activity_message="Running final Maven verification",
            docker_image=docker_image,
        )
        breakdown = build_generation_breakdown(results)
        return results, breakdown, maven_tail[-3000:]

    if scope == "project":
        # Step 3: only real, relevant, in-scope classes — Swing/AWT,
        # package-info/module-info, and pure interfaces are excluded here
        # instead of being silently fed to the LLM as generation targets.
        classes = java_parser.list_generation_targets(project_root)
        classes = _prioritize_classes(classes, coverage)
        if on_progress and classes:
            on_progress(0, len(classes), "", f"Preparing project generation for {len(classes)} class(es)")

        results, _ = await _process_class_batch(
            classes,
            project_root,
            coverage,
            model,
            skip_above_80=True,
            stop_at_project_80=True,
            on_progress=on_progress,
            should_abort=should_abort,
            session_id=session_id,
            docker_image=docker_image,
            naming_note=naming_note,
        )
        _, maven_tail = jacoco.run_maven_tests(
            project_root,
            session_id=session_id,
            activity_message="Running final Maven verification",
            docker_image=docker_image,
        )
        breakdown = build_generation_breakdown(results)
        return results, breakdown, maven_tail[-3000:]

    raise ValueError(f"Unknown scope: {scope}")


async def build_project_stats(project_root: Path, analyzed: bool) -> ProjectStats:
    static = compute_static_stats(project_root)
    coverage = None
    if analyzed:
        report = jacoco.find_jacoco_report(project_root)
        if report:
            coverage = jacoco.parse_jacoco_report(report)
    return ProjectStats(static=static, coverage=coverage, analyzed=analyzed)