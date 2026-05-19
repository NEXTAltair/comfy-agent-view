# comfy-agent-view

ComfyUI workflow JSONを、エージェントが低コンテキストで扱える構造化ビューに変換するツール。

生の workflow JSON はノードエディタの UI snapshot に近く、座標、色、サイズ、group、reroute などの復元用情報を大量に含む。`comfy-agent-view` はそこから処理理解に必要な情報だけを抽出し、prompt本文は既定で隠す。

## Current MVP

このリポジトリは Python package として実装済み。

```bash
uv venv .venv
uv pip install -e '.[dev]'
.venv/bin/comfy-agent-view --help
```

主なコマンド:

```bash
comfy-agent-view list
comfy-agent-view list /path/to/other/workflows
comfy-agent-view summarize /path/to/workflow.json --profile safe
comfy-agent-view normalize /path/to/workflow.json --profile debug
comfy-agent-view repair-links /path/to/workflow.json --dry-run
comfy-agent-view diagnose-load /path/to/workflow.json
comfy-agent-view apply-workflow-patch /path/to/patch.json --no-dry-run
comfy-agent-view fetch-object-info --comfy-url http://127.0.0.1:8188
comfy-agent-view mcp
```

MCP serverを使う場合は optional dependency を入れる。

```bash
uv pip install -e '.[mcp]'
```

読み取り範囲は ComfyUI の `user` ディレクトリ配下に限定する。恒久設定は user config に書き、CLI の `--comfyui-user-dir` は一時実行やデバッグだけに使う。

`list` の root を省略した場合は `<comfyui_user_dir>/default/workflows` を見る。ComfyUI の通常 workflow 保存先は `user/default/workflows` なので、設定項目は user ディレクトリのままにして、workflow 操作時だけ既定パスを足す。

そのディレクトリが symlink / junction の場合はリンク先を workflow root として扱う。Stability Matrix ではこの形になることがある。

user config の場所は次で確認できる。

```bash
comfy-agent-view --print-config-path
```

ComfyUI の `/object_info` は固定ファイルではなく起動中 API から得る。custom node の `widgets_values` を名前付き input に戻したい場合は、ComfyUI 起動中に一度キャッシュする。

```bash
comfy-agent-view fetch-object-info --comfy-url http://127.0.0.1:8188
comfy-agent-view --print-object-info-path
```

既定保存先は user config と同じディレクトリの `object_info.json`。`normalize` / `summarize` はこの既定キャッシュがあれば自動で参照し、なければ静的 fallback を使う。

ComfyUI 上で workflow 読み込みエラーになる場合は、`diagnose-load` を使う。これは workflow 静的診断、`object_info` cache、`comfyui_user_dir` 直下の `comfyui.log` / `comfyui.prev.log` / `comfyui.prev2.log` を読み、正規化された小さい診断 report を返す。log 全文や prompt 本文は返さない。

```bash
comfy-agent-view diagnose-load /path/to/workflow.json
```

出力は人間向け文章ではなく agent handoff 用 JSON。最上位の `summary.status`, `summary.primary_issue`, `summary.next_action` を読めば次の安全な操作候補が分かる。

frontend の Error Report は通常不要。既に手元にある場合だけ補助入力として渡せる。

```bash
comfy-agent-view diagnose-load /path/to/workflow.json --error-report-text '...'
```

workflow を修正する場合は、agent が workflow 本体を直接編集せず、patch spec を作って `apply-workflow-patch` に渡す。原本は読み取り専用で、修正は必ず `output` に書く。

```json
{
  "source": "/path/to/Caption.json",
  "output": "/path/to/Caption.fixed.json",
  "operations": [
    {"op": "set", "node_id": 42, "path": ["widgets_values", 0], "value": "..."},
    {"op": "delete_link", "link_id": 123},
    {"op": "set_input_link", "node_id": 51, "input": "image", "link_id": 456}
  ]
}
```

既定は dry-run。実際に書く場合は `--no-dry-run` を付ける。既存 output は `--overwrite` なしでは拒否する。

設定例:

```toml
[comfy_agent_view]
comfyui_user_dir = "H:\\StabilityMatrix-win-x64\\Data\\Packages\\ComfyUI\\user"
default_profile = "safe"
allow_full_profile = true
```

同じ内容のテンプレートは `config.example.toml` にある。実際の `config.toml` はローカル設定なので追跡しない。

設定がない状態で workflow path を読むとエラーになる。推測で任意パスを読む fallback は持たない。

## Interfaces

- Primary: MCP stdio server (`comfy-agent-view mcp`)
- Secondary: CLI for manual checks, wrapper integrations, and debugging
- Core library: MCP/CLIから共通利用する純粋Python実装

MVPで公開する MCP tools:

- `comfy_workflow_list`
- `comfy_workflow_summarize`
- `comfy_workflow_normalize`
- `comfy_workflow_repair_links`
- `comfy_object_info_fetch`

## Design Records

暫定仕様と設計判断は ADR に分割した。

- [docs/decisions/README.md](docs/decisions/README.md)

主要な判断:

- MCP stdio-first とし、core は MCP 非依存にする
- CLI は手動確認と fallback 用に同梱する
- prompt本文は既定の `safe` profile では返さない
- link / slot 破損は落とさず warnings と repair report で診断する
- daemon、ComfyUI custom node、workflow実行、画像生成 queue 投入は MVP では扱わない
