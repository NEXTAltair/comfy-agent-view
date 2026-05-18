# comfy-agent-view
workflow JSONをエージェントが直読みしてトークン死起こすのを避ける
# comfy-agent-view 実装方針

## 0. なぜこのツールが必要なのか

ComfyUI の通常 `workflow.json` は、人間やエージェントが読むための recipe ではなく、ノードエディタのキャンバス状態を保存した UI snapshot に近い。

そのため、workflow JSON には生成処理に必要な情報だけでなく、以下のような UI 復元用情報が大量に混ざる。

```json
{
  "pos": [123, 456],
  "size": [315, 260],
  "flags": {},
  "order": 7,
  "mode": 0,
  "color": "#322",
  "bgcolor": "#533",
  "groups": [],
  "reroutes": []
}
```

これは ComfyUI 上で画面を再現するには必要だが、エージェントが workflow の意味を理解するにはノイズになる。

エージェントが本当に知りたいのは、主に以下である。

```text
- どの checkpoint / VAE / LoRA / ControlNet を使っているか
- positive / negative prompt が存在するか
- sampler / scheduler / steps / cfg / seed / denoise は何か
- 解像度はいくつか
- ノード同士がどう接続されているか
- img2img / txt2img / upscale / ControlNet / IPAdapter 等の構成か
- 壊れた link / slot 参照があるか
```

生の workflow JSON をそのままエージェントに読ませると、以下の問題が起きる。

1. **コンテキスト浪費**

   * UI座標、色、サイズ、折りたたみ状態、reroute、group 情報などが大量に入り、LLMの入力コンテキストを無駄に使う。

2. **読み間違い**

   * `widgets_values` が配列で保存されることがあり、ノード定義と照合しないと seed / steps / cfg などの意味が分からない。

3. **壊れた workflow の診断が難しい**

   * ComfyUI 更新や custom node 更新により、昔の workflow が `origin_slot` / `target_slot` の不整合で読めなくなることがある。
   * 例: `TypeError: can't access property "type", node.outputs[link_info.origin_slot] is undefined`

4. **プロンプト本文の不用意な露出**

   * workflow 内には過去の生成 prompt が残る。
   * 機密ではなくても、NSFW prompt や個人的に見られたくない prompt が含まれることがある。
   * そのため、デフォルトでは prompt 本文を隠し、明示時だけ `full` profile で出す必要がある。

5. **エージェント間で再利用しづらい**

   * OpenClaw、Hermes、Claude Desktop、Claude Code、Codex では plugin/tool の作法が異なる。
   * 各エージェント専用 plugin に workflow 解析ロジックを直書きすると、保守が重くなる。

このツールの目的は、ComfyUI workflow JSON から UI ノイズを除去し、接続・処理・主要パラメータ・モデル依存関係だけを抽出した **agent-readable view** を作ることである。

つまり `comfy-agent-view` は、単なる JSON 整形ツールではない。

```text
ComfyUI workflow JSON
  ↓
UI情報を削除
  ↓
links / widgets_values を意味ある構造へ正規化
  ↓
prompt本文を profile に応じて redaction
  ↓
エージェントが低コンテキストで読める dict を返す
```

この前処理により、エージェントは生の巨大 workflow JSON を読まずに、必要な構造だけを把握できる。

---

## 1. 結論

`comfy-agent-view` は **最初から MCP stdio server として実装する**。

ただし、解析ロジックを MCP handler に直書きせず、**MCP非依存の core ライブラリ**として切り出す。

推奨構成は以下。

```text
comfy-agent-view
  core/
    ComfyUI workflow JSONを解析・正規化する純粋Python実装

  schemas/
    Pydantic v2 models

  mcp/
    official MCP Python SDK / FastMCP による stdio MCP server

  cli/
    手動実行・OpenClaw wrapper・デバッグ用 CLI

  adapters/
    OpenClaw plugin wrapper などを後で追加
```

最終判断:

```text
MCP stdio-first
+ coreはMCP非依存
+ CLI同梱
+ OpenClaw pluginは必要なら薄いwrapper
+ daemonなし
+ ComfyUI pluginなし
+ PydanticAIなし
+ Pydantic v2 schema
+ official MCP Python SDK / FastMCP
```

