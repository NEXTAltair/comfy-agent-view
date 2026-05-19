from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from .config import load_config, object_info_cache_path
from .models import (
    BrokenLink,
    NormalizeResult,
    NormalizedNode,
    ObjectInfoFetchResult,
    Profile,
    PromptPresence,
    RepairResult,
    SourceInfo,
    SummaryResult,
    UseObjectInfo,
    WarningItem,
    WorkflowListItem,
    WorkflowListResult,
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
        origin = node_by_id.get(link.get("origin_id"))
        origin_slot = link.get("origin_slot")
        if origin is None:
            broken.append(_broken(link, "origin node is missing"))
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
    clone["links"] = [item for item in clone.get("links", []) if _raw_link_id(item) not in remove_ids]
    for node in _extract_nodes(clone):
        for input_item in node.get("inputs", []) or []:
            if isinstance(input_item, dict) and input_item.get("link") in remove_ids:
                input_item["link"] = None
        for output_item in node.get("outputs", []) or []:
            if isinstance(output_item, dict) and isinstance(output_item.get("links"), list):
                output_item["links"] = [link for link in output_item["links"] if link not in remove_ids]
    return clone


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
