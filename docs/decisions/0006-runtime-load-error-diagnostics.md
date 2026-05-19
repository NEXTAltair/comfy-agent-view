# ADR 0006: Runtime Load Error Diagnostics

- **日付**: 2026-05-19
- **ステータス**: Accepted

## Context

`normalize` と `repair-links` は workflow JSON を静的に読む。これは link / slot 破損や主要パラメータ抽出には効くが、ComfyUI が実際に workflow を読み込む時の失敗までは再現できない。

Caption 系 workflow のように custom node、frontend extension、古い node definition、Manager 経由の node 名変更が絡む場合、ComfyUI 上では読み込みエラーになるが、静的 JSON だけでは原因が見えないことがある。

実例として、ComfyUI frontend の Error Report には次のような client-side stack trace が出ることがある。

```text
Exception Type: ワークフローデータの再読み込みエラーにより、読み込みが中止されました
Exception Message: TypeError: can't access property "type", node.outputs[link_info.origin_slot] is undefined
beforeRegisterNodeDef/nodeType.prototype.onConnectionsChange@.../extensions/ComfyUI-Impact-Pack/impact-pack.js:399:6
...
loadGraphData@.../assets/dialogService-*.js
```

これは server log の import error ではなく、frontend が graph を configure する途中で extension hook が壊れた link / slot を参照して落ちる例である。この場合、server log tail だけでは原因が見えない。

ComfyUI frontend の Error Report は、永続保存された標準 log file ではなく、frontend が現在の error object、system information、最近の log 断片を組み立てて表示する診断テキストとして扱う。`comfy-agent-view` は Error Report がファイルとして存在することに依存しない。

この種の問題では、ComfyUI の runtime log と `/object_info` cache をあわせて見る必要がある。Stability Matrix / Windows 環境では、少なくとも次の log が `comfyui_user_dir` 直下に存在することがある。

```text
<comfyui_user_dir>/comfyui.log
<comfyui_user_dir>/comfyui.prev.log
<comfyui_user_dir>/comfyui.prev2.log
```

ただし log は大きく、prompt、file path、extension 情報、traceback を含む。`comfy-agent-view` が任意 log viewer になると、path security と redaction policy が崩れる。

log 全文を LLM に渡して要約させる設計も採用しない。容量が大きすぎ、prompt / path 漏れの危険があり、同じ log から同じ診断を返す再現性も落ちる。

## Decision

runtime load error は、通常の workflow normalize とは別の診断フローとして扱う。

対象 workflow と、任意の pasted Error Report text を指定して、以下を同じ report にまとめる。Error Report text は supplemental input であり、必須ではない。

1. workflow の静的 normalize / repair dry-run 結果
2. 既定 cache の `/object_info` に node type が存在するか
3. `comfyui_user_dir` 直下の ComfyUI log から deterministic parser で作った正規化 event
4. pasted Error Report text から deterministic parser で作った正規化 event
5. workflow に含まれる node type / node id と正規化 event の照合
6. 修正候補

診断 tool は読み取り専用を既定にする。修正は最初から原本へ書き込まず、dry-run report と patch plan を返す。

初期対象 log は `comfyui.log`, `comfyui.prev.log`, `comfyui.prev2.log` に限定する。再帰的な log 探索や任意 log path 指定は持たない。

log 処理は LLM を介さず、次の順序で行う。

```text
raw log files
  -> bounded tail reader
  -> line parser / traceback grouper
  -> redactor
  -> event classifier
  -> compact diagnostic report
```

Error Report text が与えられた場合も raw text として LLM に渡さず、deterministic parser で section を分解する。

```text
pasted ComfyUI Error Report markdown
  -> section parser
  -> stack frame parser
  -> redactor
  -> event classifier
  -> compact diagnostic report
```

bounded tail reader は file 全体を読まない。既定では各 log の末尾だけを対象にし、実装時の初期値は以下を目安にする。

- 最大 bytes: 1 MiB / file
- 最大 lines: 5000 / file
- 最大 events: 200 / report
- 最大 message length: 500 chars / event
- traceback は先頭 error 行、例外型、最後の数 frame だけ保持する

line parser は ComfyUI の一般的な timestamp prefix を優先して読む。

```text
[2026-05-19 11:43:49.614] To see the GUI go to: http://0.0.0.0:8188
```

timestamp がない継続行は直前 event にぶら下げる。ANSI color code は削除する。

正規化 event は以下の最小形にする。

```python
{
    "file": "comfyui.log",
    "line_start": 1234,
    "line_end": 1240,
    "timestamp": "2026-05-19T11:43:49.614+09:00",
    "severity": "error",
    "category": "missing_custom_node",
    "source": "ComfyUI-Manager",
    "node_type": "SomeCaptionNode",
    "exception_type": "ImportError",
    "message": "Cannot import ...",
    "fingerprint": "sha256:...",
}
```

pasted Error Report text 由来の event は以下の形を許す。