---

## 2. なぜこの形式にするか

### 2.1 OpenClaw / Claude / Codex / Hermes で作法が違う

OpenClaw plugin、Claude Desktop extension、Codex MCP設定、Hermes plugin はそれぞれ作法が違う。

各エージェント専用 plugin を先に作ると、実装が分散して保守が重くなる。

そのため、共通面を **MCP stdio server** に寄せる。

```text
Claude Desktop
  -> MCP serverとして comfy-agent-view mcp を登録

Claude Code
  -> MCP serverとして登録

Codex
  -> config.toml の mcp_servers に登録

OpenClaw / Hermes
  -> MCP対応ならそのまま
  -> 難しければ CLI wrapper/plugin で呼ぶ
```

### 2.2 常駐サービスは不要

`comfy-agent-viewd.exe` のような HTTP daemon は MVP では重い。

必要な時だけ MCP stdio server として起動されればよい。

```text
Agent
  ↓
comfy-agent-view mcp
  ↓
workflow JSONを読む
  ↓
agent-readable dictを返す
```

stdio MCP なら、Claude や Codex などのクライアントが必要時にプロセスを起動する。

Windows service や HTTP port 管理は不要。

### 2.3 ComfyUI pluginを最初に作らない

ComfyUI側 plugin にすると、ComfyUI が起動していない時に workflow を読みにくい。

今回の目的は「エージェントが workflow ディレクトリの JSON を読む前に、無駄情報を削って正規化すること」なので、ComfyUI 本体から独立していた方が扱いやすい。

ただし、ComfyUI が起動中なら `/object_info` を参照して精度を上げる。

```text
ComfyUI起動中:
  /object_info を使って widget / input / output mapping の精度を上げる

ComfyUI停止中:
  workflow JSON単体から best-effort で解析する
```

---

## 3. プロジェクト名

```text
comfy-agent-view
```

目的は、ComfyUI workflow JSON から **agent-readable view** を作ること。

---

## 4. 実装の最終形

```text
comfy-agent-view
  ├─ core
  │   ├─ list_workflows()
  │   ├─ normalize_workflow()
  │   ├─ summarize_workflow()
  │   ├─ repair_broken_links()
  │   ├─ extract_models()
  │   ├─ redact_prompts()
  │   └─ resolve_links()
  │
  ├─ schemas
  │   └─ Pydantic v2 models
  │
  ├─ mcp
  │   └─ FastMCP stdio server
  │
  ├─ cli
  │   └─ manual/debug entrypoints
  │
  └─ adapters
      └─ OpenClaw wrapper later
```

---

## 5. Primary interface

MCP stdio server。

```bash
comfy-agent-view mcp
```

---

## 6. Secondary interface

CLI。

```bash
comfy-agent-view list "D:\ComfyUI\ComfyUI\user\default\workflows"
comfy-agent-view summarize "D:\ComfyUI\ComfyUI\user\default\workflows\foo.json" --profile safe
comfy-agent-view normalize "D:\ComfyUI\ComfyUI\user\default\workflows\foo.json" --profile debug
comfy-agent-view repair-links "D:\ComfyUI\ComfyUI\user\default\workflows\foo.json" --dry-run
```

CLIは次の用途に使う。

```text
- 手動確認
- OpenClaw plugin wrapperからの呼び出し
- MCPが使えない環境でのfallback
- Windows / WSL path問題のデバッグ
```

---

## 7. MCP tools

MVPで公開するtoolは4つ。

```text
comfy_workflow_list
comfy_workflow_summarize
comfy_workflow_normalize
comfy_workflow_repair_links
```

---

## 8. comfy_workflow_list

workflow JSONを一覧する。

### Input

```python
{
    "root": "D:\\ComfyUI\\ComfyUI\\user\\default\\workflows",
    "recursive": True,
    "limit": 100,
}
```

### Output

```python
{
    "format": "comfy_workflow_list_v1",
    "root": "D:\\ComfyUI\\ComfyUI\\user\\default\\workflows",
    "workflows": [
        {
            "path": "D:\\ComfyUI\\ComfyUI\\user\\default\\workflows\\foo.json",
            "name": "foo.json",
            "size_bytes": 123456,
            "modified_at": "2026-05-18T10:30:00+09:00",
            "format_guess": "comfy_ui_workflow",
        }
    ],
    "warnings": [],
}
```

