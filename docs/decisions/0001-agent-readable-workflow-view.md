# ADR 0001: Agent-Readable Workflow View

- **日付**: 2026-05-19
- **ステータス**: Accepted

## Context

ComfyUI の通常 `workflow.json` は、人間やエージェントが読む recipe ではなく、ノードエディタのキャンバス状態を保存した UI snapshot に近い。

workflow JSON には生成処理に必要な情報だけでなく、`pos`、`size`、`flags`、`order`、`mode`、`color`、`bgcolor`、`groups`、`reroutes` などの UI 復元用情報が混ざる。これは ComfyUI 上で画面を再現するには必要だが、エージェントが workflow の意味を理解するにはノイズになる。

エージェントが主に知りたいのは、checkpoint / VAE / LoRA / ControlNet、positive / negative prompt の有無、sampler / scheduler / steps / cfg / seed / denoise、解像度、ノード接続、txt2img / img2img / upscale / ControlNet / IPAdapter などの構成、壊れた link / slot 参照である。

## Decision

`comfy-agent-view` は、ComfyUI workflow JSON から UI ノイズを除去し、接続・処理・主要パラメータ・モデル依存関係だけを抽出した **agent-readable view** を作る。

変換の基本フローは以下とする。

```text
ComfyUI workflow JSON
  -> UI情報を削除
  -> links / widgets_values を意味ある構造へ正規化
  -> prompt本文を profile に応じて redaction
  -> エージェントが低コンテキストで読める dict を返す
```

MVP の責務は、読む、要約する、正規化する、壊れた link を検出する、までに限定する。

## Rationale

- **生JSONをそのまま読ませる案（却下）**: UI座標、色、サイズ、折りたたみ状態、reroute、group 情報で LLM 入力コンテキストを浪費する。
- **Markdown要約だけ返す案（却下）**: エージェント間で再利用しづらく、後続 tool / plugin から構造化処理しにくい。
- **agent-readable dict 案（採用）**: 必要情報を構造化して返せるため、OpenClaw、Claude、Codex、Hermes で共通利用しやすい。

## Consequences

- エージェントは巨大な生 workflow JSON を直接読む必要がなくなる。
- UI情報と処理情報を分離できる。
- 出力 schema の設計と後方互換性が重要になる。
- ComfyUI / custom node 側の仕様差分は best-effort で吸収し、未知部分は warnings に残す必要がある。
