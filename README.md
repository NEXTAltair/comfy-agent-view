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
comfy-agent-view list /path/to/workflows
comfy-agent-view summarize /path/to/workflow.json --profile safe
comfy-agent-view normalize /path/to/workflow.json --profile debug
comfy-agent-view repair-links /path/to/workflow.json --dry-run
comfy-agent-view mcp
```

MCP serverを使う場合は optional dependency を入れる。

```bash
uv pip install -e '.[mcp]'
```

読み取り範囲は ComfyUI の `user` ディレクトリ配下に限定する。恒久設定は user config に書き、CLI の `--comfyui-user-dir` は一時実行やデバッグだけに使う。

user config の場所は次で確認できる。

```bash
comfy-agent-view --print-config-path
```

設定例:

```toml
[comfy_agent_view]
comfyui_user_dir = "H:\\StabilityMatrix-win-x64\\Data\\Packages\\ComfyUI\\user"
default_profile = "safe"
allow_full_profile = true
```

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

## Design Records

暫定仕様と設計判断は ADR に分割した。

- [docs/decisions/README.md](docs/decisions/README.md)

主要な判断:

- MCP stdio-first とし、core は MCP 非依存にする
- CLI は手動確認と fallback 用に同梱する
- prompt本文は既定の `safe` profile では返さない
- link / slot 破損は落とさず warnings と repair report で診断する
- daemon、ComfyUI custom node、workflow実行、画像生成 queue 投入は MVP では扱わない
