from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from pydantic import ValidationError

from .config import load_config, object_info_cache_path
from .models import (
    BrokenLink,
    LogFileStatus,
    NormalizeResult,
    NormalizedNode,
    ObjectInfoFetchResult,
    Profile,
    PromptPresence,
    RepairResult,
    RuntimeDiagnosticEvent,
    RuntimeAction,
    RuntimeLoadDiagnosticResult,
    RuntimeLogsDiagnostics,
    RuntimeObjectInfoDiagnostics,
    RuntimeRepairPlanItem,
    RuntimeSummary,
    RuntimeStaticDiagnostics,
    SourceInfo,
    SummaryResult,
    UseObjectInfo,
    WarningItem,
    AppliedWorkflowPatchOperation,
    WorkflowListItem,
    WorkflowListResult,
    WorkflowPatchOperation,
    WorkflowPatchResult,
    WorkflowPatchSpec,
)

UI_ONLY_NODE_FIELDS = {
    "pos",
    "size",
    "flags",
    "order",
    "mode",
    "color",
    "bgcolor",
}

WIDGET_MAPS: dict[str, list[str]] = {
    "CheckpointLoaderSimple": ["ckpt_name"],
    "VAELoader": ["vae_name"],
    "UNETLoader": ["unet_name", "weight_dtype"],
    "CLIPLoader": ["clip_name", "type", "device"],
    "LoraLoader": ["lora_name", "strength_model", "strength_clip"],
    "CLIPTextEncode": ["text"],
    "EmptyLatentImage": ["width", "height", "batch_size"],
    "KSampler": ["seed", "control_after_generate", "steps", "cfg", "sampler_name", "scheduler", "denoise"],
    "KSamplerAdvanced": [
        "add_noise",
        "noise_seed",
        "steps",
        "cfg",
        "sampler_name",
        "scheduler",
        "start_at_step",
        "end_at_step",
        "return_with_leftover_noise",
    ],
    "SaveImage": ["filename_prefix"],
    "LoadImage": ["image", "upload"],
    "ControlNetLoader": ["control_net_name"],
}

PRIVATE_MODEL_KEYS = {"checkpoints", "loras", "vae", "controlnet", "unet", "clip"}
PRIVATE_INPUT_KEYS = {"filename_prefix", "image", "upload"}
LOG_FILES = ("comfyui.log", "comfyui.prev.log", "comfyui.prev2.log")
LOG_TAIL_BYTES = 1024 * 1024
LOG_TAIL_LINES = 5000
LOG_MAX_EVENTS = 200
LOG_MAX_MESSAGE = 500

_ISO_TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s*-\s*")
_BRACKET_TS_RE = re.compile(r"^\[(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]\s*")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\\\s:]+\\)*([^\\\s:]+)")
_POSIX_PATH_RE = re.compile(r"(?<!\w)/(?:[^\s/]+/)+([^\s/]+)")
_URL_RE = re.compile(r"https?://[^\s)]+")
_SOURCE_RE = re.compile(r"\[([A-Za-z0-9_.: -]{2,80})\]")


def list_workflows(
    root: str | None = None,
    recursive: bool = True,
    limit: int = 100,
    comfyui_user_dir: str | None = None,
) -> WorkflowListResult:
    default_root = str(_default_workflow_dir(comfyui_user_dir)) if root is None else root
    root_path = _resolve_allowed_path(default_root, comfyui_user_dir=comfyui_user_dir)
    warnings: list[WarningItem] = []
    if not root_path.exists():
        return WorkflowListResult(
            root=str(root_path),
            workflows=[],
            warnings=[WarningItem(level="error", code="ROOT_NOT_FOUND", message=f"Root does not exist: {root_path}")],
        )
    if not root_path.is_dir():
        return WorkflowListResult(
            root=str(root_path),
            workflows=[],
            warnings=[WarningItem(level="error", code="ROOT_NOT_DIRECTORY", message=f"Root is not a directory: {root_path}")],
        )

    pattern = "**/*.json" if recursive else "*.json"
    items: list[WorkflowListItem] = []
    for path in sorted(root_path.glob(pattern)):
        if len(items) >= limit:
            warnings.append(WarningItem(code="LIMIT_REACHED", message=f"Stopped after {limit} workflows."))
            break
        if not path.is_file():
            continue
        stat = path.stat()
        items.append(
            WorkflowListItem(
                path=str(path),
                name=path.name,
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                format_guess=_guess_format(path),
            )
        )
    return WorkflowListResult(root=str(root_path), workflows=items, warnings=warnings)


def summarize_workflow(
    path: str,
    profile: Profile = "safe",
    detail: str = "compact",
    comfyui_user_dir: str | None = None,
) -> SummaryResult:
    normalized = normalize_workflow(path=path, profile=profile, use_object_info="auto", comfyui_user_dir=comfyui_user_dir)
    stats = {
        "node_count": len(normalized.nodes),
        "link_count": _count_links(_load_json_path(path, comfyui_user_dir=comfyui_user_dir)[0]),
        "custom_node_count": _custom_node_count(normalized.nodes),
        "unknown_widget_nodes": sum(1 for node in normalized.nodes if node.unknown_widgets),
        "broken_link_count": sum(1 for warning in normalized.warnings if warning.code.startswith("BROKEN_")),
    }
    kinds = _infer_kind(normalized.nodes, normalized.models, normalized.generation)
    return SummaryResult(
        source=normalized.source,
        profile=profile,
        kind=kinds,
        pipeline={
            "main_chain": _main_chain(normalized.nodes),
            "terminal_nodes": _terminal_nodes(normalized.nodes),
        },
        models=normalized.models,
        generation=normalized.generation,
        prompts={name: _prompt_presence(value, profile) for name, value in normalized.prompts.items()},
        stats=stats,
        warnings=normalized.warnings,
    )


