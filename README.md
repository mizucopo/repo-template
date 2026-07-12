# リポジトリテンプレート

新しいリポジトリをセットアップするためのシンプルなcopierテンプレート。

## 使い方

```bash
copier copy git@github.com:mizucopo/repo-template.git <destination>
```

## テンプレートの更新

このテンプレートの最新変更をプロジェクトに適用するには：

```bash
copier update
```

これにより、プロジェクト固有のカスタマイズを保持しながら、最新のテンプレート変更がマージされます。

既存プロジェクトで `.copier-answers.yml` の回答だけを変更し、同じテンプレートのバージョンに再適用する場合は `copier update` ではなく `copier recopy` を使用します。

```bash
copier recopy -f
```

例えば `use_gh_actions_release` を `false` から `true` に変更したあと、`.github/workflows/release.yml` を生成したい場合は `copier recopy -f` を実行してください。

## オプション

- `use_python`: Python関連ファイルを生成するか
- `use_rust`: Rust関連ファイルを生成するか
- `use_chrome_extension`: Chrome Extension関連ファイルを生成するか
- `use_tauri`: Tauri関連ファイルを生成するか
- `use_gh_actions_docker_release`: .github/workflows/docker-release.ymlを生成するか
- `use_gh_actions_release`: .github/workflows/release.ymlを生成するか（`use_gh_actions_docker_release`が有効な場合は無視される）
- `use_gh_actions_chrome_extension_release`: Chrome Extension配布zip用の.github/workflows/chrome-extension-release.ymlを生成するか
- `chrome_extension_release_package_root_directory`: Chrome Extension配布release workflowが`npm ci`、quality gate、buildを実行するpackage root directory
- `chrome_extension_release_zip_name`: GitHub Releaseへ添付するChrome Extension配布zip名（`{version}`を`package.json`のversionに置換、path separatorと`#`は不可）
- `chrome_extension_release_title`: Chrome Extension配布用GitHub Release title（`{version}`をversionに置換）
- `chrome_extension_release_notes`: Chrome Extension配布用GitHub Release notes（`{version}`をversionに置換）
- `use_gh_actions_pr_tag_check`: .github/workflows/pr-tag-check.ymlを生成するか

`use_chrome_extension=true` は、新規・既存プロジェクトのどちらにも同じManifest V3 TypeScript標準構成を適用します。`package.json`, `src/`, `tests/`, TypeScript/Vitest/ESLint/Prettier設定、build script、Chrome Extension quality workflowを一つのruntime supportとして生成します。導入先固有の構成を温存する適用モードはありません。

### 既存Chrome Extensionを標準構成へ移行する

既存プロジェクトでは、Gitの専用ブランチを復元点として使い、テンプレートが管理する構成へ明示的に移行します。最初に作業ツリーがcleanであることを確認し、専用ブランチを作成してください。

```bash
git status --short
git switch -c feature/standardize-chrome-extension
```

次に `--pretend` と `--overwrite` を併用し、ファイルを変更せずに適用予定を確認します。

```bash
copier copy --trust --defaults --overwrite --pretend \
  -d use_chrome_extension=true \
  git@github.com:mizucopo/repo-template.git .
```

適用予定に問題がなければ、同じコマンドから `--pretend` を外して標準構成を適用します。`--overwrite` は既存ファイルを置き換える意図を明示するため、省略しないでください。

```bash
copier copy --trust --defaults --overwrite \
  -d use_chrome_extension=true \
  git@github.com:mizucopo/repo-template.git .
```

適用後は `git diff` で全差分を確認し、既存のプロジェクト固有ロジックだけを標準の `src/` と `tests/` へ移植します。root `manifest.json`、旧build設定、旧workflowなど標準構成と競合する未管理ファイルは、動作を移植してから削除してください。最後に依存関係とquality gateを実行し、生成した `package-lock.json` を含めてレビューします。

```bash
git diff
npm install
npm run check
git status --short
```

