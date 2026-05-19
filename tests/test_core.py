from __future__ import annotations

import json

import pytest

from comfy_agent_view.config import config_path
from comfy_agent_view.core import normalize_workflow, repair_broken_links, summarize_workflow


def _workflow(path):
    data = {
        "nodes": [
            {
                "id": 1,
                "type": "CheckpointLoaderSimple",
                "widgets_values": ["sdxl.safetensors"],
                "outputs": [{"name": "MODEL", "type": "MODEL"}, {"name": "CLIP", "type": "CLIP"}, {"name": "VAE", "type": "VAE"}],
                "pos": [0, 0],
            },
            {
                "id": 2,
                "type": "CLIPTextEncode",
                "widgets_values": ["a detailed prompt"],
                "inputs": [{"name": "clip", "link": 10}],
                "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING"}],
            },
            {
                "id": 3,
                "type": "EmptyLatentImage",
                "widgets_values": [1024, 1536, 1],
                "outputs": [{"name": "LATENT", "type": "LATENT"}],
            },
            {
                "id": 4,
                "type": "KSampler",
                "widgets_values": [123, "fixed", 28, 7.0, "dpmpp_2m", "karras", 1.0],
                "inputs": [{"name": "positive", "link": 11}],
                "outputs": [{"name": "LATENT", "type": "LATENT"}],
            },
            {"id": 5, "type": "SaveImage", "widgets_values": ["ComfyUI"], "inputs": [{"name": "images", "link": 12}]},
        ],
        "links": [
            [10, 1, 1, 2, 0, "CLIP"],
            [11, 2, 0, 4, 1, "CONDITIONING"],
            [12, 4, 0, 5, 0, "LATENT"],
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_normalize_redacts_prompt(tmp_path):
    path = _workflow(tmp_path / "wf.json")
    result = normalize_workflow(str(path), profile="safe", comfyui_user_dir=str(tmp_path))
    assert result.models["checkpoints"] == ["sdxl.safetensors"]
    assert result.generation["width"] == 1024
    assert result.generation["sampler"] == "dpmpp_2m"
    assert result.prompts["positive"].startswith("[REDACTED:")
    assert "pos" not in result.nodes[0].model_dump()


def test_summarize_returns_structured_counts(tmp_path):
    path = _workflow(tmp_path / "wf.json")
    result = summarize_workflow(str(path), comfyui_user_dir=str(tmp_path))
    assert result.stats["node_count"] == 5
    assert result.stats["link_count"] == 3
    assert result.kind["has_lora"] is False


def test_repair_detects_bad_origin_slot(tmp_path):
    path = _workflow(tmp_path / "wf.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["links"].append([99, 1, 9, 5, 0, "MODEL"])
    path.write_text(json.dumps(data), encoding="utf-8")
    result = repair_broken_links(str(path), comfyui_user_dir=str(tmp_path))
    assert result.ok is False
    assert result.would_remove_links == [99]


def test_repair_write_requires_output_inside_comfyui_user_dir(tmp_path):
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    path = _workflow(workflow_dir / "wf.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["links"].append([99, 1, 9, 5, 0, "MODEL"])
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PermissionError, match="outside comfyui_user_dir"):
        repair_broken_links(
            str(path),
            dry_run=False,
            output_path=str(tmp_path / "outside.json"),
            comfyui_user_dir=str(workflow_dir),
        )


def test_comfyui_user_dir_can_come_from_user_config(tmp_path, monkeypatch):
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    path = _workflow(workflow_dir / "wf.json")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.toml"
    config_file.write_text(
        f"""
[comfy_agent_view]
comfyui_user_dir = {json.dumps(str(workflow_dir))}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMFY_AGENT_VIEW_CONFIG", str(config_file))

    result = normalize_workflow(str(path), profile="safe")

    assert result.source.name == "wf.json"
    assert config_path() == config_file


def test_explicit_comfyui_user_dir_overrides_config(tmp_path, monkeypatch):
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    path = _workflow(workflow_dir / "wf.json")
    denied_dir = tmp_path / "denied"
    denied_dir.mkdir()
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        f"""
[comfy_agent_view]
comfyui_user_dir = {json.dumps(str(denied_dir))}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMFY_AGENT_VIEW_CONFIG", str(config_file))

    result = normalize_workflow(str(path), profile="safe", comfyui_user_dir=str(workflow_dir))

    assert result.source.name == "wf.json"


def test_unconfigured_comfyui_user_dir_rejects_paths(tmp_path, monkeypatch):
    path = _workflow(tmp_path / "wf.json")
    monkeypatch.setenv("COMFY_AGENT_VIEW_CONFIG", str(tmp_path / "missing.toml"))

    with pytest.raises(PermissionError, match="comfyui_user_dir is not configured"):
        normalize_workflow(str(path), profile="safe")
