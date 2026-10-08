# リポジトリテンプレート

自分用の Python・Rust・Chrome Extension・Tauri・Docker project を生成・更新する Copier template。最新版の構成だけをサポートします。

## 導入と更新

```sh
copier copy git@github.com:mizucopo/repo-template.git <destination>
```

既存の Copier project は、clean な専用 branch で最新版を適用します。

```sh
copier update --trust --defaults --vcs-ref HEAD
```

Copier の three-way merge により、template と project の変更を取り込みます。差分・競合を確認し、品質検証後に通常の PR で merge します。回答だけを変更する場合は `--vcs-ref=:current:` と `-d OPTION=VALUE` を使います。`copier recopy` は通常の更新には使いません。

Copier 未導入の既存 project は、`copier copy --trust --overwrite --pretend SOURCE .` で差分を確認してから `--pretend` を外し、製品固有の振る舞いを標準 layout に移します。初回適用で既存 source を除外する運用はしません。

## 構成

すべての回答・既定値・入力条件は [copier.yml](copier.yml) を正本とします。

| 選択 | 主な生成内容 | 品質コマンド |
| --- | --- | --- |
| `use_python` | pyproject、src、pytest、Ruff、mypy | `uv run task check` |
| `use_rust` | Cargo、Rust toolchain、src | rustfmt・Clippy・Cargo test |
| `use_chrome_extension` | Manifest V3、TypeScript、Vitest、dist build | `npm run check` |
| `use_tauri` | frontend と src-tauri、3 platform の任意配布 | `npm run check` |
| `use_docker` | deny-all と言語別入力の dockerignore | 任意の build・smoke check |

Python `application` は src 直下の module を直接実行し、project 自身を install しません。`package/library` は import package と build system を生成します。Docker でも同じ layout と install 方針を使います。

Docker の通常 build 入力は Rust/Python の回答に応じて許可します。test・fixture・独自 build script・workspace 等は生成先 `.dockerignore` へ明示追加し、Copier 更新時に競合を確認します。追加例と runtime image との境界は生成先の **docs/docker-build-context.md** を参照してください。

Tauri と root Rust、Tauri と Chrome Extension は同時に選べません。Tauri の表示名と package 名は別で、package 名の既定値は `project_name` です。icon は初回生成後に project が所有し、更新時も変更・削除を保持します。

`use_version_management` は package/manifest version の管理を選びます。Python・Rust・Chrome Extension・Tauri には必須です。version の必要がない文書・設定 repository では無効にできます。version 管理と公開は別の選択です。

## 公開

| 回答 | 公開内容 |
| --- | --- |
| `use_gh_actions_release` | Git tag と GitHub Release |
| `use_gh_actions_docker_release` | Docker Hub/ECR image と Release |
| `use_gh_actions_docker_project_pipeline` | project hook による単一・複数 Docker image |
| `use_gh_actions_chrome_extension_release` | Chrome Extension distribution ZIP |
| `use_gh_actions_tauri_build` | Windows x64/ARM64・Mac ARM64 の Tauri ZIP |

公開方式は一つ選びます。任意の `use_gh_actions_tauri_homebrew_notify` は、安定版公開後に別 repository の Tap を通知します。Tap 側が配布物を検証し、Cask を更新します。

通常の PR CI → squash merge → 最新のマージ済み PR の分類で最新 main を採番 → 採番 commit と同じタグ → 同じ Actions run 内で検証・公開、という流れです。人間・AI 共通の分類基準、公開の選択、ラベルを変更できない投稿者の手順は、生成先の **CONTRIBUTING.md** に集約します。AGENTS.md と PR テンプレートから同じ文書を参照します。

生成先の **docs/release.md** が設定・採番・復旧手順の正本です。Docker hook は **docs/docker-project-pipeline.md**、Tap 通知は **docs/homebrew-tap-notification.md** を参照します。旧マージ準備・署名・二段階 bootstrap は生成しません。

## CI と作業指示

PR 品質 CI は read-only・secret なしで、project の品質コマンドを実行します。利用する技術に対応する native job は `quality-checks`、`rust-quality-checks`、`chrome-extension-quality-checks`、`tauri-quality-checks`、`docker-quality-checks`、`docker-project-quality-checks` です。公開する project には `release-classification` も生成します。

採番には標準 `GITHUB_TOKEN` の main・タグへの直接 push を許す repository 設定が必要です。PR 必須・必須チェック等によって直接 push が拒否される設定では公開を停止します。workflow が保護設定を変更したり、別の認証へ切り替えたりすることはありません。

共通作業指示は AGENTS.md、project 固有の追記は空の `.codex/project.md`、言語別指示は `.codex/languages/` に置きます。参照と優先順位は AGENTS.md に記載します。CLAUDE.md は生成しません。

Dependabot は選択した runtime、Docker と GitHub Actions の監視回答から週次更新を生成します。自動分類・自動承認・自動 merge は行いません。

## テンプレートの検証

```sh
python3 -m unittest discover -s template_tests -p 'test_*.py'
```

CI は Copier 9.17.1 と actionlint を使い、生成・更新・実行動作を検証します。Actions の参照は full commit SHA に固定します。