既に `.copier-answers.yml` を作成済みの場合、同じテンプレートversionと回答を再適用するには `copier recopy -f`、新しいテンプレートversionを取り込むには `copier update` を使います。どちらも専用ブランチのcleanな作業ツリーで実行し、適用後に差分と `npm run check` を確認してください。

旧 `chrome_extension_mode`、`adopt_existing`、`javascript_rollup`、`chrome_extension_manifest_path` は廃止しました。古い `.copier-answers.yml` にこれらの回答が残っている場合も、再適用後は削除され、標準構成が生成されます。既存実装を温存する更新経路はないため、上記の手順でプロジェクト固有の動作を標準構成へ移してください。

`use_tauri` は専用の `src-tauri` と Node.js フロントエンドを生成するため、`use_rust` と `use_chrome_extension` とは同時に利用できません。

## 生成される主なファイル

Copierの回答に応じて、以下のようなファイルが生成されます。条件の詳細はオプションの組み合わせで決まるため、ここでは生成後に確認する主なファイルの役割を説明します。

### 共通ファイル

- `.copier-answers.yml`: Copierの回答を記録するファイル。`copier update` や `copier recopy` はこの内容をもとにテンプレートを再適用します。
- `.gitignore`: 選択したruntime supportに応じて、生成物やlocal環境ファイルをGit管理から除外します。
- `AGENTS.md`, `CLAUDE.md`: 生成先リポジトリで作業するエージェント向けの共通ルールと、選択したruntime supportごとの品質確認手順をまとめます。
- `LICENSE`: MITライセンスを選択した場合に生成されます。
- `version`: Python、Rust、Chrome Extension、Tauriのruntime supportを使わない場合に、release workflowのversion sourceとして生成されます。

### Python関連ファイル

- `.python-version`: 生成先リポジトリで使うPythonバージョンを固定します。
- `pyproject.toml`: Pythonプロジェクトのメタデータ、依存関係、ruff、mypy、pytestなどの設定をまとめます。
- `src/`, `stubs/`, `tests/`: Python実装、型スタブ、テストの初期ディレクトリです。
- `.github/workflows/pr-quality-checks.yml`: pull requestでpytest、mypy、ruffを実行し、結果をGitHub Checksに公開します。

### Rust関連ファイル

- `Cargo.toml`: root Rust runtime supportのCargo package定義です。
- `rust-toolchain.toml`: Rust toolchainを固定し、local環境とCIの差を抑えます。
- `src/main.rs`: root Rust runtime supportの最小実行ファイルです。
- `.github/workflows/rust-quality-checks.yml`: `cargo fmt`、Clippy、Cargo testを実行するquality gateです。

### Chrome Extension関連ファイル

- `.node-version`: Chrome ExtensionまたはTauriで使うNode.jsバージョンを固定します。
- `package.json`: TypeScript build、Vitest、ESLint、Prettier scriptなどをまとめます。
- `src/manifest.json`, `src/background.ts`, `src/popup.html`, `src/popup.ts`, `src/popup.css`: Manifest V3 Chrome Extensionの標準実装です。
- `src/lib/`, `tests/`: 再利用するTypeScriptロジックとVitestテストを置く初期ディレクトリです。
- `scripts/copy-extension-assets.mjs`, `scripts/clean-dist.mjs`: Chrome拡張のbuild outputを整える補助scriptです。
- `tsconfig.json`, `tsconfig.build.json`, `eslint.config.mjs`, `vitest.config.ts`, `.prettierrc.json`, `.prettierignore`: TypeScript、lint、test、formatの設定です。
- `.github/workflows/chrome-extension-quality-checks.yml`: lint、format、typecheck、Vitest、buildを実行するquality gateです。

### Tauri関連ファイル

