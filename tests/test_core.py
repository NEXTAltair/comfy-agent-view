from __future__ import annotations

import json

import pytest

from comfy_agent_view.config import config_path, object_info_cache_path
from comfy_agent_view.core import fetch_object_info, list_workflows, normalize_workflow, repair_broken_links, summarize_workflow


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


def test_normalize_collects_unet_and_clip_loaders(tmp_path):
    path = _workflow(tmp_path / "wf.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["nodes"].extend(
        [
            {
                "id": 6,
                "type": "UNETLoader",
                "widgets_values": ["anima-preview.safetensors", "default"],
            },
            {
                "id": 7,
                "type": "CLIPLoader",
                "widgets_values": ["qwen_3_06b_base.safetensors", "stable_diffusion", "default"],
            },
        ]
    )
    path.write_text(json.dumps(data), encoding="utf-8")

    result = normalize_workflow(str(path), profile="safe", comfyui_user_dir=str(tmp_path))

    assert result.models["unet"] == [{"name": "anima-preview.safetensors", "weight_dtype": "default"}]
    assert result.models["clip"] == [
        {"name": "qwen_3_06b_base.safetensors", "type": "stable_diffusion", "device": "default"}
    ]


def test_normalize_uses_default_object_info_cache_for_widgets(tmp_path, monkeypatch):
    config_file = tmp_path / "config" / "config.toml"
    config_file.parent.mkdir()
    config_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("COMFY_AGENT_VIEW_CONFIG", str(config_file))
    object_info_cache_path().write_text(
        json.dumps(
            {
                "CustomModelLoader": {
                    "input": {
                        "required": {
                            "model_name": ["COMBO", {}],
                            "weight_dtype": ["COMBO", {}],
                            "linked_model": ["MODEL", {"forceInput": True}],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    path = _workflow(tmp_path / "wf.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["nodes"] = [
        {
            "id": 99,
            "type": "CustomModelLoader",
            "widgets_values": ["custom.safetensors", "default"],
        }
    ]
    data["links"] = []
    path.write_text(json.dumps(data), encoding="utf-8")

    result = normalize_workflow(str(path), profile="debug", comfyui_user_dir=str(tmp_path))

    assert result.nodes[0].inputs["model_name"] == "custom.safetensors"
    assert result.nodes[0].inputs["weight_dtype"] == "default"
    assert result.nodes[0].unknown_widgets is None


def test_known_widget_map_takes_precedence_over_object_info_order(tmp_path, monkeypatch):
    config_file = tmp_path / "config" / "config.toml"
    config_file.parent.mkdir()
    config_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("COMFY_AGENT_VIEW_CONFIG", str(config_file))
    object_info_cache_path().write_text(
        json.dumps(
            {
                "KSampler": {
                    "input": {
                        "required": {
                            "cfg": ["FLOAT", {}],
                            "denoise": ["FLOAT", {}],
                            "sampler_name": ["COMBO", {}],
                            "scheduler": ["COMBO", {}],
                            "seed": ["INT", {}],
                            "steps": ["INT", {}],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    path = _workflow(tmp_path / "wf.json")

    result = normalize_workflow(str(path), profile="safe", comfyui_user_dir=str(tmp_path))

    assert result.generation["seed"] == 123
    assert result.generation["steps"] == 28
    assert result.generation["cfg"] == 7.0
    assert result.generation["sampler"] == "dpmpp_2m"
    assert result.generation["scheduler"] == "karras"


def test_fetch_object_info_writes_default_cache(tmp_path, monkeypatch):
    config_file = tmp_path / "config" / "config.toml"
    config_file.parent.mkdir()
    config_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("COMFY_AGENT_VIEW_CONFIG", str(config_file))

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"KSampler": {"input": {"required": {}}}}'

    monkeypatch.setattr("comfy_agent_view.core.urlopen", lambda url, timeout: FakeResponse())

    result = fetch_object_info("http://comfy.local:8188")

    assert result.path == str(object_info_cache_path())
    assert result.source_url == "http://comfy.local:8188/object_info"
    assert result.node_count == 1
    assert json.loads(object_info_cache_path().read_text(encoding="utf-8"))["KSampler"]


def test_summarize_returns_structured_counts(tmp_path):
    path = _workflow(tmp_path / "wf.json")
    result = summarize_workflow(str(path), comfyui_user_dir=str(tmp_path))
    assert result.stats["node_count"] == 5
    assert result.stats["link_count"] == 3
    assert result.kind["has_lora"] is False


def test_list_workflows_defaults_to_comfyui_default_workflow_dir(tmp_path):
    workflow_dir = tmp_path / "default" / "workflows"
    workflow_dir.mkdir(parents=True)
    _workflow(workflow_dir / "wf.json")
    _workflow(tmp_path / "root_noise.json")

    result = list_workflows(comfyui_user_dir=str(tmp_path))

    assert result.root == str(workflow_dir.resolve())
    assert [item.name for item in result.workflows] == ["wf.json"]


def test_default_workflow_dir_may_be_symlink_target_outside_user_dir(tmp_path):
    user_dir = tmp_path / "user"
    default_dir = user_dir / "default"
    target_dir = tmp_path / "Workflows"
    default_dir.mkdir(parents=True)
    target_dir.mkdir()
    (default_dir / "workflows").symlink_to(target_dir, target_is_directory=True)
    _workflow(target_dir / "wf.json")

    result = list_workflows(comfyui_user_dir=str(user_dir))

    assert result.root == str(target_dir.resolve())
    assert [item.name for item in result.workflows] == ["wf.json"]


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
