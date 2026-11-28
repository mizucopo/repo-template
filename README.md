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

Python の共通品質設定はテンプレートで管理し、依存・公開版・配布設定・lockfile・coverage 等はプロジェクト側に残します。application の `python_mypy_import_root` は `src`（既定の平坦な import）または `repository`（`src.*`）を選択します。過去の dev stub 依存が不要な新規プロジェクトは `python_include_legacy_stub_dependencies=false` を選べます。更新手順と設定の境界は、生成先の **.codex/languages/python.md** を参照してください。

Docker の通常 build 入力は Rust/Python の回答に応じて許可します。root と Docker quality の `docker_build_context` 直下に `.dockerignore` を生成します。test・fixture・独自 build script・workspace 等は使用する context の policy へ明示追加し、Copier 更新時に競合を確認します。配置・優先順位・追加例と runtime image との境界は生成先の **docs/docker-build-context.md** を参照してください。

Docker PR 品質確認は既定で従来どおり Ubuntu 単一 job を生成します。任意の platform matrix、test stage、オフライン smoke、製品固有 hook は生成先の **docs/docker-build-context.md** を参照してください。

Tauri と root Rust、Tauri と Chrome Extension は同時に選べません。Tauri の表示名 `tauri_product_name` と package 名 `tauri_package_name` は別です。package 名の既定値は `project_name` で、回答で明示的に変更できます。icon は初回生成後に project が所有し、更新時も変更・削除を保持します。

`use_version_management` は package/manifest version の管理を選びます。Python・Rust・Chrome Extension・Tauri には必須です。version の必要がない文書・設定 repository では無効にできます。version 管理と公開は別の選択です。

## 公開

| 回答 | 公開内容 |
| --- | --- |
| `use_gh_actions_release` | Git tag と GitHub Release |
| `use_gh_actions_docker_release` | Docker Hub/ECR image と Release |
| `use_gh_actions_docker_project_pipeline` | project hook による単一・複数 Docker image |
| `use_gh_actions_chrome_extension_release` | Chrome Extension distribution ZIP |
| `use_gh_actions_tauri_build` | Windows x64/ARM64・Mac ARM64 の Tauri ZIP |

`use_rust` の通常 Release / Docker Release は `rust_release_targets` で Linux x64（既定）・Windows x64/ARM64・macOS ARM64 の CLI バイナリを選択できます。native runner で archive の展開・起動と SHA-256 を検証してから公開します。製品固有の同梱 hook と Copier 更新手順は生成先の **docs/release.md** を参照します。

公開方式は一つ選びます。任意の `use_gh_actions_tauri_homebrew_notify` は、安定版公開後に別 repository の Tap を通知します。Tap 側が配布物を検証し、Cask を更新します。

通常の PR CI → squash merge → 方式に応じて最新 main を採番 → 採番 commit と同じタグ → 同じ Actions run 内で検証・公開、という流れです。upstream-revision は既定で文書のみを含む新しい squash-merged PR があれば、ラベルなしで現在の main 全体を公開します。既存 worker の入力限定・手動指定の互換構成は **docs/docker-project-pipeline.md** を参照します。SemVer・Chrome は最新のマージ済み PR の分類を使います。人間・AI 共通の方式別公開方針、分類基準、公開の選択、ラベルを変更できない投稿者の手順は、生成先の **CONTRIBUTING.md** に集約します。AGENTS.md と PR テンプレートから同じ文書を参照します。

生成先の **docs/release.md** が設定・採番・復旧手順の正本です。Docker hook は **docs/docker-project-pipeline.md**、Tap 通知は **docs/homebrew-tap-notification.md** を参照します。旧マージ準備・署名・二段階 bootstrap は生成しません。

## CI と作業指示

PR 品質 CI は read-only・secret なしで、project の品質コマンドを実行します。利用する技術に対応する native job は `quality-checks`、`rust-quality-checks`、`chrome-extension-quality-checks`、`tauri-quality-checks`、`docker-quality-checks`、`docker-project-quality-checks` です。公開する project には `release-classification` も生成します。upstream-revision では分類ラベルの検証をスキップします。

`use_gh_actions_native_pr` は初期値OFFです。既存の Linux 品質ゲートを置き換えず、Rust CLI の native 配布・スモークと Tauri の Windows/macOS 回帰を PR に追加します。対象・hook・移行手順は生成先の **docs/native-pr-checks.md** を参照してください。

採番には標準 `GITHUB_TOKEN` の main・タグへの直接 push を許す repository 設定が必要です。PR 必須・必須チェック等によって直接 push が拒否される設定では公開を停止します。workflow が保護設定を変更したり、別の認証へ切り替えたりすることはありません。

共通作業指示は AGENTS.md、project 固有の追記は空の `.codex/project.md`、言語別指示は `.codex/languages/` に置きます。参照と優先順位は AGENTS.md に記載します。CLAUDE.md は生成しません。

Dependabot は選択した runtime、Docker と GitHub Actions の監視回答から週次更新を生成します。自動分類・自動承認・自動 merge は行いません。