---

## 9. comfy_workflow_summarize

軽量な構造化 summary dict を返す。

Markdown文章では返さない。

### Input

```python
{
    "path": "D:\\ComfyUI\\ComfyUI\\user\\default\\workflows\\foo.json",
    "profile": "safe",
    "detail": "compact",
}
```

### Output

```python
{
    "format": "comfy_workflow_summary_v1",
    "source": {
        "path": "D:\\ComfyUI\\ComfyUI\\user\\default\\workflows\\foo.json",
        "name": "foo.json",
        "format_detected": "comfy_ui_workflow",
    },
    "profile": "safe",
    "kind": {
        "workflow_type": "txt2img",
        "family_guess": "sdxl",
        "has_img2img": False,
        "has_controlnet": False,
        "has_ipadapter": False,
        "has_lora": True,
        "has_upscale": False,
    },
    "pipeline": {
        "main_chain": [
            "CheckpointLoaderSimple",
            "LoraLoader",
            "CLIPTextEncode",
            "EmptyLatentImage",
            "KSampler",
            "VAEDecode",
            "SaveImage",
        ],
        "terminal_nodes": ["SaveImage"],
    },
    "models": {
        "checkpoints": ["xxx.safetensors"],
        "loras": [
            {
                "name": "abc.safetensors",
                "strength_model": 0.8,
                "strength_clip": 0.8,
            }
        ],
        "vae": [],
        "controlnet": [],
    },
    "generation": {
        "width": 1024,
        "height": 1536,
        "seed": 123456,
        "steps": 28,
        "cfg": 7.0,
        "sampler": "dpmpp_2m",
        "scheduler": "karras",
        "denoise": 1.0,
    },
    "prompts": {
        "positive": {
            "present": True,
            "redacted": True,
            "token_count_estimate": 84,
        },
        "negative": {
            "present": True,
            "redacted": True,
            "token_count_estimate": 37,
        },
    },
    "stats": {
        "node_count": 18,
        "link_count": 22,
        "custom_node_count": 3,
        "unknown_widget_nodes": 1,
        "broken_link_count": 0,
    },
    "warnings": [],
}
```

---

## 10. comfy_workflow_normalize

詳細な agent-readable graph dict を返す。

### Input

```python
{
    "path": "D:\\ComfyUI\\ComfyUI\\user\\default\\workflows\\foo.json",
    "profile": "safe",
    "comfy_url": "http://127.0.0.1:8188",
    "use_object_info": "auto",
}
```

`use_object_info`:

```text
auto:
  ComfyUIが起動していれば /object_info を使う。
  失敗してもJSON単体で続行する。

never:
  /object_info を使わない。

require:
  /object_info が取れなければエラー。
```

### Output

```python
{
    "format": "comfy_agent_view_v1",
    "source": {
        "path": "D:\\ComfyUI\\ComfyUI\\user\\default\\workflows\\foo.json",
        "format_detected": "comfy_ui_workflow",
    },
    "profile": "safe",
    "nodes": [
        {
            "id": 12,
            "type": "KSampler",
            "title": "Main Sampler",
            "inputs": {
                "model": {
                    "from_node": 4,
                    "from_output": "MODEL",
                    "from_output_name": "MODEL",
                },
                "positive": {
                    "from_node": 6,
                    "from_output": "CONDITIONING",
                    "from_output_name": "CONDITIONING",
                },
                "negative": {
                    "from_node": 7,
                    "from_output": "CONDITIONING",
                    "from_output_name": "CONDITIONING",
                },
                "seed": 123456,
                "steps": 28,
                "cfg": 7.0,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 1.0,
            },
        }
    ],
    "models": {
        "checkpoints": ["xxx.safetensors"],
        "loras": [
            {
                "name": "abc.safetensors",
                "strength_model": 0.8,
                "strength_clip": 0.8,
            }
        ],
        "vae": [],
        "controlnet": [],
    },
    "generation": {
        "width": 1024,
        "height": 1536,
        "seed": 123456,
        "steps": 28,
        "cfg": 7.0,
        "sampler": "dpmpp_2m",
        "scheduler": "karras",
        "denoise": 1.0,
    },
    "prompts": {
        "positive": "[REDACTED: 84 tokens]",
        "negative": "[REDACTED: 37 tokens]",
    },
    "warnings": [],
}
```

