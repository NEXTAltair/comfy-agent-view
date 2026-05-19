# ADR 0005: Path Security and Runtime Boundaries

- **日付**: 2026-05-19
- **ステータス**: Accepted

## Context

`comfy-agent-view` はローカルファイル読み取り tool である。MCP や wrapper 経由で使う場合、任意パス読み取りや意図しない書き込みを避ける必要がある。

また、Windows native の ComfyUI、WSL 上の OpenClaw / Hermes、Windows native の Claude Desktop / Codex が混在する可能性がある。

## Decision

core 側に最低限の path / security 制約を持たせる。

恒久設定は `comfy-agent-view` 自身の user config に保存する。OpenClaw の `TOOLS.md`、Claude Desktop の設定、Codex の MCP 設定など、各エージェント固有の設定ファイルは launcher / 接続情報だけを持ち、workflow 読み取り範囲の単一ソースにはしない。

user config の既定パス:

```text
Linux / WSL:
  $XDG_CONFIG_HOME/comfy-agent-view/config.toml
  or ~/.config/comfy-agent-view/config.toml

Windows:
  %APPDATA%\comfy-agent-view\config.toml

macOS:
  ~/Library/Application Support/comfy-agent-view/config.toml
```

`object_info` cache の既定保存先は、user config と同じディレクトリの `object_info.json` とする。

```text
Linux / WSL:
  $XDG_CONFIG_HOME/comfy-agent-view/object_info.json
  or ~/.config/comfy-agent-view/object_info.json

Windows:
  %APPDATA%\comfy-agent-view\object_info.json

macOS:
  ~/Library/Application Support/comfy-agent-view/object_info.json
```

設定ファイルの場所は `COMFY_AGENT_VIEW_CONFIG` で明示上書きできる。ただしこれは設定ファイルの場所を変える逃げ道であり、読み取り対象ディレクトリの値そのものを上書きする経路ではない。

```toml
[comfy_agent_view]
comfyui_user_dir = "H:\\StabilityMatrix-win-x64\\Data\\Packages\\ComfyUI\\user"
default_profile = "safe"
allow_full_profile = true
```

設定の実効順序:

1. CLI `--comfyui-user-dir`（その起動だけの明示指定）
2. user config の `comfyui_user_dir`
3. 未設定なら推測せずエラー

MVP では `COMFY_AGENT_VIEW_ALLOWED_ROOTS` のような値本体の env override は持たない。CI や検証では一時 config file を作る。

workflow 操作の既定 root は `comfyui_user_dir` 直下ではなく、ComfyUI の通常配置である `<comfyui_user_dir>/default/workflows` とする。ComfyUI には workflow ディレクトリを通常設定で変更するオプションがなく、多くの利用環境ではこのパスが実質的な既定保存先になる。

ただし明示された workflow path / root は、`comfyui_user_dir` 配下であれば許可する。これは将来の診断や手動配置済みファイルを扱うためであり、`list` の無指定実行で `comfyui.db`、logs、Manager cache、settings まで走査しないための境界とは分ける。

Stability Matrix などの環境では `<comfyui_user_dir>/default/workflows` が symlink / junction で user ディレクトリ外の実体を指すことがある。この場合は、その default workflow ディレクトリの実体だけを許可 root に加える。汎用 `allowed_roots` は復活させない。

制約:

- 読み取り範囲は `comfyui_user_dir` 配下に限定する
- 設定項目は汎用 `allowed_roots` ではなく、ComfyUI user ディレクトリを指す `comfyui_user_dir` とする
- workflow list の無指定実行は `<comfyui_user_dir>/default/workflows` を既定 root とする
- `<comfyui_user_dir>/default/workflows` が symlink / junction の場合は、その実体だけを workflow root として許可する
- `/object_info` は ComfyUI の固定ファイルではないため、tool 側の既定 cache path に保存する
- object_info cache は workflow 読み取り境界とは別の tool-owned metadata として扱う
- `comfyui.db` は MVP では読み取らない
- asset DB / environment DB の診断は将来検討であり、現時点では設計対象外
- ComfyUI `input` / `output` / `models` など user 外の領域は MVP では扱わず、必要になった時点で個別の明示設定として追加する
- default profile は `safe`
- repair は `dry_run=True` が既定
- `output_path` も `comfyui_user_dir` 配下に限定する
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
- **汎用 `allowed_roots` 案（却下）**: MVP では用途が ComfyUI user ディレクトリに閉じている。抽象的な許可ディレクトリ一覧にすると、利用者に不要な設計判断を押しつける。
- **`comfyui_user_dir` + dry-run default 案（採用）**: 実用性を保ちながら、読み取り範囲と修復書き込みを用途名で明示できる。
- **workflow ディレクトリだけ許可する案（却下）**: workflow JSON の構造理解には足りるが、ロード失敗や custom node 互換崩れの診断では logs が必要になるため user ディレクトリを許可する。
- **`list` の既定 root を `comfyui_user_dir` 直下にする案（却下）**: `comfyui.db`、logs、settings、Manager cache など workflow ではない JSON まで混ざる。既定探索は `<comfyui_user_dir>/default/workflows` に限定し、必要な時だけ明示 root を渡す。
- **symlink / junction を一切たどらない案（却下）**: Windows / Stability Matrix 環境では default workflow ディレクトリ自体が外部実体へのリンクになり得る。ComfyUI の通常保存先として見えているものは扱える必要がある。
- **MVP で `comfyui.db` を読む案（却下）**: SQLite 読み取り実装、schema 追従、DB lock 配慮が増える。workflow graph 正規化と link 修復の主材料ではないため、必要になった時に追加する。
- **input / output / models も許可する案（MVP では却下）**: 現在の目的である workflow 構造理解と link 修復への寄与が薄い。将来、画像メタデータ内 workflow 抽出や model inventory を扱う時に再検討する。
- **OpenClaw `TOOLS.md` に保存する案（却下）**: OpenClaw からは便利だが、Claude Desktop、Codex、Hermes など他エージェントと共有できない。
- **各エージェント設定に重複保存する案（却下）**: 設定が分散し、許可範囲の更新漏れが起きる。
- **値本体を env override する案（MVP では却下）**: 設定経路が増え、優先順位の説明が必要になる。CI では一時 config file で代替できる。
- **tool 自身の user config に保存する案（採用）**: 読み取り範囲を tool の責務として保持でき、各エージェントは同じ設定を参照できる。
- **object_info path を毎回指定させる案（却下）**: cache は tool が生成・消費する環境スナップショットであり、通常運用で利用者に path 管理を押しつける必要がない。
- **object_info を tool 既定 cache に保存する案（採用）**: ComfyUI 起動中 API から取得した schema を offline normalize で再利用できる。

## Consequences

- 利用者は user config に恒久的な `comfyui_user_dir` を保存する。
- workflow list の無指定実行は `<comfyui_user_dir>/default/workflows` を見る。
- symlink / junction 環境では、default workflow ディレクトリの実体 path が result root として返ることがある。
- object_info cache は user config と同じディレクトリの `object_info.json` に保存される。
- CLI の `--comfyui-user-dir` は一時的な上書きとして使う。
- `COMFY_AGENT_VIEW_CONFIG` は config file の場所だけを切り替える。
- 修復系 tool は `dry_run=True` を既定にするが、明示的に `dry_run=false` と `output_path` が指定された場合は書き込める。
- Windows / WSL の path 差分は core または adapter 層で吸収する。
- workflow 実行、画像生成 queue 投入、任意コマンド実行は MVP の責務外とする。