`use_gh_actions_codeql` は CodeQL Advanced setup の任意生成を選びます（初期値は無効）。`codeql_languages` に製品と補助 script の全解析言語を明示し、標準 Ubuntu runner で PR・main push・週次解析を行います。解析 job だけに `security-events: write` を付与し、結果を upload します。生成先の **docs/codeql.md** が設定と Default setup からの移行手順を説明します。Copier は GitHub の設定を変更しません。

## 任意のセキュリティ検査

`use_gh_actions_bandit` と `use_gh_actions_cargo_audit` は初期値OFFの独立した選択です。前者は `use_python`、後者は `use_rust` または `use_tauri` と併用します。両方を選べば、一つの **Security Checks** workflow 内で独立したjobを生成します。

- Bandit は `src` と `.github/scripts` のPythonソースを検査します。スキャン対象を実行せず、製品の依存もinstallしません。検出だけでなく、構文エラー・読取り失敗・解析器の内部エラーも失敗として扱います。
- cargo-audit はコミット済みの `Cargo.lock`（Tauriは `src-tauri/Cargo.lock`）をRustSecの既知アドバイザリと照合します。PRでブロック対象を検出したときは、同じ条件でベースと比較して既存・新規・未確認を表示します。

両方の検査はPR・main push・手動実行に対応します。既定ブランチの定期検査はBanditが週次、cargo-auditが日次です。日次のRust監査でブロック対象を検出すると、通知jobが `issues: write` 権限でリポジトリのIssuesに通知します。検査jobは `contents: read` で動作し、追加Secretは不要です。

有効化、結果確認、狭い例外の管理、scanner版の更新、Cargo.lockの準備は生成先の **docs/security-checks.md** を参照してください。通常のCopier更新だけで既存projectの検査を有効にしません。

## テンプレートの検証

```sh
python3 -m unittest discover -s template_tests -p 'test_*.py'
```

CI は Copier 9.17.1、固定版のactionlint・Bandit・cargo-auditを使い、生成・更新・実行動作を検証します。scannerの回帰検証には公開fixtureだけを使い、実サービス・実認証情報を使いません。Actions の参照は full commit SHA に固定します。

生成ワークフローの外部 Action は [`_action_pins.yml.jinja`](_action_pins.yml.jinja) から、公式タグに対応する完全 SHA とバージョン注記を出力します。採用先の Dependabot などが先に更新した場合、公式タグと互換性を確認し、上流の pin を先に更新してください。採用先のより新しい pin を機械的に戻したり、Dependabot を一律に無効化したりしません。テンプレート自身の CI は Jinja を使わないため直接 SHA を書き、同じ pin であることをテストします。

正式版の actionlint が `concurrency.queue` を解釈できるようになるまでは、queue 対応の [上流 PR #654](https://github.com/rhysd/actionlint/pull/654) の固定 commit を使用します。Git と Go 1.25 以降を用意し、リポジトリのルートで次を実行します。ダウンロードとビルドが成功した場合にだけローカルのバイナリを更新し、グローバルの actionlint は変更しません。

```sh
bash .github/scripts/install-actionlint.sh
export PATH="$PWD/.tools/bin:$PATH"
```

生成先でワークフローを検証する場合も同じスクリプトを使えます。queue の診断を無視せず、queue 対応が正式版に入った時点で上流の installer と検証を切り替えます。

ローカル Docker builder が利用できる場合は、公開テスト文字列だけの fixture で root・subdirectory の実入力除外も検証できます。必要なら `DOCKER_CONTEXT` で builder を選択します。外部 image の取得・push は行いません。

```sh
REPO_TEMPLATE_DOCKER_TESTS=1 python3 -m unittest discover -s template_tests -p 'test_template.py' -k filters_real_build_inputs
```

### 共通設定の逸脱を検出する

`use_repo_template_policy=true` を指定すると、PR に `repo-template-policy` チェックが生成されます。
標準の生成ワークフローとCI補助スクリプト、ツールチェーンと、pyproject.toml／Cargo.toml／package.json の一部の共通キーを、正本の main から回答と固定リビジョンで再生成して照合します。通常の Copier 更新で生成結果と一致する変更は通過します。既存の差分を後から自動で消さず、新しく変更を加える箇所だけ検査します。

- 製品のソース、依存関係、ロックファイル、バージョン・識別子、テストや公開の固有データは引き続き採用側で管理します。
- 製品固有の workflow は `.github/repo-template-policy.json` の `local_workflows` に正確なパスを記載します。共通キーの例外は `local_managed_keys` に `pyproject.toml:tool.ruff.line-length` のような正確なキーを記載します。生成された workflow 自体は例外にできません。
- 例外を使う前に、その理由を明記した別 PR でポリシーをレビュー・マージします。同じ PR に例外を追加しても、その PR の検証ルールは変わりません。最初の導入 PR ではポリシーと既存差分を人が確認します。
- 判定はベース側チェックコードで行い、PR 側のスクリプト・Copier タスクは実行しません。これは通常の PR チェックであり、単独では管理者権限や改変されたワークフローに対する強制境界ではありません。マージを必ず止める運用は、レビューで保護されたベースと、権限を持つ管理者が設定した必須チェック／Ruleset が別途必要です。このテンプレートは GitHub 保護設定を変更しません。