---

## 11. comfy_workflow_repair_links

壊れた link / slot 参照を検出し、必要なら修復コピーを書き出す。

対象エラー例:

```text
TypeError: can't access property "type", node.outputs[link_info.origin_slot] is undefined
```

これは典型的には、workflow内の `origin_slot` が、元ノードの `outputs` 配列に存在しない index を指している状態。

### Input

```python
{
    "path": "D:\\ComfyUI\\ComfyUI\\user\\default\\workflows\\foo.json",
    "dry_run": True,
    "output_path": None,
}
```

### Output

```python
{
    "format": "comfy_workflow_repair_report_v1",
    "ok": False,
    "source": {
        "path": "D:\\ComfyUI\\ComfyUI\\user\\default\\workflows\\foo.json",
    },
    "broken_links": [
        {
            "link_id": 42,
            "origin_id": 17,
            "origin_slot": 2,
            "target_id": 12,
            "target_slot": 0,
            "reason": "origin_slot out of range; node has 2 outputs",
        }
    ],
    "would_remove_links": [42],
    "written_path": None,
    "message": "1 broken link detected. Run with dry_run=false and output_path to write a repaired copy.",
}
```

---

## 12. profile設計

### 12.1 safe

既定値。普段使い。

出すもの:

```text
- ノード種別
- 接続関係
- checkpoint名
- LoRA名
- VAE名
- ControlNet名
- seed
- steps
- cfg
- sampler
- scheduler
- denoise
- width / height
- promptの存在
- promptの推定token数
```

隠すもの:

```text
- positive prompt本文
- negative prompt本文
- メモノード本文
```

### 12.2 private

さらに隠す。

隠すもの:

```text
- prompt本文
- checkpoint名
- LoRA名
- VAE名
- ControlNet名
- filename_prefix
- 参照画像パス
```

用途:

```text
- 人に見せる可能性があるログ
- promptやモデル名も隠したい時
```

### 12.3 full

ユーザーが明示した時だけ使う。

出すもの:

```text
- prompt本文
- negative prompt本文
- メモノード本文
- filename_prefix
```

用途:

```text
- prompt調整
- prompt移植
- prompt差分比較
```

### 12.4 debug

壊れた workflow 調査用。

出すもの:

```text
- raw link id
- origin_slot
- target_slot
- raw inputs
- raw outputs
- raw widgets_values
- unknown_widgets
- repair candidates
```

用途:

```text
- ComfyUIでworkflowが読めない
- origin_slot / target_slot エラー
- custom node更新後の互換崩れ
```

---

## 13. 生workflowから落とすUI-only fields

MVPでは以下を削除する。

### root level

```text
config
state
groups
extra
reroutes
```

### node level

```text
pos
size
flags
order
mode
color
bgcolor
```

`properties` は全部捨てない。

title や表示名の抽出に使える場合があるので、必要情報を抜いた後に落とす。

---

## 14. link解決仕様

ComfyUI workflow は、接続を slot 番号で持つ。

概念的にはこう。

```text
link_id
origin_id
origin_slot
target_id
target_slot
type
```

正規化では、これをエージェントが読める名前付き接続に変換する。

### 変換前

```python
{
    "link": 42
}
```

### 変換後

```python
{
    "positive": {
        "from_node": 6,
        "from_output": "CONDITIONING",
        "from_output_name": "CONDITIONING",
    }
}
```

### 壊れている場合

落とさず、warningsに入れる。

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

---

## 15. widgets_values 展開仕様

### 15.1 Stage 1: 主要ノードだけ手動mapping

MVPでは以下を優先する。

```text
CheckpointLoaderSimple
VAELoader
LoraLoader
CLIPTextEncode
EmptyLatentImage
KSampler
KSamplerAdvanced
VAEDecode
SaveImage
LoadImage
ControlNetLoader
ControlNetApply
```

### 15.2 Stage 2: object_info

ComfyUIが起動している場合は `/object_info` を参照する。

