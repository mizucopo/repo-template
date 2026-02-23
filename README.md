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

## オプション

- `use_pyproject`: pyproject.tomlを生成するか
- `use_gh_actions_docker_release`: .github/workflows/docker-release.ymlを生成するか
- `use_gh_actions_release`: .github/workflows/release.ymlを生成するか（`use_gh_actions_docker_release`が有効な場合は無視される）
- `use_gh_actions_pr_tag_check`: .github/workflows/pr-tag-check.ymlを生成するか

## ライセンス

詳細は[LICENSE](LICENSE)を参照してください。
