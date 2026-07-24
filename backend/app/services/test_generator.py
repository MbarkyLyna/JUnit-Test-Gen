from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

from app.models.schemas import (
    CoverageStats,
    GenerationBreakdown,
    GenerationResult,
    ProjectStats,
)
from app.services import di_resolver, jacoco, java_parser, ollama_client
from app.services.java_parser import compute_static_stats

COOLDOWN_SECONDS = float(os.environ.get("GENERATION_COOLDOWN_SECONDS", "3"))


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
    import re

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
    target = project_root / class_path_str.replace("/", "\\")
    if not target.exists():
        target = project_root / class_path_str
    if not target.exists():
        raise FileNotFoundError(f"Class not found: {class_path_str}")
    return target


async def generate_for_class(
    class_path: Path,
    project_root: Path,
    coverage: CoverageStats | None,
    model: str = ollama_client.DEFAULT_MODEL,
) -> GenerationResult:
    rel = _rel_path(class_path, project_root)
    class_name = _class_name_from_path(class_path)
    test_path = _test_path_for_class(class_path, project_root)

    initial_cov = 0.0
    if coverage and class_name in coverage.per_class:
        initial_cov = coverage.per_class[class_name]

    try:
        target_source = java_parser.read_class_content(class_path)
        dependencies = di_resolver.resolve_dependencies(class_path, project_root)

        trivial_lines = java_parser.identify_trivial_lines(target_source)

        uncovered: list[int] = []
        if coverage:
            raw_uncovered = jacoco.get_uncovered_for_class(coverage, class_name)
            uncovered = [ln for ln in raw_uncovered if ln not in trivial_lines]

        prompt = ollama_client.build_test_generation_prompt(
            class_name, target_source, dependencies, uncovered, initial_cov
        )
        test_code = await ollama_client.generate_tests(prompt, model=model)

        pkg = java_parser.get_package_name(target_source)
        _write_test_file(test_path, test_code, pkg)

        exit_code, maven_out = jacoco.run_maven_tests(project_root)
        passed = exit_code == 0

        final_cov = initial_cov
        report = jacoco.find_jacoco_report(project_root)
        if report:
            new_cov = jacoco.parse_jacoco_report(report)
            final_cov = new_cov.per_class.get(class_name, initial_cov)

        delta = round(final_cov - initial_cov, 2)
        if not passed:
            status = "failed_to_compile"
            msg = "Test generated but failed Maven compilation/execution inside Docker container sandbox."
        elif final_cov >= 80.0:
            status = "full_coverage"
            msg = f"Test generated successfully. Coverage reached {final_cov}% (+{delta}%)."
        elif delta > 0:
            status = "partial_coverage"
            msg = f"Test generated successfully. Coverage improved to {final_cov}% (+{delta}%)."
        else:
            status = "no_change"
            msg = "Test generated and compiled, but coverage remained unchanged."

        return GenerationResult(
            class_name=class_name,
            class_path=rel,
            test_path=_rel_path(test_path, project_root),
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
            class_path=rel,
            test_path=str(test_path.relative_to(project_root)) if test_path else None,
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
        f"{full} of {total} classes ({full_pct}%) reached target coverage (≥80%), "
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
) -> tuple[list[GenerationResult], CoverageStats | None]:
    results: list[GenerationResult] = []
    total = len(targets)

    for idx, cls_path in enumerate(targets, start=1):
        if should_abort and should_abort():
            break

        rel = _rel_path(cls_path, project_root)
        cls_name = _class_name_from_path(cls_path)

        if skip_above_80 and coverage and coverage.per_class.get(cls_name, 0.0) >= 80.0:
            continue

        if on_progress:
            on_progress(idx, total, rel, f"Generating class {idx} of {total}: {cls_name}")

        result = await generate_for_class(cls_path, project_root, coverage, model)
        results.append(result)

        report = jacoco.find_jacoco_report(project_root)
        if report:
            coverage = jacoco.parse_jacoco_report(report)

        if stop_at_project_80 and coverage and coverage.line_coverage_pct >= 80.0:
            break

        if idx < total and not (should_abort and should_abort()):
            await _cooldown_between_classes()

    return results, coverage


async def generate_tests_for_scope(
    project_root: Path,
    scope: str,
    class_path_str: str | None,
    coverage: CoverageStats | None,
    model: str = ollama_client.DEFAULT_MODEL,
    class_paths: list[str] | None = None,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    should_abort: Callable[[], bool] | None = None,
) -> tuple[list[GenerationResult], GenerationBreakdown, str]:
    results: list[GenerationResult] = []

    if scope == "class":
        if not class_path_str:
            raise ValueError("class_path required for scope=class")
        target = _resolve_class_path(project_root, class_path_str)
        if on_progress:
            on_progress(1, 1, _rel_path(target, project_root), f"Generating class 1 of 1")
        result = await generate_for_class(target, project_root, coverage, model)
        results.append(result)
        _, maven_tail = jacoco.run_maven_tests(project_root)
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
        )
        _, maven_tail = jacoco.run_maven_tests(project_root)
        breakdown = build_generation_breakdown(results)
        return results, breakdown, maven_tail[-3000:]

    if scope == "project":
        classes = java_parser.list_main_classes(project_root)
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
        )
        _, maven_tail = jacoco.run_maven_tests(project_root)
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