取得できない場合は失敗にしない。

```python
{
    "unknown_widgets": [
        {
            "node_id": 31,
            "type": "SomeCustomNode",
            "widgets_values": [1, "foo", True],
        }
    ]
}
```

`unknown_widgets` は原則 `debug` profileでのみ出す。

---

## 16. path / security仕様

ローカルファイル読み取り tool なので、最低限の制限を core 側に持たせる。

```toml
[comfy_agent_view]
allowed_roots = [
  "D:\\ComfyUI\\ComfyUI\\user\\default\\workflows",
  "D:\\ComfyUI\\input",
  "D:\\ComfyUI\\output"
]

default_profile = "safe"
allow_full_profile = true
allow_repair_write = true
```

制約:

```text
- allowed_roots外のpathは拒否
- default profileはsafe
- repairはdry_run=Trueが既定
- output_pathもallowed_roots内に限定
- shell=True禁止
- workflow内文字列をコマンド実行に使わない
- 任意コマンド実行toolは作らない
```

---

## 17. Windows / WSL運用

前提:

```text
ComfyUI:
  Windows native

OpenClaw / Hermes:
  WSLで動くことがある

Claude Desktop / Codex:
  Windows nativeで動くことがある
```

そのため、Windows path と WSL path の両方に対応する。

### 対応すべきpath例

```text
D:\ComfyUI\ComfyUI\user\default\workflows\foo.json
/mnt/d/ComfyUI/ComfyUI/user/default/workflows/foo.json
```

WSLからWindows exeを呼ぶ場合は、必要に応じてpath変換する。

```text
/mnt/d/ComfyUI/foo.json
  ↓
D:\ComfyUI\foo.json
```

ただし、MCP server自体をWindows側で登録できる環境では、Windows pathをそのまま扱えばよい。

---

## 18. PydanticAIは使うか

MVPでは **PydanticAIを中核にしない**。

理由:

```text
- 今回の処理はLLM agentではなく、決定論的なJSON正規化
- PydanticAI Agentに依存すると層が増える
- tool schemaにはPydantic v2だけで十分
```

使うもの:

```text
Pydantic v2:
  入出力schema

official MCP Python SDK / FastMCP:
  MCP server

Typer or argparse:
  CLI
```

PydanticAIは将来、検証用agentやtoolset wrapperで使う余地はある。

ただし、normalizer本体はPydanticAI AgentやLLM呼び出しに依存させない。

---

## 19. 実装フェーズ

### Phase 1: core + MCP + CLI

最初から MCP server を主インターフェースにする。

```text
- Pydantic schemas
- workflow loader
- UI field stripper
- link resolver
- basic model extractor
- basic generation extractor
- prompt redaction
- summarize dict
- repair-links dry-run
- CLI
- MCP stdio server
```

### Phase 2: OpenClaw接続

OpenClawがMCPで扱えるなら、そのまま `comfy-agent-view mcp` を登録する。

難しければ、OpenClaw plugin wrapperを作る。

```text
OpenClaw tool call
  ↓
comfy-agent-view CLI
  ↓
JSON result
```

OpenClaw plugin内に正規化ロジックを直書きしない。

### Phase 3: Claude / Codex対応

```text
Claude Desktop:
  claude_desktop_config.json
  後でDesktop Extension化

Codex:
  config.toml の mcp_servers に登録

Claude Code:
  MCP serverとして登録
```

### Phase 4: 高度化

```text
- object_info cache
- custom node mapping追加
- API prompt format変換
- image metadata内workflow抽出
- workflow diff
- prompt diff
- ComfyUI custom node側のExport Agent Viewボタン
```

---

## 20. MVPでやらないこと

```text
- daemon / HTTP常駐サービス
- ComfyUI custom node
- workflow実行
- 画像生成queue投入
- 全custom node完全対応
- NSFW分類
- prompt自動評価
- 任意コマンド実行
```

責務はあくまで以下まで。

```text
読む
要約する
正規化する
壊れたlinkを検出する
```

---

## 21. MCP server実装イメージ

