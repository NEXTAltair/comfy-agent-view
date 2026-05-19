# ADR 0006: Runtime Load Error Diagnostics

- **日付**: 2026-05-19
- **ステータス**: Accepted

## Context

`normalize` と `repair-links` は workflow JSON を静的に読む。これは link / slot 破損や主要パラメータ抽出には効くが、ComfyUI が実際に workflow を読み込む時の失敗までは再現できない。

Caption 系 workflow のように custom node、frontend extension、古い node definition、Manager 経由の node 名変更が絡む場合、ComfyUI 上では読み込みエラーになるが、静的 JSON だけでは原因が見えないことがある。

この種の問題では、ComfyUI の runtime log と `/object_info` cache をあわせて見る必要がある。Stability Matrix / Windows 環境では、少なくとも次の log が `comfyui_user_dir` 直下に存在することがある。

```text
<comfyui_user_dir>/comfyui.log
<comfyui_user_dir>/comfyui.prev.log
<comfyui_user_dir>/comfyui.prev2.log
```

ただし log は大きく、prompt、file path、extension 情報、traceback を含む。`comfy-agent-view` が任意 log viewer になると、path security と redaction policy が崩れる。

## Decision

runtime load error は、通常の workflow normalize とは別の診断フローとして扱う。

対象 workflow を指定して、以下を同じ report にまとめる。

1. workflow の静的 normalize / repair dry-run 結果
2. 既定 cache の `/object_info` に node type が存在するか
3. `comfyui_user_dir` 直下の ComfyUI log tail
4. workflow に含まれる node type / node id と log 行の照合
5. 修正候補

診断 tool は読み取り専用を既定にする。修正は最初から原本へ書き込まず、dry-run report と patch plan を返す。

初期対象 log は `comfyui.log`, `comfyui.prev.log`, `comfyui.prev2.log` に限定する。再帰的な log 探索や任意 log path 指定は持たない。

report は以下の形を目標にする。

```python
{
    "format": "comfy_runtime_diagnostic_v1",
    "workflow": ".../Caption.json",
    "static": {
        "normalize_ok": true,
        "broken_link_count": 0,
        "unknown_widget_nodes": 2,
    },
    "object_info": {
        "missing_node_types": ["SomeCaptionNode"],
        "known_node_types": ["LoadImage", "CLIPTextEncode"],
    },
    "logs": {
        "files_checked": ["comfyui.log", "comfyui.prev.log"],
        "matched_errors": [
            {
                "file": "comfyui.log",
                "line": 1234,
                "level": "error",
                "node_type": "SomeCaptionNode",
                "message": "Cannot import ...",
            }
        ],
    },
    "repair_plan": [
        {
            "kind": "missing_custom_node",
            "node_type": "SomeCaptionNode",
            "action": "install_or_update_custom_node",
            "confidence": "high",
        }
    ],
}
```

修正候補は段階を分ける。

1. **構造修復**: broken link / slot の除去や接続修正。既存の `repair-links` と同じく dry-run が既定。
2. **schema 更新**: `/object_info` cache が古い場合は `fetch-object-info` を再実行する。
3. **custom node 欠落**: log と object_info から欠落 node type を出す。install / update は提案までで、tool は実行しない。
4. **workflow 互換修正**: node type 名変更、widget 名変更、不要 node 削除など。原本へ直接書かず、output path への修正版生成を明示操作にする。

`comfyui.db` はこの診断の初期実装でも読まない。必要なら将来 ADR を追加する。

## Rationale

- **静的 normalize だけで解決する案（却下）**: ComfyUI の loader / extension 側で落ちる問題を見逃す。
- **log を全文返す案（却下）**: prompt やローカル path を過剰に露出する。
- **任意 log path を読ませる案（却下）**: local file viewer 化してしまい、`comfyui_user_dir` 境界の意味が薄れる。
- **ComfyUI API に queue 投入して再現する案（MVP では却下）**: 画像生成や副作用を伴う。診断 tool は読み取り専用から始める。
- **log tail + object_info + static report 案（採用）**: 実行時エラーと workflow graph を同じ座標系で照合でき、修正対象を絞れる。

## Consequences

- 新しい診断系 command / MCP tool を追加する余地ができる。
- `comfyui_user_dir` を許可範囲にした理由が明確になる。workflow だけでなく log 診断にも使う。
- log 読み取りは tail / matching / redaction を前提にし、全文 dump はしない。
- 診断 report は修正候補を返すが、custom node install、ComfyUI 起動、queue 投入は行わない。
- 修正版 workflow を書く場合は、従来通り `dry_run=false` と `output_path` の明示を要求する。
