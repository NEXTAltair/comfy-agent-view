# ADR 0005: Path Security and Runtime Boundaries

- **日付**: 2026-05-19
- **ステータス**: Accepted

## Context

`comfy-agent-view` はローカルファイル読み取り tool である。MCP や wrapper 経由で使う場合、任意パス読み取りや意図しない書き込みを避ける必要がある。

また、Windows native の ComfyUI、WSL 上の OpenClaw / Hermes、Windows native の Claude Desktop / Codex が混在する可能性がある。

## Decision

core 側に最低限の path / security 制約を持たせる。

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

- `allowed_roots` 外の path は拒否する
- default profile は `safe`
- repair は `dry_run=True` が既定
- `output_path` も `allowed_roots` 内に限定する
- `shell=True` は使わない
- workflow 内文字列をコマンド実行に使わない
- 任意コマンド実行 tool は作らない

Windows path と WSL path の両方を扱う。

```text
D:\ComfyUI\ComfyUI\user\default\workflows\foo.json
/mnt/d/ComfyUI/ComfyUI/user/default/workflows/foo.json
```

WSL から Windows exe を呼ぶ場合は、必要に応じて path 変換する。

## Rationale

- **制限なしで任意パスを読む案（却下）**: MCP tool として危険すぎる。
- **ComfyUI 起動中だけ扱う案（却下）**: workflow ディレクトリの JSON を直接読む目的に合わない。
- **allowed_roots + dry-run default 案（採用）**: 実用性を保ちながら、読み取り範囲と修復書き込みを制御できる。

## Consequences

- 利用者は `COMFY_AGENT_VIEW_ALLOWED_ROOTS` または CLI の `--allowed-root` で許可範囲を設定する。
- 修復系 tool は既定で書き込まない。
- Windows / WSL の path 差分は core または adapter 層で吸収する。
- workflow 実行、画像生成 queue 投入、任意コマンド実行は MVP の責務外とする。
