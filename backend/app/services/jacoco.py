from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from app.models.schemas import CoverageStats
from app.services import docker_runner

JACOCO_REPORT_EXECUTION = """
                    <execution>
                        <id>report</id>
                        <phase>test</phase>
                        <goals>
                            <goal>report</goal>
                        </goals>
                    </execution>"""

JACOCO_REPORT_RELATIVE = Path("target/site/jacoco/jacoco.xml")


def resolve_jacoco_version(java_version: int) -> str:
    """
    JaCoCo version compatibility is real and version-specific: an old JaCoCo
    can't parse bytecode from a newer javac, and some old bytecode trips up
    newer JaCoCo. Pick a version known to work with the project's declared
    Java version instead of pinning one version for every project.
    """
    if java_version <= 7:
        return "0.7.9"
    if java_version <= 10:
        return "0.8.5"
    if java_version <= 16:
        return "0.8.7"
    if java_version <= 20:
        return "0.8.10"
    return "0.8.12"


def _plugin_snippet(version: str) -> str:
    return f"""
            <plugin>
                <groupId>org.jacoco</groupId>
                <artifactId>jacoco-maven-plugin</artifactId>
                <version>{version}</version>
                <executions>
                    <execution>
                        <goals>
                            <goal>prepare-agent</goal>
                        </goals>
                    </execution>
                    <execution>
                        <id>report</id>
                        <phase>test</phase>
                        <goals>
                            <goal>report</goal>
                        </goals>
                    </execution>
                </executions>
            </plugin>
"""


def has_jacoco_plugin(pom_path: Path) -> bool:
    content = pom_path.read_text(encoding="utf-8", errors="ignore")
    return "jacoco-maven-plugin" in content


def has_jacoco_report_execution(pom_path: Path) -> bool:
    content = pom_path.read_text(encoding="utf-8", errors="ignore")
    return "<goal>report</goal>" in content


def ensure_jacoco_report_execution(pom_path: Path) -> bool:
    """Add report execution to an existing JaCoCo plugin block when missing."""
    content = pom_path.read_text(encoding="utf-8", errors="ignore")
    if has_jacoco_report_execution(pom_path) or not has_jacoco_plugin(pom_path):
        return False

    marker = "<artifactId>jacoco-maven-plugin</artifactId>"
    idx = content.find(marker)
    if idx == -1:
        return False

    plugin_start = content.rfind("<plugin>", 0, idx)
    plugin_end = content.find("</plugin>", idx)
    if plugin_start == -1 or plugin_end == -1:
        return False

    plugin_block = content[plugin_start:plugin_end]
    if "<executions>" in plugin_block:
        new_plugin = plugin_block.replace(
            "</executions>",
            f"{JACOCO_REPORT_EXECUTION}\n                </executions>",
            1,
        )
    else:
        new_plugin = (
            plugin_block
            + f"""
                <executions>{JACOCO_REPORT_EXECUTION}
                </executions>"""
        )

    pom_path.write_text(content[:plugin_start] + new_plugin + content[plugin_end:], encoding="utf-8")
    return True


def inject_jacoco_plugin(pom_path: Path, java_version: int = 17) -> bool:
    """Ensure a JaCoCo plugin version compatible with java_version, plus the
    report execution, exist in pom.xml. Returns True if modified."""
    modified = False

    if not has_jacoco_plugin(pom_path):
        version = resolve_jacoco_version(java_version)
        content = pom_path.read_text(encoding="utf-8", errors="ignore")
        snippet = _plugin_snippet(version)

        if "<plugins>" in content:
            content = content.replace("<plugins>", f"<plugins>{snippet}", 1)
        elif "</build>" in content:
            content = content.replace(
                "</build>",
                f"        <plugins>{snippet}\n        </plugins>\n    </build>",
                1,
            )
        else:
            content = content.replace(
                "</project>",
                f"    <build>\n        <plugins>{snippet}\n        </plugins>\n    </build>\n</project>",
                1,
            )

        pom_path.write_text(content, encoding="utf-8")
        modified = True

    if ensure_jacoco_report_execution(pom_path):
        modified = True

    return modified


