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
- `use_tauri`: Tauri関連ファイルを生成するか
- `use_gh_actions_docker_release`: .github/workflows/docker-release.ymlを生成するか
- `use_gh_actions_release`: .github/workflows/release.ymlを生成するか（`use_gh_actions_docker_release`が有効な場合は無視される）
- `use_gh_actions_pr_tag_check`: .github/workflows/pr-tag-check.ymlを生成するか

`chrome_extension_mode=scaffold` は新規Manifest V3 Chrome拡張向けに、`package.json`, `src/`, `tests/`, TypeScript/Vitest/ESLint設定、Chrome拡張品質チェックworkflowを生成します。

`chrome_extension_mode=adopt_existing` は既存JavaScript Chrome拡張向けに、starter TypeScript拡張ファイルを生成しません。既存の `package.json`, `src/`, `test` / `tests`, `.github/workflows/release.yml` をテンプレートで置き換えず、`.node-version`, `AGENTS.md`, `CLAUDE.md`, licenseなどの共通メタデータだけを取り込む用途で使用してください。

`use_tauri` は専用の `src-tauri` と Node.js フロントエンドを生成するため、`use_rust` と `use_chrome_extension` とは同時に利用できません。

## ライセンス

詳細は[LICENSE](LICENSE)を参照してください。