```python
from mcp.server.fastmcp import FastMCP

from comfy_agent_view.core import (
    list_workflows,
    normalize_workflow,
    summarize_workflow,
    repair_broken_links,
)

mcp = FastMCP("comfy-agent-view")


@mcp.tool()
def comfy_workflow_list(
    root: str,
    recursive: bool = True,
    limit: int = 100,
) -> dict:
    """List ComfyUI workflow JSON files."""
    return list_workflows(
        root=root,
        recursive=recursive,
        limit=limit,
    ).model_dump(mode="json")


@mcp.tool()
def comfy_workflow_summarize(
    path: str,
    profile: str = "safe",
    detail: str = "compact",
) -> dict:
    """Return a compact structured summary of a ComfyUI workflow."""
    return summarize_workflow(
        path=path,
        profile=profile,
        detail=detail,
    ).model_dump(mode="json")


@mcp.tool()
def comfy_workflow_normalize(
    path: str,
    profile: str = "safe",
    comfy_url: str | None = None,
    use_object_info: str = "auto",
) -> dict:
    """Normalize a ComfyUI workflow JSON into an agent-readable graph."""
    return normalize_workflow(
        path=path,
        profile=profile,
        comfy_url=comfy_url,
        use_object_info=use_object_info,
    ).model_dump(mode="json")


@mcp.tool()
def comfy_workflow_repair_links(
    path: str,
    dry_run: bool = True,
    output_path: str | None = None,
) -> dict:
    """Detect and optionally repair broken ComfyUI workflow links."""
    return repair_broken_links(
        path=path,
        dry_run=dry_run,
        output_path=output_path,
    ).model_dump(mode="json")


if __name__ == "__main__":
    mcp.run()
```

---

## 22. Codex設定例

```toml
[mcp_servers.comfy_agent_view]
command = "C:\\Tools\\comfy-agent-view\\comfy-agent-view.exe"
args = ["mcp"]
enabled_tools = [
  "comfy_workflow_list",
  "comfy_workflow_summarize",
  "comfy_workflow_normalize",
  "comfy_workflow_repair_links",
]
```

---

## 23. OpenClaw向け運用ルール

OpenClawのplugin説明、またはskill側にはこう書く。

```md
When inspecting ComfyUI workflow JSON files, do not read the raw JSON directly unless the user explicitly asks for raw JSON.

Use `comfy_workflow_summarize` first for overview.
Use `comfy_workflow_normalize` when node-level graph details are needed.
Use `comfy_workflow_repair_links` when diagnosing load failures, broken links, or slot errors.

Default to profile="safe".
Use profile="full" only when the user explicitly asks to inspect or edit prompt text.
Use profile="debug" only for workflow repair or compatibility diagnosis.
```

日本語版:

```md
ComfyUI workflow JSONを調査する時は、生JSONを直接読まない。
まず `comfy_workflow_summarize` を使う。
ノード単位の接続や処理内容が必要な場合は `comfy_workflow_normalize` を使う。
ロード失敗、壊れたlink、slot番号エラーの調査では `comfy_workflow_repair_links` を使う。

通常は profile="safe" を使う。
プロンプト本文の確認・編集をユーザーが明示した場合のみ profile="full" を使う。
workflow修復や互換性調査の場合のみ profile="debug" を使う。
```

---

## 24. 実装者への重要な注意

このツールは「ComfyUI workflow JSONを綺麗に整形するだけのツール」ではない。

目的は、エージェントが workflow を扱う時の以下の問題を解決することである。

```text
- 生JSONによるコンテキスト浪費を避ける
- UI情報と処理情報を分離する
- slot番号ベースの接続を意味ある接続情報に変換する
- 壊れたworkflowを落とさず診断できるようにする
- prompt本文の不用意な露出を避ける
- OpenClaw / Claude / Codex / Hermes で共通に使える形にする
```

そのため、実装では以下を守る。

```text
- coreに正規化ロジックを集約する
- MCP toolは薄いwrapperにする
- CLIも同じcoreを呼ぶ
- OpenClaw pluginに解析処理を直書きしない
- prompt本文はsafe profileでは返さない
- 修復系はdry-runを既定にする
- 任意パス読み取りを避けるためallowed_rootsを使う
```

MVP名は `comfy-agent-view`。

最初の実装目標は、**ComfyUI workflow JSONを安全・軽量・構造化された agent-readable dict に変換する MCP tool** である。
