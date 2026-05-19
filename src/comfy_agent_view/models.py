from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Profile = Literal["safe", "private", "full", "debug"]
UseObjectInfo = Literal["auto", "never", "require"]


class SourceInfo(BaseModel):
    path: str
    name: str | None = None
    format_detected: str = "comfy_ui_workflow"


class WarningItem(BaseModel):
    level: Literal["info", "warning", "error"] = "warning"
    code: str
    message: str
    node_id: int | None = None
    input: str | None = None
    link_id: int | None = None


class WorkflowListItem(BaseModel):
    path: str
    name: str
    size_bytes: int
    modified_at: str
    format_guess: str = "comfy_ui_workflow"


class WorkflowListResult(BaseModel):
    format: Literal["comfy_workflow_list_v1"] = "comfy_workflow_list_v1"
    root: str
    workflows: list[WorkflowListItem]
    warnings: list[WarningItem] = Field(default_factory=list)


class PromptPresence(BaseModel):
    present: bool
    redacted: bool
    token_count_estimate: int | None = None


class SummaryResult(BaseModel):
    format: Literal["comfy_workflow_summary_v1"] = "comfy_workflow_summary_v1"
    source: SourceInfo
    profile: Profile
    kind: dict[str, Any]
    pipeline: dict[str, Any]
    models: dict[str, Any]
    generation: dict[str, Any]
    prompts: dict[str, PromptPresence]
    stats: dict[str, Any]
    warnings: list[WarningItem] = Field(default_factory=list)


class NormalizedNode(BaseModel):
    id: int | str
    type: str
    title: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: list[dict[str, Any]] | None = None
    unknown_widgets: list[Any] | None = None


class NormalizeResult(BaseModel):
    format: Literal["comfy_agent_view_v1"] = "comfy_agent_view_v1"
    source: SourceInfo
    profile: Profile
    nodes: list[NormalizedNode]
    models: dict[str, Any]
    generation: dict[str, Any]
    prompts: dict[str, Any]
    warnings: list[WarningItem] = Field(default_factory=list)


class ObjectInfoFetchResult(BaseModel):
    format: Literal["comfy_object_info_cache_v1"] = "comfy_object_info_cache_v1"
    ok: bool
    source_url: str
    path: str
    node_count: int
    message: str


class BrokenLink(BaseModel):
    link_id: int | str
    origin_id: int | str | None = None
    origin_slot: int | None = None
    target_id: int | str | None = None
    target_slot: int | None = None
    reason: str


class RepairResult(BaseModel):
    format: Literal["comfy_workflow_repair_report_v1"] = "comfy_workflow_repair_report_v1"
    ok: bool
    source: SourceInfo
    broken_links: list[BrokenLink]
    would_remove_links: list[int | str]
    written_path: str | None = None
    message: str
    warnings: list[WarningItem] = Field(default_factory=list)


class LogFileStatus(BaseModel):
    file: str
    exists: bool
    readable: bool
    size_bytes: int | None = None
    mtime: str | None = None
    bytes_read: int = 0
    lines_read: int = 0
    truncated: bool = False
    error: str | None = None


class RuntimeDiagnosticEvent(BaseModel):
    file: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    timestamp: str | None = None
    severity: str
    category: str
    source: str | None = None
    node_type: str | None = None
    package: str | None = None
    exception_type: str | None = None
    extension: str | None = None
    message: str
    fingerprint: str
    count: int = 1
    confidence: str = "low"


class RuntimeStaticDiagnostics(BaseModel):
    normalize_ok: bool
    broken_link_count: int
    unknown_widget_nodes: int
    warnings: list[WarningItem] = Field(default_factory=list)


class RuntimeObjectInfoDiagnostics(BaseModel):
    path: str
    exists: bool
    valid: bool
    stale: bool
    node_count: int = 0
    missing_node_types: list[str] = Field(default_factory=list)
    stale_reason: str | None = None


class RuntimeLogsDiagnostics(BaseModel):
    files_checked: list[str]
    file_status: list[LogFileStatus]
    events_scanned: int
    events_returned: int
    noise_counts: dict[str, int] = Field(default_factory=dict)
    matched_errors: list[RuntimeDiagnosticEvent] = Field(default_factory=list)


class RuntimeRepairPlanItem(BaseModel):
    kind: str
    action: str
    confidence: str
    message: str


class RuntimeLoadDiagnosticResult(BaseModel):
    format: Literal["comfy_runtime_diagnostic_v1"] = "comfy_runtime_diagnostic_v1"
    workflow: str
    source: SourceInfo
    static: RuntimeStaticDiagnostics
    object_info: RuntimeObjectInfoDiagnostics
    logs: RuntimeLogsDiagnostics
    optional_inputs: dict[str, str] = Field(default_factory=dict)
    frontend_error: dict[str, Any] = Field(default_factory=dict)
    repair_plan: list[RuntimeRepairPlanItem] = Field(default_factory=list)
    warnings: list[WarningItem] = Field(default_factory=list)
