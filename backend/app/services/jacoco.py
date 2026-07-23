from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from app.models.schemas import CoverageStats
from app.services import docker_runner

JACOCO_PLUGIN_SNIPPET = """
            <plugin>
                <groupId>org.jacoco</groupId>
                <artifactId>jacoco-maven-plugin</artifactId>
                <version>0.8.12</version>
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


def inject_jacoco_plugin(pom_path: Path) -> bool:
    """Inject JaCoCo plugin into pom.xml if missing. Returns True if modified."""
    if has_jacoco_plugin(pom_path):
        return False

    content = pom_path.read_text(encoding="utf-8", errors="ignore")

    if "<plugins>" in content:
        content = content.replace("<plugins>", f"<plugins>{JACOCO_PLUGIN_SNIPPET}", 1)
    elif "</build>" in content:
        content = content.replace(
            "</build>",
            f"        <plugins>{JACOCO_PLUGIN_SNIPPET}\n        </plugins>\n    </build>",
            1,
        )
    else:
        content = content.replace(
            "</project>",
            f"    <build>\n        <plugins>{JACOCO_PLUGIN_SNIPPET}\n        </plugins>\n    </build>\n</project>",
            1,
        )

    pom_path.write_text(content, encoding="utf-8")
    return True


def run_maven_tests(project_root: Path, timeout: int = 600) -> tuple[int, str]:
    """Run mvn test strictly inside a Docker container sandbox."""
    return docker_runner.run_in_docker(project_root, args=["test", "-DskipTests=false", "-q"], timeout=timeout)



def find_jacoco_report(project_root: Path) -> Path | None:
    candidates = list(project_root.rglob("jacoco.xml"))
    # Prefer site/jacoco report
    for c in candidates:
        if "site" in c.parts and "jacoco" in c.parts:
            return c
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

    # Root-level counters
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