def normalize_workflow(
    path: str,
    profile: Profile = "safe",
    comfy_url: str | None = None,
    use_object_info: UseObjectInfo = "auto",
    comfyui_user_dir: str | None = None,
) -> NormalizeResult:
    data, source_path = _load_json_path(path, comfyui_user_dir=comfyui_user_dir)
    nodes_raw = _extract_nodes(data)
    node_by_id = {node.get("id"): node for node in nodes_raw}
    links = _extract_links(data)
    link_by_id = {link["id"]: link for link in links if link.get("id") is not None}
    warnings = _detect_broken_link_warnings(links, node_by_id)
    object_info = _load_object_info(use_object_info, warnings)
    if use_object_info == "require" and object_info is None:
        warnings.append(
            WarningItem(
                level="error",
                code="OBJECT_INFO_CACHE_MISSING",
                message=f"object_info cache does not exist: {object_info_cache_path()}",
            )
        )
    elif comfy_url and use_object_info == "auto" and object_info is None:
        warnings.append(
            WarningItem(
                level="info",
                code="OBJECT_INFO_CACHE_MISSING",
                message=f"Run fetch-object-info to cache {comfy_url.rstrip('/')}/object_info before normalizing custom nodes.",
            )
        )

    normalized_nodes: list[NormalizedNode] = []
    models = _empty_models()
    generation: dict[str, Any] = {}
    prompt_texts: dict[str, str] = {}

    for raw in nodes_raw:
        node_type = str(raw.get("type") or "Unknown")
        widgets = _widgets_as_dict(node_type, raw.get("widgets_values"), object_info=object_info)
        inputs = _normalize_inputs(raw, widgets, link_by_id, node_by_id, warnings)
        inputs = _profile_inputs(inputs, profile)
        _collect_models(node_type, inputs, models, profile)
        _collect_generation(node_type, inputs, generation)
        _collect_prompts(node_type, inputs, prompt_texts)

        unknown_widgets = None
        if profile == "debug" and raw.get("widgets_values") and not widgets:
            unknown_widgets = list(raw.get("widgets_values") or [])

        normalized = {
            key: value
            for key, value in raw.items()
            if key not in UI_ONLY_NODE_FIELDS
        }
        title = _node_title(raw)
        normalized_nodes.append(
            NormalizedNode(
                id=normalized.get("id", len(normalized_nodes)),
                type=node_type,
                title=title,
                inputs=inputs,
                outputs=_profile_outputs(raw.get("outputs"), profile),
                unknown_widgets=unknown_widgets,
            )
        )

    prompts = _profile_prompts(prompt_texts, profile)
    return NormalizeResult(
        source=SourceInfo(path=str(source_path), name=source_path.name),
        profile=profile,
        nodes=normalized_nodes,
        models=models,
        generation=generation,
        prompts=prompts,
        warnings=warnings,
    )