- `package.json`, `.node-version`, `index.html`, `vite.config.ts`: Tauri frontendのNode.js/Vite設定とentrypointです。
- `src/main.ts`, `src/styles.css`, `src/lib/greeting.ts`, `tests/lib/greeting.test.ts`: TypeScript frontendのstarter実装とテストです。
- `src-tauri/Cargo.toml`, `src-tauri/src/`, `src-tauri/tauri.conf.json`, `src-tauri/capabilities/default.json`, `src-tauri/build.rs`: Tauri application shell、Rust code、権限、bundle設定をまとめます。
- `src-tauri/icons/`: Tauri bundleで使う初期iconです。
- `rust-toolchain.toml`: Tauri側のRust toolchainを固定します。
- `.github/workflows/tauri-quality-checks.yml`: frontendのlint、format、typecheck、test、buildと、Rust側のrustfmt、Clippy、Cargo testを実行するquality gateです。

### Release関連ファイル

- `.github/workflows/release.yml`: version sourceを読み、git tagとGitHub Releaseを作成します。
- `.github/workflows/chrome-extension-release.yml`: Chrome Extension配布zipを作成し、git tagとGitHub Releaseに添付します。
- `.github/workflows/docker-release.yml`: Docker imageをbuild/pushし、git tagとGitHub Releaseを作成します。
- `.github/workflows/pr-tag-check.yml`: pull request上でversion sourceの値が既存tagと衝突しないか確認します。

`use_gh_actions_chrome_extension_release=true` はChrome Extension runtime support専用の配布release workflowです。write権限でtagとGitHub Releaseを作成できるように `main` へのpushで起動し、checkoutしたcommitが `main` 向けにmerge済みのpull request由来であることを検証します。そのうえで `package.json` とChrome manifestのversion一致、Chrome manifest version形式、既存tagが別commitを指していないことを確認し、`npm ci`、生成先プロジェクトの `npm run check`、`npm run build`、配布zip作成、tag作成、GitHub Release作成、zip uploadまでを実行します。build後に `dist/manifest.json` がある場合は `dist` を配布zipのrootにし、ない場合は設定されたChrome manifestがあるdirectoryを配布zipのrootにします。実際にzipする `manifest.json` のversionもrelease直前に再検証します。再実行時に同じmerged PR commitのtagやGitHub Releaseが既に存在する場合はそれらを再利用し、zip uploadは同名assetを上書きします。

Chrome Extension配布release workflowを使う生成先プロジェクトでは、`chrome_extension_release_package_root_directory` に `package.json` があるdirectoryを指定してください。Node.js versionはテンプレートがrepository rootに生成する `.node-version` を使います。workflowはlockfileを前提に `npm ci` を実行するため、生成先プロジェクトでは `package-lock.json` をcommitしておく必要があります。配布zip名、GitHub Release title、release notesはtemplate answersから生成され、`{version}` placeholderはrelease時の `package.json` versionに置換されます。`use_gh_actions_pr_tag_check=true` も併用する場合、Chrome ExtensionのPR tag checkは同じpackage root directoryの `package.json` をversion sourceとして検証します。

既存の `use_gh_actions_release=true` はversion sourceからtagとGitHub Releaseだけを作成する汎用release workflowです。Chrome Extensionの配布zipをRelease assetとして添付したい場合は `use_gh_actions_chrome_extension_release=true` を使い、tag-onlyの汎用releaseが必要な場合だけ `use_gh_actions_release=true` を使ってください。同じversion tagを作成するため、Chrome Extension配布release workflowは `use_gh_actions_release=true` や `use_gh_actions_docker_release=true` と同時に有効化できません。

Chrome Extensionを使う場合、release workflowのversion sourceは `package.json` の `version` です。PR上の `.github/workflows/pr-tag-check.yml` は、`package.json` と `src/manifest.json` の `version` を両方読み、Chrome manifest version形式と両者の一致をmerge前に検証します。不一致や不正なmanifest versionは、tag確認前に明確な失敗checkとして表示され、workflowも失敗します。

## ライセンス

詳細は[LICENSE](LICENSE)を参照してください。
