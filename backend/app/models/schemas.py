from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FileNode(BaseModel):
    name: str
    path: str
    type: str  # "file" | "directory"
    children: list[FileNode] = Field(default_factory=list)


class StaticStats(BaseModel):
    class_count: int = 0
    package_count: int = 0
    test_count: int = 0


class CoverageStats(BaseModel):
    line_coverage_pct: float = 0.0
    branch_coverage_pct: float = 0.0
    instruction_coverage_pct: float = 0.0
    per_class: dict[str, float] = Field(default_factory=dict)
    per_package: dict[str, float] = Field(default_factory=dict)
    uncovered_lines: dict[str, list[int]] = Field(default_factory=dict)


class ProjectStats(BaseModel):
    static: StaticStats
    coverage: CoverageStats | None = None
    analyzed: bool = False


class UploadResponse(BaseModel):
    session_id: str
    tree: FileNode
    stats: ProjectStats
    project_root: str


class CloneRequest(BaseModel):
    repo_url: str = Field(description="Public GitHub repository URL")
    branch: str | None = Field(default=None, description="Optional branch name")


class AnalyzeResponse(BaseModel):
    session_id: str
    stats: ProjectStats
    maven_output_tail: str = ""


class GenerateRequest(BaseModel):
    scope: str = Field(description="'class', 'classes', or 'project'")
    class_path: str | None = Field(
        default=None, description="Relative path to .java file when scope=class"
    )
    class_paths: list[str] | None = Field(
        default=None, description="Relative paths when scope=classes"
    )


class GenerationResult(BaseModel):
    class_name: str = ""
    class_fqcn: str = ""
    class_path: str
    test_path: str | None = None
    test_source: str | None = None
    success: bool
    message: str
    tests_passed: bool = False
    initial_coverage_pct: float = 0.0
    final_coverage_pct: float = 0.0
    coverage_delta: float = 0.0
    status: str = "no_change"


class SessionStatusResponse(BaseModel):
    session_id: str
    active: bool
    operation: str | None = None
    message: str = ""
    elapsed_seconds: int = 0


class GenerationBreakdown(BaseModel):
    total_targeted: int = 0
    full_coverage_count: int = 0
    partial_coverage_count: int = 0
    failed_compile_count: int = 0
    no_change_count: int = 0
    summary_headline: str = ""


class GenerateResponse(BaseModel):
    session_id: str
    results: list[GenerationResult]
    breakdown: GenerationBreakdown | None = None
    stats: ProjectStats
    maven_output_tail: str = ""


class GenerateJobStartResponse(BaseModel):
    job_id: str
    session_id: str
    scope: str
    message: str = ""


class GenerateJobStatus(BaseModel):
    job_id: str
    session_id: str
    scope: str
    status: str
    current_index: int = 0
    total: int = 0
    current_class: str = ""
    message: str = ""
    results: list[GenerationResult] = Field(default_factory=list)
    breakdown: GenerationBreakdown | None = None
    stats: ProjectStats | None = None
    maven_output_tail: str = ""
    error: str | None = None
