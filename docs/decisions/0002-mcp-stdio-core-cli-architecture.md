# ADR 0002: MCP stdio + Core/CLI Architecture

- **日付**: 2026-05-19
- **ステータス**: Accepted

## Context

OpenClaw plugin、Claude Desktop extension、Codex MCP 設定、Hermes plugin はそれぞれ作法が違う。各エージェント専用 plugin に workflow 解析ロジックを直書きすると、実装が分散して保守が重くなる。

また、MVP では `comfy-agent-viewd.exe` のような HTTP daemon や Windows service を持つほどの常駐性は不要である。

## Decision

`comfy-agent-view` は最初から MCP stdio server として実装する。ただし、解析ロジックを MCP handler に直書きせず、MCP 非依存の core ライブラリとして切り出す。

推奨構成:

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

Primary interface は `comfy-agent-view mcp`、secondary interface は CLI とする。

MVP で公開する MCP tools:

- `comfy_workflow_list`
- `comfy_workflow_summarize`
- `comfy_workflow_normalize`
- `comfy_workflow_repair_links`

## Rationale

- **MCP stdio 採用の理由**: Claude Desktop、Claude Code、Codex などが共通に扱える。クライアントが必要時にプロセスを起動するため、HTTP port 管理が不要。
- **core 分離の理由**: MCP、CLI、OpenClaw wrapper が同じ正規化ロジックを呼べる。実装の分散を避けられる。
- **CLI 同梱の理由**: 手動確認、OpenClaw plugin wrapper からの呼び出し、MCP が使えない環境での fallback、Windows / WSL path 問題のデバッグに使える。
- **ComfyUI plugin を最初に作らない理由**: ComfyUI が起動していない時にも workflow ファイルを読める方が、このツールの目的に合う。

## Consequences

- daemon / HTTP 常駐サービスは MVP では作らない。
- MCP handler は薄い wrapper にし、正規化処理は core に集約する。
- OpenClaw / Hermes で MCP 対応が難しい場合は CLI wrapper を後で追加する。
- PydanticAI は normalizer 本体に使わない。今回の処理は LLM agent ではなく、決定論的な JSON 正規化である。