def fetch_object_info(comfy_url: str = "http://127.0.0.1:8188") -> ObjectInfoFetchResult:
    url = f"{comfy_url.rstrip('/')}/object_info"
    try:
        with urlopen(url, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ValueError(f"Failed to fetch {url}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("/object_info response must be a JSON object.")
    path = object_info_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return ObjectInfoFetchResult(
        ok=True,
        source_url=url,
        path=str(path),
        node_count=len(data),
        message=f"Cached {len(data)} node definitions.",
    )


def diagnose_load(
    path: str,
    comfyui_user_dir: str | None = None,
    error_report_text: str | None = None,
) -> RuntimeLoadDiagnosticResult:
    data, source_path = _load_json_path(path, comfyui_user_dir=comfyui_user_dir)
    warnings: list[WarningItem] = []
    normalized = normalize_workflow(path=path, profile="safe", use_object_info="auto", comfyui_user_dir=comfyui_user_dir)
    repair = repair_broken_links(path=path, dry_run=True, comfyui_user_dir=comfyui_user_dir)
    object_info = _object_info_diagnostics(data)
    log_statuses, events = _read_runtime_log_events(comfyui_user_dir=comfyui_user_dir)
    if error_report_text:
        events.extend(_parse_error_report_text(error_report_text))

    workflow_terms = _workflow_terms(normalized)
    ranked_events = _rank_events(events, workflow_terms)
    matched_errors = ranked_events[:LOG_MAX_EVENTS]
    noise_counts = Counter(event.category for event in events if event.category in {"startup_info", "deprecated_api", "manager_cache_warning"})
    frontend = _frontend_error_summary(events, bool(error_report_text))
    repair_plan = _runtime_repair_plan(source_path, repair, object_info, matched_errors)
    summary = _runtime_summary(repair_plan, matched_errors)

    if not error_report_text and not matched_errors and not repair.broken_links:
        optional_state = "helpful_if_frontend_only_error_persists"
    else:
        optional_state = "not_required"

    return RuntimeLoadDiagnosticResult(
        summary=summary,
        workflow=str(source_path),
        source=SourceInfo(path=str(source_path), name=source_path.name),
        evidence=matched_errors[:10],
        static=RuntimeStaticDiagnostics(
            normalize_ok=not any(warning.level == "error" for warning in normalized.warnings),
            broken_link_count=len(repair.broken_links),
            unknown_widget_nodes=sum(1 for node in normalized.nodes if node.unknown_widgets),
            warnings=normalized.warnings + repair.warnings,
        ),
        object_info=object_info,
        logs=RuntimeLogsDiagnostics(
            files_checked=list(LOG_FILES),
            file_status=log_statuses,
            events_scanned=len(events),
            events_returned=len(matched_errors),
            noise_counts=dict(noise_counts),
            matched_errors=matched_errors,
        ),
        optional_inputs={"error_report_text": optional_state},
        frontend_error=frontend,
        repair_plan=repair_plan,
        warnings=warnings,
    )


def _object_info_diagnostics(data: dict[str, Any]) -> RuntimeObjectInfoDiagnostics:
    path = object_info_cache_path()
    exists = path.exists()
    valid = False
    stale = True
    node_count = 0
    stale_reason = "missing" if not exists else "metadata_missing"
    object_info: dict[str, Any] | None = None
    if exists:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = None
            stale_reason = "invalid"
        if isinstance(loaded, dict):
            valid = True
            object_info = loaded
            node_count = len(loaded)
            if _looks_like_object_info_with_metadata(loaded):
                stale = False
                stale_reason = None
    node_types = sorted({str(node.get("type")) for node in _extract_nodes(data) if node.get("type")})
    missing = [node_type for node_type in node_types if object_info is not None and node_type not in object_info]
    return RuntimeObjectInfoDiagnostics(
        path=str(path),
        exists=exists,
        valid=valid,
        stale=stale,
        node_count=node_count,
        missing_node_types=missing,
        stale_reason=stale_reason,
    )


def _looks_like_object_info_with_metadata(data: dict[str, Any]) -> bool:
    metadata = data.get("_metadata") or data.get("metadata")
    return isinstance(metadata, dict) and bool(metadata.get("fetched_at"))


def _read_runtime_log_events(comfyui_user_dir: str | None = None) -> tuple[list[LogFileStatus], list[RuntimeDiagnosticEvent]]:
    user_dir = Path(_comfyui_user_dir(comfyui_user_dir)).expanduser().resolve()
    statuses: list[LogFileStatus] = []
    events: list[RuntimeDiagnosticEvent] = []
    for name in LOG_FILES:
        log_path = (user_dir / name).resolve()
        if not _is_relative_to(log_path, user_dir):
            statuses.append(LogFileStatus(file=name, exists=False, readable=False, error="outside comfyui_user_dir"))
            continue
        status, parsed = _read_one_log_file(log_path, name)
        statuses.append(status)
        events.extend(parsed)
    return statuses, _dedupe_events(events)


def _read_one_log_file(path: Path, name: str) -> tuple[LogFileStatus, list[RuntimeDiagnosticEvent]]:
    if not path.exists():
        return LogFileStatus(file=name, exists=False, readable=False), []
    try:
        stat = path.stat()
        with path.open("rb") as handle:
            if stat.st_size > LOG_TAIL_BYTES:
                handle.seek(-LOG_TAIL_BYTES, 2)
            raw = handle.read(LOG_TAIL_BYTES)
    except OSError as error:
        return LogFileStatus(file=name, exists=True, readable=False, error=str(error)), []

    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    truncated = stat.st_size > LOG_TAIL_BYTES
    if len(lines) > LOG_TAIL_LINES:
        lines = lines[-LOG_TAIL_LINES:]
        truncated = True
    status = LogFileStatus(
        file=name,
        exists=True,
        readable=True,
        size_bytes=stat.st_size,
        mtime=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        bytes_read=len(raw),
        lines_read=len(lines),
        truncated=truncated,
    )
    return status, _parse_log_lines(name, lines)


def _parse_log_lines(file_name: str, lines: list[str]) -> list[RuntimeDiagnosticEvent]:
    raw_events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for index, line in enumerate(lines, start=1):
        clean = _ANSI_RE.sub("", line)
        timestamp, message = _split_timestamp(clean)
        if timestamp:
            if current:
                raw_events.append(current)
            current = {"timestamp": timestamp, "line_start": index, "line_end": index, "message": message}
        elif current:
            current["line_end"] = index
            current["message"] = f"{current['message']}\n{clean}".strip()
        elif clean.strip():
            current = {"timestamp": None, "line_start": index, "line_end": index, "message": clean}
    if current:
        raw_events.append(current)

    return [_event_from_message(file_name, item) for item in raw_events if str(item.get("message") or "").strip()]


def _split_timestamp(line: str) -> tuple[str | None, str]:
    match = _ISO_TS_RE.match(line) or _BRACKET_TS_RE.match(line)
    if not match:
        return None, line.strip()
    timestamp = _normalize_timestamp(match.group("ts"))
    message = line[match.end() :].strip()
    message = _ISO_TS_RE.sub("", message)
    message = _BRACKET_TS_RE.sub("", message)
    return timestamp, message.strip()


def _normalize_timestamp(value: str) -> str:
    normalized = value.replace(" ", "T")
    try:
        return datetime.fromisoformat(normalized).isoformat()
    except ValueError:
        return normalized


def _event_from_message(file_name: str | None, item: dict[str, Any]) -> RuntimeDiagnosticEvent:
    message = _redact_log_message(str(item.get("message") or ""))
    severity = _severity(message)
    category = _category(message)
    source = _source(message)
    extension = _extension(message)
    exception_type = _exception_type(message)
    node_type = _node_type_hint(message)
    package = extension or source
    fingerprint = _event_fingerprint(category, source, node_type, package, exception_type, message)
    return RuntimeDiagnosticEvent(
        file=file_name,
        line_start=item.get("line_start"),
        line_end=item.get("line_end"),
        timestamp=item.get("timestamp"),
        severity=severity,
        category=category,
        source=source,
        node_type=node_type,
        package=package,
        exception_type=exception_type,
        extension=extension,
        message=_limit_message(message),
        fingerprint=fingerprint,
        confidence="low",
    )


def _parse_error_report_text(text: str) -> list[RuntimeDiagnosticEvent]:
    message_match = re.search(r"Exception Message:\*\*\s*(.+)", text)
    type_match = re.search(r"Exception Type:\*\*\s*(.+)", text)
    stack_match = re.search(r"/extensions/([^/\s]+)/([^:\s]+\.js):(\d+)", text)
    message = message_match.group(1).strip() if message_match else text.strip().splitlines()[0]
    redacted = _redact_log_message(message)
    extension = stack_match.group(1) if stack_match else None
    asset = stack_match.group(2) if stack_match else None
    exception_type = _exception_type(redacted)
    if type_match and not exception_type:
        exception_type = type_match.group(1).strip()
    event = RuntimeDiagnosticEvent(
        source="comfyui_frontend_error_report",
        severity="error",
        category=_category(redacted),
        exception_type=exception_type,
        message=_limit_message(redacted),
        extension=extension,
        package=extension,
        fingerprint=_event_fingerprint(_category(redacted), "comfyui_frontend_error_report", None, extension, exception_type, redacted),
        confidence="medium",
    )
    if asset and extension:
        event.message = f"{event.message} [extension={extension} asset={asset}]"
    return [event]


def _dedupe_events(events: list[RuntimeDiagnosticEvent]) -> list[RuntimeDiagnosticEvent]:
    by_key: dict[str, RuntimeDiagnosticEvent] = {}
    for event in events:
        existing = by_key.get(event.fingerprint)
        if existing:
            existing.count += 1
            existing.line_end = event.line_end or existing.line_end
            existing.timestamp = event.timestamp or existing.timestamp
        else:
            by_key[event.fingerprint] = event
    return list(by_key.values())


def _rank_events(events: list[RuntimeDiagnosticEvent], workflow_terms: set[str]) -> list[RuntimeDiagnosticEvent]:
    ranked = sorted(events, key=lambda event: _event_score(event, workflow_terms), reverse=True)
    for event in ranked:
        event.confidence = _event_confidence(event, workflow_terms)
    primary_categories = {
        "broken_origin_slot",
        "frontend_graph_load_error",
        "custom_node_import_error",
        "missing_custom_node",
        "missing_python_module",
        "model_resolution_warning",
    }
    return [
        event
        for event in ranked
        if event.category in primary_categories or event.confidence != "low"
    ]


def _event_score(event: RuntimeDiagnosticEvent, workflow_terms: set[str]) -> tuple[int, int, int, int, int]:
    severity_score = {"error": 4, "warning": 3, "info": 2, "debug": 1}.get(event.severity, 0)
    relevance = 1 if _event_matches_workflow(event, workflow_terms) else 0
    category_score = {
        "broken_origin_slot": 9,
        "frontend_graph_load_error": 8,
        "custom_node_import_error": 7,
        "missing_custom_node": 7,
        "missing_python_module": 6,
        "model_resolution_warning": 5,
        "missing_optional_dependency": 4,
        "deprecated_api": 2,
        "manager_cache_warning": 1,
    }.get(event.category, 0)
    recency = _timestamp_sort_value(event.timestamp)
    return (severity_score, relevance, category_score, recency, min(event.count, 10))


def _timestamp_sort_value(timestamp: str | None) -> int:
    if not timestamp:
        return 0
    try:
        return int(datetime.fromisoformat(timestamp).timestamp())
    except ValueError:
        return 0


def _event_confidence(event: RuntimeDiagnosticEvent, workflow_terms: set[str]) -> str:
    if event.category in {"broken_origin_slot", "frontend_graph_load_error"} and _event_matches_workflow(event, workflow_terms):
        return "high"
    if _event_matches_workflow(event, workflow_terms):
        return "high"
    if event.severity == "error" and event.category != "unknown":
        return "medium"
    return "low"


def _event_matches_workflow(event: RuntimeDiagnosticEvent, workflow_terms: set[str]) -> bool:
    candidates = [event.node_type, event.package, event.source, event.extension]
    return any(candidate and candidate in workflow_terms for candidate in candidates)


def _workflow_terms(normalized: NormalizeResult) -> set[str]:
    terms = {node.type for node in normalized.nodes}
    for values in normalized.models.values():
        if isinstance(values, list):
            for value in values:
                if isinstance(value, dict):
                    terms.update(str(item) for item in value.values() if item)
                elif value:
                    terms.add(str(value))
    return terms


def _frontend_error_summary(events: list[RuntimeDiagnosticEvent], provided: bool) -> dict[str, Any]:
    frontend = [event for event in events if event.category in {"frontend_graph_load_error", "broken_origin_slot"}]
    if not frontend:
        return {"present": False, "input_provided": provided}
    event = frontend[0]
    return {
        "present": True,
        "input_provided": provided,
        "category": event.category,
        "exception_type": event.exception_type,
        "extension": event.extension,
        "message": event.message,
    }


def _runtime_summary(
    repair_plan: list[RuntimeRepairPlanItem],
    events: list[RuntimeDiagnosticEvent],
) -> RuntimeSummary:
    if repair_plan:
        first = repair_plan[0]
        status = "needs_repair" if first.kind == "broken_origin_slot" else "needs_setup"
        return RuntimeSummary(
            status=status,
            primary_issue=first.kind,
            confidence=first.confidence,
            next_action=first.next_action,
        )
    if events:
        event = events[0]
        return RuntimeSummary(
            status="inconclusive",
            primary_issue=event.category,
            confidence=event.confidence,
            next_action=None,
        )
    return RuntimeSummary(status="ok", primary_issue=None, confidence="high", next_action=None)


def _runtime_repair_plan(
    source_path: Path,
    repair: RepairResult,
    object_info: RuntimeObjectInfoDiagnostics,
    events: list[RuntimeDiagnosticEvent],
) -> list[RuntimeRepairPlanItem]:
    plan: list[RuntimeRepairPlanItem] = []
    if repair.broken_links or any(event.category in {"broken_origin_slot", "frontend_graph_load_error"} for event in events):
        plan.append(
            RuntimeRepairPlanItem(
                kind="broken_origin_slot",
                action="repair-links",
                confidence="high" if repair.broken_links else "medium",
                next_action=RuntimeAction(
                    tool="repair-links",
                    args={"path": str(source_path), "dry_run": True},
                    command=["comfy-agent-view", "repair-links", str(source_path), "--dry-run"],
                    safe_to_run=True,
                    writes_files=False,
                    requires_user_approval=False,
                ),
            )
        )
    should_refresh_object_info = object_info.stale and not (
        object_info.exists and object_info.valid and object_info.stale_reason == "metadata_missing"
    )
    if should_refresh_object_info:
        plan.append(
            RuntimeRepairPlanItem(
                kind="object_info_stale",
                action="fetch-object-info",
                confidence="medium",
                next_action=RuntimeAction(
                    tool="fetch-object-info",
                    args={},
                    command=["comfy-agent-view", "fetch-object-info"],
                    safe_to_run=True,
                    writes_files=True,
                    requires_user_approval=False,
                ),
            )
        )
    if object_info.missing_node_types:
        plan.append(
            RuntimeRepairPlanItem(
                kind="missing_custom_node",
                action="inspect-custom-node-install",
                confidence="medium",
                next_action=RuntimeAction(
                    tool="inspect-custom-node-install",
                    args={"node_types": object_info.missing_node_types[:10]},
                    command=[],
                    safe_to_run=False,
                    writes_files=False,
                    requires_user_approval=True,
                ),
            )
        )
    return plan


def _severity(message: str) -> str:
    lower = message.lower()
    if lower.startswith("found comfy_kitchen backend"):
        return "info"
    if any(token in message for token in ("ERROR", "Exception", "Traceback", "Cannot import", "ModuleNotFoundError", "ImportError")):
        return "error"
    if "warning" in lower or "not installed" in lower or "outdated cache" in lower:
        return "warning"
    if "debug" in lower:
        return "debug"
    return "info"


def _category(message: str) -> str:
    lower = message.lower()
    if lower.startswith("found comfy_kitchen backend"):
        return "startup_info"
    if "node.outputs" in lower and "origin_slot" in lower and "undefined" in lower:
        return "broken_origin_slot"
    if "modulenotfounderror" in lower:
        return "missing_python_module"
    if any(token in lower for token in ("importerror", "cannot import", "failed to import custom node")):
        return "custom_node_import_error"
    if "not installed" in lower:
        return "missing_optional_dependency"
    if "deprecation warning" in lower or "deprecated legacy api" in lower:
        return "deprecated_api"
    if any(token in lower for token in ("model not found", "checkpoint not found", "lora not found", "vae not found")):
        return "model_resolution_warning"
    if "comfyui-manager" in lower and any(token in lower for token in ("cache", "registry")):
        return "manager_cache_warning"
    if any(token in lower for token in ("startup time", "python version", "comfyui version", "starting server", "device:")):
        return "startup_info"
    if "error" in lower or "exception" in lower:
        return "unknown"
    return "startup_info"


def _source(message: str) -> str | None:
    match = _SOURCE_RE.search(message)
    if match:
        return match.group(1).strip()
    for name in ("ComfyUI-Manager", "LoRA-Manager", "Impact Pack", "Inspire Pack", "rgthree-comfy", "WAS Node Suite"):
        if name.lower() in message.lower():
            return name
    return None


def _extension(message: str) -> str | None:
    match = re.search(r"extensions/([^/\s]+)/", message)
    if match:
        return match.group(1)
    if "ComfyUI-Impact-Pack" in message:
        return "ComfyUI-Impact-Pack"
    return None


def _exception_type(message: str) -> str | None:
    match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\b", message)
    return match.group(1) if match else None


def _node_type_hint(message: str) -> str | None:
    match = re.search(r"node type ['\"]?([A-Za-z0-9_ -]+)['\"]?", message, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _redact_log_message(message: str) -> str:
    redacted = _URL_RE.sub(_redact_url, message)
    redacted = _WINDOWS_PATH_RE.sub(lambda match: f"[PATH:{match.group(1)}]", redacted)
    redacted = _POSIX_PATH_RE.sub(lambda match: f"[PATH:{match.group(1)}]", redacted)
    redacted = re.sub(r"(?i)(api[_-]?key|token|password|secret)=\S+", r"\1=[REDACTED]", redacted)
    redacted = re.sub(r'"([^"]{121,})"', lambda match: _redact_long_string(match.group(1)), redacted)
    return redacted.strip()


def _redact_url(match: re.Match[str]) -> str:
    value = match.group(0)
    asset = re.search(r"/extensions/([^/\s]+)/([^:\s]+\.js)(?::(\d+))?", value)
    if asset:
        line = f":{asset.group(3)}" if asset.group(3) else ""
        return f"extension={asset.group(1)} asset={asset.group(2)}{line}"
    return "[URL]"


def _redact_long_string(value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()[:12]
    return f'"[REDACTED:length={len(value)} sha256={digest}]"'


def _limit_message(message: str) -> str:
    if len(message) <= LOG_MAX_MESSAGE:
        return message
    digest = sha256(message.encode("utf-8")).hexdigest()[:12]
    return f"{message[:LOG_MAX_MESSAGE]}... [TRUNCATED sha256={digest}]"


def _event_fingerprint(
    category: str,
    source: str | None,
    node_type: str | None,
    package: str | None,
    exception_type: str | None,
    message: str,
) -> str:
    payload = "|".join(str(item or "") for item in (category, source, node_type, package, exception_type, message))
    return f"sha256:{sha256(payload.encode('utf-8')).hexdigest()}"


def repair_broken_links(
    path: str,
    dry_run: bool = True,
    output_path: str | None = None,
    comfyui_user_dir: str | None = None,
) -> RepairResult:
    data, source_path = _load_json_path(path, comfyui_user_dir=comfyui_user_dir)
    nodes = _extract_nodes(data)
    node_by_id = {node.get("id"): node for node in nodes}
    links = _extract_links(data)
    broken = _detect_broken_links(links, node_by_id)
    remove_ids = [item.link_id for item in broken]
    written_path = None
    warnings: list[WarningItem] = []

    if broken and not dry_run:
        if not output_path:
            warnings.append(
                WarningItem(level="error", code="OUTPUT_PATH_REQUIRED", message="output_path is required when dry_run is false.")
            )
        else:
            out = _resolve_allowed_path(output_path, for_write=True, comfyui_user_dir=comfyui_user_dir)
            repaired = _remove_links(data, set(remove_ids))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(repaired, ensure_ascii=False, indent=2), encoding="utf-8")
            written_path = str(out)

    ok = not broken
    if broken:
        message = f"{len(broken)} broken link(s) detected."
        if dry_run:
            message += " Run with dry_run=false and output_path to write a repaired copy."
    else:
        message = "No broken links detected."

    return RepairResult(
        ok=ok,
        source=SourceInfo(path=str(source_path), name=source_path.name),
        broken_links=broken,
        would_remove_links=remove_ids,
        written_path=written_path,
        message=message,
        warnings=warnings,
    )


def apply_workflow_patch(
    patch_path: str,
    dry_run: bool = True,
    overwrite: bool = False,
    comfyui_user_dir: str | None = None,
) -> WorkflowPatchResult:
    patch_data, patch_source_path = _load_json_path(patch_path, comfyui_user_dir=comfyui_user_dir)
    try:
        spec = WorkflowPatchSpec.model_validate(patch_data)
    except ValidationError as error:
        raise ValueError(f"Invalid workflow patch spec: {error}") from error
    data, source_path = _load_json_path(spec.source, comfyui_user_dir=comfyui_user_dir)
    output_path = _resolve_allowed_path(spec.output, for_write=True, comfyui_user_dir=comfyui_user_dir)
    if output_path.exists() and not overwrite and not dry_run:
        raise ValueError(f"Output path already exists: {output_path}")
    if source_path == output_path:
        raise ValueError("Patch output must be different from source.")

    patched = json.loads(json.dumps(data))
    applied: list[AppliedWorkflowPatchOperation] = []
    warnings: list[WarningItem] = []
    for operation in spec.operations:
        applied.append(_apply_patch_operation(patched, operation))

    validation = _validate_workflow_data(patched, output_path)
    written_path = None
    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(patched, ensure_ascii=False, indent=2), encoding="utf-8")
        written_path = str(output_path)

    if not spec.operations:
        warnings.append(WarningItem(code="NO_OPERATIONS", message=f"No operations in patch: {patch_source_path}"))

    return WorkflowPatchResult(
        ok=validation.ok,
        dry_run=dry_run,
        source=SourceInfo(path=str(source_path), name=source_path.name),
        output_path=str(output_path),
        applied=applied,
        written_path=written_path,
        validation=validation,
        warnings=warnings + validation.warnings,
    )


def _apply_patch_operation(data: dict[str, Any], operation: WorkflowPatchOperation) -> AppliedWorkflowPatchOperation:
    if operation.op == "set":
        if operation.node_id is None or not operation.path:
            raise ValueError("set operation requires node_id and path.")
        node = _node_by_id(data, operation.node_id)
        _set_path(node, operation.path, operation.value)
        return AppliedWorkflowPatchOperation(
            op=operation.op,
            node_id=operation.node_id,
            path=operation.path,
            message=f"Set node {operation.node_id} path {operation.path}.",
        )
    if operation.op == "delete_link":
        if operation.link_id is None:
            raise ValueError("delete_link operation requires link_id.")
        before = len(_extract_links(data))
        updated = _remove_links(data, {operation.link_id})
        data.clear()
        data.update(updated)
        after = len(_extract_links(data))
        if before == after:
            raise ValueError(f"Link does not exist: {operation.link_id}")
        return AppliedWorkflowPatchOperation(
            op=operation.op,
            link_id=operation.link_id,
            message=f"Deleted link {operation.link_id}.",
        )
    if operation.op == "delete_node":
        if operation.node_id is None:
            raise ValueError("delete_node operation requires node_id.")
        _node_by_id(data, operation.node_id)
        link_refs = [
            link.get("id")
            for link in _extract_links(data)
            if _id_equal(link.get("origin_id"), operation.node_id)
            or _id_equal(link.get("target_id"), operation.node_id)
        ]
        linked_ids = {link_id for link_id in link_refs if link_id is not None}
        if linked_ids:
            updated = _remove_links(data, linked_ids)
            data.clear()
            data.update(updated)
        before = len(_extract_nodes(data))
        data["nodes"] = [item for item in data.get("nodes", []) if not (isinstance(item, dict) and _id_equal(item.get("id"), operation.node_id))]
        after = len(_extract_nodes(data))
        if before == after:
            raise ValueError(f"Node does not exist: {operation.node_id}")
        return AppliedWorkflowPatchOperation(
            op=operation.op,
            node_id=operation.node_id,
            message=f"Deleted node {operation.node_id} and {len(linked_ids)} attached link(s).",
        )
    if operation.op == "set_input_link":
        if operation.node_id is None or operation.input is None or operation.link_id is None:
            raise ValueError("set_input_link operation requires node_id, input, and link_id.")
        node = _node_by_id(data, operation.node_id)
        target_slot, previous_link = _set_node_input_link(node, operation.input, operation.link_id)
        _retarget_link(data, operation.link_id, operation.node_id, target_slot)
        _ensure_origin_output_link(data, operation.link_id)
        return AppliedWorkflowPatchOperation(
            op=operation.op,
            node_id=operation.node_id,
            link_id=operation.link_id,
            input=operation.input,
            message=f"Set node {operation.node_id} input {operation.input} to link {operation.link_id}; previous link was {previous_link}.",
        )
    raise ValueError(f"Unsupported operation: {operation.op}")


def _node_by_id(data: dict[str, Any], node_id: int | str) -> dict[str, Any]:
    for node in _extract_nodes(data):
        if str(node.get("id")) == str(node_id):
            return node
    raise ValueError(f"Node does not exist: {node_id}")


def _set_path(target: dict[str, Any], path: list[int | str], value: Any) -> None:
    current: Any = target
    for key in path[:-1]:
        if isinstance(current, list) and isinstance(key, int):
            if key < 0 or key >= len(current):
                raise ValueError(f"Path index out of range: {path}")
            current = current[key]
        elif isinstance(current, dict) and isinstance(key, str):
            if key not in current:
                raise ValueError(f"Path key does not exist: {path}")
            current = current[key]
        else:
            raise ValueError(f"Path cannot be traversed: {path}")
    last = path[-1]
    if isinstance(current, list) and isinstance(last, int):
        if last < 0 or last >= len(current):
            raise ValueError(f"Path index out of range: {path}")
        current[last] = value
    elif isinstance(current, dict) and isinstance(last, str):
        if last not in current:
            raise ValueError(f"Path key does not exist: {path}")
        current[last] = value
    else:
        raise ValueError(f"Path cannot be assigned: {path}")


def _set_node_input_link(node: dict[str, Any], input_name: str, link_id: int | str) -> tuple[int, Any]:
    raw_inputs = node.get("inputs")
    if isinstance(raw_inputs, list):
        for index, item in enumerate(raw_inputs):
            if isinstance(item, dict) and _input_name(item, index) == input_name:
                previous = item.get("link")
                item["link"] = link_id
                return index, previous
    elif isinstance(raw_inputs, dict):
        if input_name not in raw_inputs:
            raise ValueError(f"Input does not exist on node {node.get('id')}: {input_name}")
        item = raw_inputs[input_name]
        if isinstance(item, dict):
            previous = item.get("link")
            item["link"] = link_id
        else:
            previous = item
            raw_inputs[input_name] = {"link": link_id}
        return list(raw_inputs).index(input_name), previous
    raise ValueError(f"Node has no editable inputs: {node.get('id')}")


def _retarget_link(data: dict[str, Any], link_id: int | str, target_id: int | str, target_slot: int) -> None:
    for item in data.get("links", []) or []:
        if not _id_equal(_raw_link_id(item), link_id):
            continue
        if isinstance(item, list):
            if len(item) < 5:
                raise ValueError(f"Link is malformed: {link_id}")
            item[3] = target_id
            item[4] = target_slot
            return
        if isinstance(item, dict):
            item["target_id"] = target_id
            item["target_slot"] = target_slot
            return
    raise ValueError(f"Link does not exist: {link_id}")


def _ensure_origin_output_link(data: dict[str, Any], link_id: int | str) -> None:
    link = next((item for item in _extract_links(data) if _id_equal(item.get("id"), link_id)), None)
    if not link:
        return
    origin = None
    for node in _extract_nodes(data):
        if _id_equal(node.get("id"), link.get("origin_id")):
            origin = node
            break
    outputs = origin.get("outputs") if origin else None
    origin_slot = link.get("origin_slot")
    if not isinstance(outputs, list) or not isinstance(origin_slot, int) or origin_slot < 0 or origin_slot >= len(outputs):
        return
    output = outputs[origin_slot]
    if isinstance(output, dict):
        links = output.setdefault("links", [])
        if isinstance(links, list) and not any(_id_equal(link_id, existing) for existing in links):
            links.append(link_id)


def _validate_workflow_data(data: dict[str, Any], source_path: Path) -> RepairResult:
    nodes = _extract_nodes(data)
    node_by_id = {node.get("id"): node for node in nodes}
    broken = _detect_broken_links(_extract_links(data), node_by_id)
    remove_ids = [item.link_id for item in broken]
    message = "No broken links detected." if not broken else f"{len(broken)} broken link(s) detected."
    return RepairResult(
        ok=not broken,
        source=SourceInfo(path=str(source_path), name=source_path.name),
        broken_links=broken,
        would_remove_links=remove_ids,
        written_path=None,
        message=message,
    )


def _load_json_path(path: str, comfyui_user_dir: str | None = None) -> tuple[dict[str, Any], Path]:
    source_path = _resolve_allowed_path(path, comfyui_user_dir=comfyui_user_dir)
    with source_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Workflow JSON root must be an object.")
    return data, source_path


def _resolve_allowed_path(path: str, for_write: bool = False, comfyui_user_dir: str | None = None) -> Path:
    resolved = Path(path).expanduser().resolve()
    roots = _allowed_roots(comfyui_user_dir)
    if not any(_is_relative_to(resolved, root) for root in roots):
        raise PermissionError(f"Path is outside comfyui_user_dir: {resolved}")
    if for_write and not any(_is_relative_to(resolved.parent, root) for root in roots):
        raise PermissionError(f"Output path is outside comfyui_user_dir: {resolved}")
    return resolved


def _comfyui_user_dir(override: str | None = None) -> str:
    if override and override.strip():
        return override
    configured = load_config().comfyui_user_dir
    if not configured:
        raise PermissionError("comfyui_user_dir is not configured.")
    return configured


def _default_workflow_dir(comfyui_user_dir: str | None = None) -> Path:
    return Path(_comfyui_user_dir(comfyui_user_dir)).expanduser().resolve() / "default" / "workflows"


def _allowed_roots(comfyui_user_dir: str | None = None) -> list[Path]:
    user_dir = Path(_comfyui_user_dir(comfyui_user_dir)).expanduser().resolve()
    workflow_dir = (user_dir / "default" / "workflows").resolve()
    roots = [user_dir]
    if workflow_dir != user_dir and not _is_relative_to(workflow_dir, user_dir):
        roots.append(workflow_dir)
    return roots


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _extract_nodes(data: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = data.get("nodes", [])
    if isinstance(nodes, dict):
        nodes = list(nodes.values())
    return [node for node in nodes if isinstance(node, dict)]


def _extract_links(data: dict[str, Any]) -> list[dict[str, Any]]:
    parsed = []
    for item in data.get("links", []) or []:
        if isinstance(item, list) and len(item) >= 6:
            parsed.append(
                {
                    "id": item[0],
                    "origin_id": item[1],
                    "origin_slot": item[2],
                    "target_id": item[3],
                    "target_slot": item[4],
                    "type": item[5],
                }
            )
        elif isinstance(item, dict):
            parsed.append(item)
    return parsed


def _load_object_info(use_object_info: UseObjectInfo, warnings: list[WarningItem]) -> dict[str, Any] | None:
    if use_object_info == "never":
        return None
    path = object_info_cache_path()
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        warnings.append(
            WarningItem(level="error", code="OBJECT_INFO_CACHE_INVALID", message=f"Failed to read object_info cache: {error}")
        )
        return None
    if not isinstance(data, dict):
        warnings.append(WarningItem(level="error", code="OBJECT_INFO_CACHE_INVALID", message="object_info cache root must be an object."))
        return None
    return data


def _widgets_as_dict(node_type: str, values: Any, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(values, dict):
        return values
    if not isinstance(values, list):
        return {}
    names = WIDGET_MAPS.get(node_type) or _object_info_widget_names(node_type, object_info)
    return {name: values[index] for index, name in enumerate(names) if index < len(values)}


def _object_info_widget_names(node_type: str, object_info: dict[str, Any] | None) -> list[str]:
    if not object_info:
        return []
    node_info = object_info.get(node_type)
    if not isinstance(node_info, dict):
        return []
    inputs = node_info.get("input")
    if not isinstance(inputs, dict):
        return []
    names: list[str] = []
    for section_name in ("required", "optional"):
        section = inputs.get(section_name)
        if not isinstance(section, dict):
            continue
        for name, spec in section.items():
            if _object_info_force_input(spec):
                continue
            names.append(str(name))
    return names


def _object_info_force_input(spec: Any) -> bool:
    if not isinstance(spec, (list, tuple)) or len(spec) < 2:
        return False
    options = spec[1]
    return isinstance(options, dict) and bool(options.get("forceInput"))


def _normalize_inputs(
    raw: dict[str, Any],
    widgets: dict[str, Any],
    link_by_id: dict[Any, dict[str, Any]],
    node_by_id: dict[Any, dict[str, Any]],
    warnings: list[WarningItem],
) -> dict[str, Any]:
    inputs = dict(widgets)
    raw_inputs = raw.get("inputs")
    if isinstance(raw_inputs, dict):
        iterable = raw_inputs.items()
    elif isinstance(raw_inputs, list):
        iterable = [(_input_name(item, index), item) for index, item in enumerate(raw_inputs)]
    else:
        iterable = []

    for name, item in iterable:
        if isinstance(item, dict) and "link" in item and item.get("link") is not None:
            link_id = item.get("link")
            link = link_by_id.get(link_id)
            if link:
                inputs[name] = _link_ref(link, node_by_id, warnings, raw.get("id"), name)
            else:
                warnings.append(
                    WarningItem(
                        level="error",
                        code="MISSING_LINK",
                        message=f"Input references missing link {link_id}.",
                        node_id=_safe_int(raw.get("id")),
                        input=str(name),
                        link_id=_safe_int(link_id),
                    )
                )
                inputs[name] = {"missing_link": link_id}
        elif isinstance(item, dict) and "value" in item:
            inputs[name] = item.get("value")
        elif name not in inputs:
            inputs[name] = item
    return inputs


def _input_name(item: Any, index: int) -> str:
    if isinstance(item, dict):
        return str(item.get("name") or item.get("label") or f"input_{index}")
    return f"input_{index}"


def _link_ref(
    link: dict[str, Any],
    node_by_id: dict[Any, dict[str, Any]],
    warnings: list[WarningItem],
    target_node_id: Any,
    input_name: str,
) -> dict[str, Any]:
    origin_id = link.get("origin_id")
    origin_slot = link.get("origin_slot")
    origin = node_by_id.get(origin_id)
    output = None
    if origin and isinstance(origin.get("outputs"), list) and isinstance(origin_slot, int) and 0 <= origin_slot < len(origin["outputs"]):
        output = origin["outputs"][origin_slot]
    elif origin:
        warnings.append(
            WarningItem(
                level="error",
                code="BROKEN_ORIGIN_SLOT",
                message=f"origin_slot {origin_slot} is out of range for node {origin_id}.",
                node_id=_safe_int(target_node_id),
                input=input_name,
                link_id=_safe_int(link.get("id")),
            )
        )
    return {
        "from_node": origin_id,
        "from_output": _output_type(output, link),
        "from_output_name": _output_name(output, link),
    }


def _output_type(output: Any, link: dict[str, Any]) -> Any:
    if isinstance(output, dict):
        return output.get("type") or link.get("type")
    return link.get("type")


def _output_name(output: Any, link: dict[str, Any]) -> Any:
    if isinstance(output, dict):
        return output.get("name") or output.get("label") or output.get("type") or link.get("type")
    return link.get("type")


def _detect_broken_link_warnings(links: list[dict[str, Any]], node_by_id: dict[Any, dict[str, Any]]) -> list[WarningItem]:
    return [
        WarningItem(level="error", code="BROKEN_ORIGIN_SLOT", message=item.reason, link_id=_safe_int(item.link_id))
        for item in _detect_broken_links(links, node_by_id)
    ]


def _detect_broken_links(links: list[dict[str, Any]], node_by_id: dict[Any, dict[str, Any]]) -> list[BrokenLink]:
    broken = []
    for link in links:
        if node_by_id.get(link.get("target_id")) is None:
            continue
        origin = node_by_id.get(link.get("origin_id"))
        origin_slot = link.get("origin_slot")
        if origin is None:
            continue
        outputs = origin.get("outputs")
        if not isinstance(outputs, list):
            broken.append(_broken(link, "origin node has no outputs array"))
            continue
        if not isinstance(origin_slot, int) or origin_slot < 0 or origin_slot >= len(outputs):
            broken.append(_broken(link, f"origin_slot out of range; node has {len(outputs)} outputs"))
    return broken


def _broken(link: dict[str, Any], reason: str) -> BrokenLink:
    return BrokenLink(
        link_id=link.get("id"),
        origin_id=link.get("origin_id"),
        origin_slot=link.get("origin_slot"),
        target_id=link.get("target_id"),
        target_slot=link.get("target_slot"),
        reason=reason,
    )


def _remove_links(data: dict[str, Any], remove_ids: set[Any]) -> dict[str, Any]:
    clone = json.loads(json.dumps(data))
    clone["links"] = [item for item in clone.get("links", []) if not _id_in(_raw_link_id(item), remove_ids)]
    for node in _extract_nodes(clone):
        for input_item in node.get("inputs", []) or []:
            if isinstance(input_item, dict) and _id_in(input_item.get("link"), remove_ids):
                input_item["link"] = None
        for output_item in node.get("outputs", []) or []:
            if isinstance(output_item, dict) and isinstance(output_item.get("links"), list):
                output_item["links"] = [link for link in output_item["links"] if not _id_in(link, remove_ids)]
    return clone


def _id_equal(left: Any, right: Any) -> bool:
    return str(left) == str(right)


def _id_in(value: Any, candidates: set[Any]) -> bool:
    return any(_id_equal(value, candidate) for candidate in candidates)


def _raw_link_id(item: Any) -> Any:
    if isinstance(item, list) and item:
        return item[0]
    if isinstance(item, dict):
        return item.get("id")
    return None


def _profile_inputs(inputs: dict[str, Any], profile: Profile) -> dict[str, Any]:
    if profile == "full" or profile == "debug":
        return inputs
    profiled = {}
    for key, value in inputs.items():
        if key == "text" and isinstance(value, str):
            profiled[key] = _redacted_prompt(value)
        elif profile == "private" and key in PRIVATE_INPUT_KEYS and value:
            profiled[key] = "[REDACTED]"
        else:
            profiled[key] = value
    return profiled


def _profile_outputs(outputs: Any, profile: Profile) -> list[dict[str, Any]] | None:
    if profile != "debug" or not isinstance(outputs, list):
        return None
    return [output for output in outputs if isinstance(output, dict)]


def _collect_models(node_type: str, inputs: dict[str, Any], models: dict[str, Any], profile: Profile) -> None:
    if node_type == "CheckpointLoaderSimple" and inputs.get("ckpt_name"):
        models["checkpoints"].append(_maybe_private(inputs["ckpt_name"], profile))
    elif node_type == "VAELoader" and inputs.get("vae_name"):
        models["vae"].append(_maybe_private(inputs["vae_name"], profile))
    elif node_type == "UNETLoader" and inputs.get("unet_name"):
        item = {
            "name": _maybe_private(inputs.get("unet_name"), profile),
            "weight_dtype": inputs.get("weight_dtype"),
        }
        models["unet"].append(item)
    elif node_type == "CLIPLoader" and inputs.get("clip_name"):
        item = {
            "name": _maybe_private(inputs.get("clip_name"), profile),
            "type": inputs.get("type"),
            "device": inputs.get("device"),
        }
        models["clip"].append(item)
    elif node_type == "LoraLoader" and inputs.get("lora_name"):
        item = {
            "name": _maybe_private(inputs.get("lora_name"), profile),
            "strength_model": inputs.get("strength_model"),
            "strength_clip": inputs.get("strength_clip"),
        }
        models["loras"].append(item)
    elif node_type == "ControlNetLoader" and inputs.get("control_net_name"):
        models["controlnet"].append(_maybe_private(inputs["control_net_name"], profile))


def _collect_generation(node_type: str, inputs: dict[str, Any], generation: dict[str, Any]) -> None:
    if node_type == "EmptyLatentImage":
        for key in ("width", "height"):
            if key in inputs:
                generation[key] = inputs[key]
    elif node_type == "KSampler":
        mapping = {"sampler_name": "sampler"}
        for key in ("seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"):
            if key in inputs:
                generation[mapping.get(key, key)] = inputs[key]
    elif node_type == "KSamplerAdvanced":
        if "noise_seed" in inputs:
            generation["seed"] = inputs["noise_seed"]
        for key in ("steps", "cfg", "sampler_name", "scheduler"):
            if key in inputs:
                generation["sampler" if key == "sampler_name" else key] = inputs[key]


def _collect_prompts(node_type: str, inputs: dict[str, Any], prompt_texts: dict[str, str]) -> None:
    if node_type != "CLIPTextEncode" or "text" not in inputs:
        return
    key = "negative" if "negative" not in prompt_texts and "positive" in prompt_texts else "positive"
    value = inputs["text"]
    if isinstance(value, str) and value.startswith("[REDACTED:"):
        prompt_texts[key] = value
    elif isinstance(value, str):
        prompt_texts[key] = value


def _profile_prompts(prompt_texts: dict[str, str], profile: Profile) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("positive", "negative"):
        text = prompt_texts.get(key)
        if text is None:
            result[key] = None
        elif profile in {"full", "debug"}:
            result[key] = text
        else:
            result[key] = _redacted_prompt(text)
    return result


def _prompt_presence(value: Any, profile: Profile) -> PromptPresence:
    if value is None:
        return PromptPresence(present=False, redacted=False)
    text = str(value)
    return PromptPresence(
        present=True,
        redacted=profile not in {"full", "debug"} or text.startswith("[REDACTED:"),
        token_count_estimate=_token_estimate(text),
    )


def _redacted_prompt(text: str) -> str:
    return f"[REDACTED: {_token_estimate(text)} tokens]"


def _token_estimate(text: str) -> int:
    if text.startswith("[REDACTED:"):
        digits = "".join(ch for ch in text if ch.isdigit())
        return int(digits) if digits else 0
    return max(1, round(len(text) / 4))


def _empty_models() -> dict[str, Any]:
    return {"checkpoints": [], "loras": [], "vae": [], "controlnet": [], "unet": [], "clip": []}


def _maybe_private(value: Any, profile: Profile) -> Any:
    if profile == "private" and value:
        return "[REDACTED]"
    return value


def _infer_kind(nodes: list[NormalizedNode], models: dict[str, Any], generation: dict[str, Any]) -> dict[str, Any]:
    types = {node.type for node in nodes}
    return {
        "workflow_type": "img2img" if "LoadImage" in types else "txt2img",
        "family_guess": _family_guess(models),
        "has_img2img": "LoadImage" in types,
        "has_controlnet": bool(models.get("controlnet")) or any("ControlNet" in item for item in types),
        "has_ipadapter": any("IPAdapter" in item for item in types),
        "has_lora": bool(models.get("loras")),
        "has_upscale": any("Upscale" in item or "upscale" in item.lower() for item in types),
    }


def _family_guess(models: dict[str, Any]) -> str | None:
    names = " ".join(str(item) for values in models.values() for item in (values if isinstance(values, list) else []))
    lower = names.lower()
    if "sdxl" in lower or "xl" in lower:
        return "sdxl"
    if "flux" in lower:
        return "flux"
    if "pony" in lower:
        return "pony"
    if "qwen" in lower:
        return "qwen"
    if "sd15" in lower or "1.5" in lower:
        return "sd15"
    return None


def _main_chain(nodes: list[NormalizedNode]) -> list[str]:
    seen = []
    for node in nodes:
        if node.type not in seen:
            seen.append(node.type)
    return seen


def _terminal_nodes(nodes: list[NormalizedNode]) -> list[str]:
    terminals = [node.type for node in nodes if node.type in {"SaveImage", "PreviewImage"}]
    return terminals or ([nodes[-1].type] if nodes else [])


def _custom_node_count(nodes: list[NormalizedNode]) -> int:
    known = set(WIDGET_MAPS) | {"VAEDecode", "PreviewImage"}
    return sum(1 for node in nodes if node.type not in known)


def _count_links(data: dict[str, Any]) -> int:
    links = data.get("links", [])
    return len(links) if isinstance(links, list) else 0


def _node_title(raw: dict[str, Any]) -> str | None:
    title = raw.get("title")
    if title:
        return str(title)
    properties = raw.get("properties")
    if isinstance(properties, dict) and properties.get("Node name for S&R"):
        return str(properties["Node name for S&R"])
    return None


def _guess_format(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "unknown_json"
    if isinstance(data, dict) and ("nodes" in data or "links" in data):
        return "comfy_ui_workflow"
    return "json"


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
