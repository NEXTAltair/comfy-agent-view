# ADR 0003: Profile Redaction Policy

- **日付**: 2026-05-19
- **ステータス**: Accepted

## Context

ComfyUI workflow には過去の生成 prompt が残る。機密ではなくても、NSFW prompt や個人的に見られたくない prompt が含まれることがある。

一方で、prompt 調整や prompt 移植では本文が必要になる。常に隠すだけでは実用性が落ち、常に出すと不用意な露出になる。

## Decision

出力 profile を `safe`、`private`、`full`、`debug` に分ける。

`safe` は既定値とする。ノード種別、接続関係、checkpoint / LoRA / VAE / ControlNet 名、seed、steps、cfg、sampler、scheduler、denoise、width / height、prompt の存在、prompt の推定 token 数を返す。positive / negative prompt 本文とメモノード本文は隠す。

`private` は人に見せる可能性があるログ向けとする。prompt 本文に加え、checkpoint / LoRA / VAE / ControlNet 名、`filename_prefix`、参照画像パスも隠す。

`full` はユーザーが明示した時だけ使う。prompt 本文、negative prompt 本文、メモノード本文、`filename_prefix` を返す。

`debug` は壊れた workflow 調査用とする。raw link id、`origin_slot`、`target_slot`、raw inputs / outputs / widgets_values、unknown widgets、repair candidates を返す。

## Rationale

- **常に prompt 本文を出す案（却下）**: エージェントログや共有出力に不用意に prompt が露出する。
- **常に prompt 本文を隠す案（却下）**: prompt 調整、prompt 移植、prompt 差分比較に使えない。
- **profile 分岐案（採用）**: 既定は安全にし、必要時だけ明示的に露出範囲を広げられる。

## Consequences

- 既定 profile は `safe` とする。
- `full` はユーザーが prompt 本文の確認・編集を明示した場合のみ使う。
- `debug` は workflow 修復や互換性調査の場合のみ使う。
- 出力 schema は profile によって detail が変わるため、各 tool は `profile` を返却結果に含める。