```python
{
    "source": "comfyui_frontend_error_report",
    "severity": "error",
    "category": "frontend_graph_load_error",
    "exception_type": "TypeError",
    "message": "can't access property \"type\", node.outputs[link_info.origin_slot] is undefined",
    "extension": "ComfyUI-Impact-Pack",
    "frontend_hook": "onConnectionsChange",
    "asset": "impact-pack.js",
    "stack_top": "beforeRegisterNodeDef/nodeType.prototype.onConnectionsChange",
    "node_id": None,
    "node_type": None,
    "repair_hint": "run repair-links and inspect BROKEN_ORIGIN_SLOT warnings",
}
```

severity は `debug | info | warning | error` に正規化する。文字列に `ERROR`, `Exception`, `Traceback`, `Cannot import`, `ModuleNotFoundError`, `ImportError` が含まれる場合は原則 `error` とする。`Warning`, `DEPRECATION WARNING`, `not installed`, `outdated cache` は `warning` とする。

category はまず以下に寄せる。

- `missing_custom_node`
- `custom_node_import_error`
- `missing_python_module`
- `broken_origin_slot`
- `frontend_graph_load_error`
- `frontend_extension_error`
- `deprecated_api`
- `object_info_schema_mismatch`
- `model_resolution_warning`
- `manager_cache_warning`
- `startup_info`
- `unknown`

redactor は raw path / prompt / long value をそのまま返さない。Windows / POSIX path は basename または path kind に丸める。workflow file name、node type、exception type、custom node package nameは診断に必要なので残す。

frontend stack trace の URL は origin と hashed asset path を落とし、extension 名、asset basename、function 名だけを保持する。`http://127.0.0.1:8188/extensions/ComfyUI-Impact-Pack/impact-pack.js:399:6` は `extension=ComfyUI-Impact-Pack`, `asset=impact-pack.js`, `line=399` のように正規化する。

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
        "events_scanned": 147,
        "events_returned": 8,
        "matched_errors": [
            {
                "file": "comfyui.log",
                "line": 1234,
                "level": "error",
                "category": "custom_node_import_error",
                "node_type": "SomeCaptionNode",
                "message": "Cannot import ...",
            }
        ],
    },
    "frontend_error": {
        "present": true,
        "category": "frontend_graph_load_error",
        "exception_type": "TypeError",
        "extension": "ComfyUI-Impact-Pack",
        "message": "can't access property \"type\", node.outputs[link_info.origin_slot] is undefined",
    },
    "repair_plan": [
        {
            "kind": "broken_origin_slot",
            "action": "run_repair_links",
            "confidence": "high",
        }
    ],
}
```

修正候補は段階を分ける。

1. **構造修復**: broken link / slot の除去や接続修正。`node.outputs[link_info.origin_slot] is undefined` は `BROKEN_ORIGIN_SLOT` として扱い、既存の `repair-links` と同じく dry-run が既定。
2. **schema 更新**: `/object_info` cache が古い場合は `fetch-object-info` を再実行する。
3. **custom node 欠落**: log と object_info から欠落 node type を出す。install / update は提案までで、tool は実行しない。
4. **workflow 互換修正**: node type 名変更、widget 名変更、不要 node 削除など。原本へ直接書かず、output path への修正版生成を明示操作にする。

`comfyui.db` はこの診断の初期実装でも読まない。必要なら将来 ADR を追加する。

## Rationale

- **静的 normalize だけで解決する案（却下）**: ComfyUI の loader / extension 側で落ちる問題を見逃す。
- **log を全文返す案（却下）**: prompt やローカル path を過剰に露出する。
- **log 全文を LLM に渡して要約する案（却下）**: 容量、漏洩、非決定性の問題がある。LLM が見るのは正規化済みの小さい report だけにする。
- **frontend Error Report の保存先を探して読む案（却下）**: Error Report は標準の永続ファイルとは限らない。貼り付けられた supplemental text として受け取り、通常診断は server log と workflow 静的解析で成立させる。
- **frontend Error Report を人間用 markdown のまま扱う案（却下）**: stack trace と system info が混ざり、診断に必要な extension / hook / exception を安定して拾えない。
- **任意 log path を読ませる案（却下）**: local file viewer 化してしまい、`comfyui_user_dir` 境界の意味が薄れる。
- **ComfyUI API に queue 投入して再現する案（MVP では却下）**: 画像生成や副作用を伴う。診断 tool は読み取り専用から始める。
- **deterministic log parser + object_info + static report 案（採用）**: 実行時エラーと workflow graph を同じ座標系で照合でき、修正対象を絞れる。LLM に渡す前に容量と形式を制御できる。

## Consequences

- 新しい診断系 command / MCP tool を追加する余地ができる。
- `comfyui_user_dir` を許可範囲にした理由が明確になる。workflow だけでなく log 診断にも使う。
- log 読み取りは bounded tail / parser / classifier / redaction を前提にし、全文 dump はしない。
- LLM / agent には raw log や raw pasted Error Report ではなく、正規化済み event と compact report だけを渡す。
- frontend 由来の `node.outputs[link_info.origin_slot] is undefined` は、まず `repair-links` の broken origin slot 診断へ接続する。
- 診断 report は修正候補を返すが、custom node install、ComfyUI 起動、queue 投入は行わない。
- 修正版 workflow を書く場合は、従来通り `dry_run=false` と `output_path` の明示を要求する。