def run_maven_tests(
    project_root: Path,
    timeout: int = 600,
    session_id: str | None = None,
    activity_message: str = "Running Maven tests inside Docker container sandbox",
    test_filter: str | None = None,
    docker_image: str | None = None,
) -> tuple[int, str]:
    """
    Run mvn test + jacoco:report strictly inside a Docker container sandbox,
    using the sandbox image that matches this project's Java version so
    bytecode/toolchain mismatches don't cause spurious failures.
    """
    args = ["test", "jacoco:report", "-DskipTests=false"]
    if test_filter:
        args.append(f"-Dtest={test_filter}")

    return docker_runner.run_in_docker(
        project_root,
        args=args,
        timeout=timeout,
        session_id=session_id,
        activity_message=activity_message,
        docker_image=docker_image,
    )


def find_jacoco_report(project_root: Path) -> Path | None:
    canonical = project_root / JACOCO_REPORT_RELATIVE
    if canonical.is_file():
        return canonical

    for candidate in sorted(project_root.rglob("target/site/jacoco/jacoco.xml")):
        return candidate

    candidates = list(project_root.rglob("jacoco.xml"))
    for candidate in candidates:
        if "site" in candidate.parts and "jacoco" in candidate.parts:
            return candidate
    return candidates[0] if candidates else None


def _pct(covered: int, missed: int) -> float:
    total = covered + missed
    return round((covered / total) * 100, 2) if total else 0.0


def parse_jacoco_report(report_path: Path) -> CoverageStats:
    tree = ET.parse(report_path)
    root = tree.getroot()

    stats = CoverageStats()
    per_class: dict[str, float] = {}
    per_package: dict[str, float] = {}
    uncovered_lines: dict[str, list[int]] = {}

    for package in root.findall("package"):
        pkg_name = package.get("name", "").replace("/", ".")
        pkg_covered = pkg_missed = 0

        for clazz in package.findall("class"):
            class_name = clazz.get("name", "").replace("/", ".")
            simple = class_name.rsplit(".", 1)[-1]
            cls_covered = cls_missed = 0
            missed_line_nums: list[int] = []

            for counter in clazz.findall("counter"):
                ctype = counter.get("type")
                if ctype == "LINE":
                    c = int(counter.get("covered", 0))
                    m = int(counter.get("missed", 0))
                    cls_covered, cls_missed = c, m
                    per_class[simple] = _pct(c, m)
                    pkg_covered += c
                    pkg_missed += m

            for sourcefile in package.findall("sourcefile"):
                sf_name = sourcefile.get("name", "")
                if not sf_name.endswith(f"{simple}.java") and simple not in sf_name:
                    continue
                for line in sourcefile.findall("line"):
                    nr = int(line.get("nr", 0))
                    ci = int(line.get("ci", 0))
                    mi = int(line.get("mi", 0))
                    if mi > 0 and ci == 0:
                        missed_line_nums.append(nr)

            if missed_line_nums:
                uncovered_lines[simple] = sorted(set(missed_line_nums))

        if pkg_name:
            per_package[pkg_name] = _pct(pkg_covered, pkg_missed)

    for counter in root.findall("counter"):
        ctype = counter.get("type")
        c = int(counter.get("covered", 0))
        m = int(counter.get("missed", 0))
        if ctype == "LINE":
            stats.line_coverage_pct = _pct(c, m)
        elif ctype == "BRANCH":
            stats.branch_coverage_pct = _pct(c, m)
        elif ctype == "INSTRUCTION":
            stats.instruction_coverage_pct = _pct(c, m)

    stats.per_class = per_class
    stats.per_package = per_package
    stats.uncovered_lines = uncovered_lines
    return stats


def get_uncovered_for_class(coverage: CoverageStats, class_name: str) -> list[int]:
    return coverage.uncovered_lines.get(class_name, [])