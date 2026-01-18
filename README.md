# Repository Template

A simple copier template for bootstrapping new repositories.

## Usage

```bash
copier copy <template-url> <destination>
```

## Updating the Template

To update your project with the latest changes from this template:

```bash
copier update
```

This will merge the latest template changes while preserving your project-specific customizations.

## Options

- `use_pyproject`: Generate pyproject.toml?
- `use_gh_actions_docker_release`: Generate .github/workflows/docker-release.yml?
- `use_gh_actions_pr_tag_check`: Generate .github/workflows/pr-tag-check.yml?

## License

See [LICENSE](LICENSE) for details.
