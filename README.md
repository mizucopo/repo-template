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
- `chrome_extension_mode`: Chrome Extensionの適用モード（`scaffold` または `adopt_existing`）
- `use_gh_actions_chrome_extension_release`: Chrome Extension配布zipをGitHub Releaseへ添付するworkflowを生成するか
- `chrome_extension_release_package_root_directory`: 配布zipに含めるpackage root directory
- `chrome_extension_release_zip_name`: 配布zip名（`${version}` をrelease versionに置換）
- `chrome_extension_release_title`: GitHub Release title（`${version}` をrelease versionに置換）
- `chrome_extension_release_notes`: GitHub Release notes（`${version}` をrelease versionに置換）
- `use_tauri`: Tauri関連ファイルを生成するか
- `use_gh_actions_docker_release`: .github/workflows/docker-release.ymlを生成するか
- `use_gh_actions_release`: .github/workflows/release.ymlを生成するか（`use_gh_actions_docker_release` または `use_gh_actions_chrome_extension_release` が有効な場合は無視される）
- `use_gh_actions_pr_tag_check`: .github/workflows/pr-tag-check.ymlを生成するか

`chrome_extension_mode=scaffold` は新規Manifest V3 Chrome拡張向けに、`package.json`, `src/`, `tests/`, TypeScript/Vitest/ESLint設定、Chrome拡張品質チェックworkflowを生成します。

`use_gh_actions_chrome_extension_release=true` は scaffold されたChrome拡張向けに、`main` へmergeされたpull requestの `merge_commit_sha` から配布zipを作り、同じversionのgit tagとGitHub Releaseを作成または再利用します。workflowは `pull_request_target` のmerged PRだけで実行され、`package.json` と `src/manifest.json` のversion一致、Chrome manifest version形式、既存tagが同じmerge result commitを指していることを検証し、lockfileがある場合は `npm ci`、ない場合は `npm install`、続いて `npm run check`, `npm run build` を実行してからzipを作成します。

汎用の `use_gh_actions_release` はversion sourceからtagとGitHub Releaseだけを作る用途です。Chrome Web Storeや手動配布に渡すzip artifactが必要なChrome拡張では、汎用release workflowではなく `use_gh_actions_chrome_extension_release` を使ってください。

`chrome_extension_mode=adopt_existing` は既存JavaScript Chrome拡張向けに、starter TypeScript拡張ファイルを生成しません。既存の `package.json`, `src/`, `test` / `tests`, `.github/workflows/release.yml` をテンプレートで置き換えず、`.node-version`, `AGENTS.md`, `CLAUDE.md`, licenseなどの共通メタデータだけを取り込む用途で使用してください。

### 既存Chrome拡張を初めてadopt_existing登録する

未登録の既存プロジェクトには `.copier-answers.yml` がないため、最初から `copier update` は実行できません。まず既存リポジトリのrootで `copier copy` を使い、adoption modeの回答を記録してください。

```bash
copier copy --trust --defaults \
  -d use_chrome_extension=true \
  -d chrome_extension_mode=adopt_existing \
  git@github.com:mizucopo/repo-template.git .
```

既に `.copier-answers.yml` を作成済みで、回答だけを同じテンプレートバージョンに再適用する場合は `copier update` ではなく `copier recopy -f` を使います。

```bash
copier recopy -f
```

実行後は、既存の `package.json`, `src/`, `test` / `tests`, root `manifest.json`, `options.html`, `rollup.config.mjs`, `vitest.config.js`, `.github/workflows/release.yml` がテンプレートに置き換えられていないことを確認してください。`adopt_existing` で新しく管理する主なファイルは `.copier-answers.yml`, `.node-version`, `AGENTS.md`, `CLAUDE.md`, licenseなどの共通メタデータです。

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
- `package.json`: TypeScript、Vitest、ESLint、Prettier、build scriptなどをまとめます。
- `src/manifest.json`, `src/background.ts`, `src/popup.html`, `src/popup.ts`, `src/popup.css`: Manifest V3 Chrome拡張のstarter実装です。
- `src/lib/`, `tests/`: 再利用するTypeScriptロジックとVitestテストを置く初期ディレクトリです。
- `scripts/copy-extension-assets.mjs`, `scripts/clean-dist.mjs`: Chrome拡張のbuild outputを整える補助scriptです。
- `tsconfig.json`, `tsconfig.build.json`, `eslint.config.mjs`, `vitest.config.ts`, `.prettierrc.json`, `.prettierignore`: TypeScript、lint、test、formatの設定です。
- `.github/workflows/chrome-extension-quality-checks.yml`: lint、format、typecheck、Vitest、buildを実行するquality gateです。
- `.github/workflows/chrome-extension-release.yml`: `main` へmergeされたpull requestのmerge result commitでChrome拡張の配布zip、git tag、GitHub Releaseを作成します。

`chrome_extension_mode=adopt_existing` では既存実装を置き換えないため、starter実装やTypeScript設定は生成されません。主に `.copier-answers.yml`, `.node-version`, `AGENTS.md`, `CLAUDE.md`, `LICENSE` などの共通メタデータを取り込みます。

### Tauri関連ファイル

- `package.json`, `.node-version`, `index.html`, `vite.config.ts`: Tauri frontendのNode.js/Vite設定とentrypointです。
- `src/main.ts`, `src/styles.css`, `src/lib/greeting.ts`, `tests/lib/greeting.test.ts`: TypeScript frontendのstarter実装とテストです。
- `src-tauri/Cargo.toml`, `src-tauri/src/`, `src-tauri/tauri.conf.json`, `src-tauri/capabilities/default.json`, `src-tauri/build.rs`: Tauri application shell、Rust code、権限、bundle設定をまとめます。
- `src-tauri/icons/`: Tauri bundleで使う初期iconです。
- `rust-toolchain.toml`: Tauri側のRust toolchainを固定します。
- `.github/workflows/tauri-quality-checks.yml`: frontendのlint、format、typecheck、test、buildと、Rust側のrustfmt、Clippy、Cargo testを実行するquality gateです。

### Release関連ファイル

- `.github/workflows/release.yml`: version sourceを読み、git tagとGitHub Releaseを作成します。
- `.github/workflows/docker-release.yml`: Docker imageをbuild/pushし、git tagとGitHub Releaseを作成します。
- `.github/workflows/chrome-extension-release.yml`: Chrome拡張のbuild outputをzip化し、git tagとGitHub Releaseを作成または再利用します。
- `.github/workflows/pr-tag-check.yml`: pull request上でversion sourceの値が既存tagと衝突しないか確認します。

## ライセンス

詳細は[LICENSE](LICENSE)を参照してください。
