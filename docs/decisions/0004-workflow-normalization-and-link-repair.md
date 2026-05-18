# ADR 0004: Workflow Normalization and Link Repair

- **日付**: 2026-05-19
- **ステータス**: Accepted

## Context

ComfyUI workflow は接続を slot 番号で持つ。`widgets_values` も配列で保存されることがあり、ノード定義と照合しないと seed / steps / cfg などの意味が分からない。

ComfyUI 更新や custom node 更新により、昔の workflow が `origin_slot` / `target_slot` の不整合で読めなくなることもある。典型例は `TypeError: can't access property "type", node.outputs[link_info.origin_slot] is undefined` である。

## Decision

正規化では slot 番号ベースの接続を、エージェントが読める名前付き接続に変換する。

変換前:

```python
{
    "link": 42
}
```

変換後:

```python
{
    "positive": {
        "from_node": 6,
        "from_output": "CONDITIONING",
        "from_output_name": "CONDITIONING",
    }
}
```

壊れている場合は例外で落とさず、warnings に入れる。

```python
{
    "level": "error",
    "code": "BROKEN_ORIGIN_SLOT",
    "node_id": 12,
    "input": "positive",
    "link_id": 42,
    "message": "origin_slot 2 is out of range for node 17 outputs length 2",
}
```

`widgets_values` 展開は段階的に行う。MVP では主要ノードを手動 mapping し、ComfyUI が起動している場合は `/object_info` を best-effort で参照する。取得できない場合は失敗にしない。

MVP で優先するノード:

- `CheckpointLoaderSimple`
- `VAELoader`
- `LoraLoader`
- `CLIPTextEncode`
- `EmptyLatentImage`
- `KSampler`
- `KSamplerAdvanced`
- `VAEDecode`
- `SaveImage`
- `LoadImage`
- `ControlNetLoader`
- `ControlNetApply`

## Rationale

- **slot番号をそのまま返す案（却下）**: エージェントが意味を読み取りにくく、生成構成の理解に余計な推論が必要になる。
- **不整合時に即エラー終了する案（却下）**: 壊れた workflow の診断ができない。
- **warnings + repair report 案（採用）**: 読める部分は保持し、壊れた箇所を局所化できる。

## Consequences

- `comfy_workflow_normalize` は agent-readable graph dict を返す。
- `comfy_workflow_repair_links` は dry-run を既定にし、壊れた link / slot 参照の検出結果を返す。
- 修復書き込みをする場合も、元ファイルを直接破壊せず output path を明示させる。
- unknown widgets は原則 `debug` profile でのみ出す。
