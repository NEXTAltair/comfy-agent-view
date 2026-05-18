# Architecture Decision Records

`comfy-agent-view` の重要な設計判断を記録するドキュメント群。

| ADR | タイトル | 日付 | ステータス |
|-----|---------|------|-----------|
| [0001](0001-agent-readable-workflow-view.md) | Agent-Readable Workflow View | 2026-05-19 | Accepted |
| [0002](0002-mcp-stdio-core-cli-architecture.md) | MCP stdio + Core/CLI Architecture | 2026-05-19 | Accepted |
| [0003](0003-profile-redaction-policy.md) | Profile Redaction Policy | 2026-05-19 | Accepted |
| [0004](0004-workflow-normalization-and-link-repair.md) | Workflow Normalization and Link Repair | 2026-05-19 | Accepted |
| [0005](0005-path-security-and-runtime-boundaries.md) | Path Security and Runtime Boundaries | 2026-05-19 | Accepted |

## ADR テンプレート

```markdown
# ADR XXXX: タイトル

- **日付**: YYYY-MM-DD
- **ステータス**: Proposed | Accepted | Deprecated | Superseded by [XXXX]

## Context

なぜこの決定が必要だったか。問題の背景と制約。

## Decision

何を決定したか。

## Rationale

なぜこの選択をしたか。他の選択肢との比較。

## Consequences

この決定による影響。良い点・悪い点・トレードオフ。
```
