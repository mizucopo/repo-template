# Repository Template

## Language

**Template option**: A Copier answer controlling generated project capabilities.

**Runtime support**: The generated source layout, tool configuration and quality gate for Python, Rust, Chrome Extension or Tauri.

**Python application runtime support**: Directly executed modules at the src root, with dependencies installed but the project itself not installed as a package.

**Copier-managed codebase**: Generated configuration and source updated through Copier three-way merge. Project-specific implementation remains in the destination project.

**Project-owned branding asset**: An initially generated icon owned by the destination after creation; later updates preserve its changes and deletion.

**Codebase update**: copier update merges the current template revision with project evolution and exposes conflicts for review.

**Quality gate**: The project's repeatable local and CI verification command.

**Project version management**: Maintained package/manifest version metadata. Publication is a separate optional capability.

**Version source**: Declared project data used for numbering and release metadata.

**Numbering commit**: A commit on main updating only declared version/lockfile fields. Its parent is the collected source, and its trailers identify the source, run, tag, version and PRs.

**Rerunnable release**: Publication of the numbering commit whose tag and existing deliverables are checked before resuming missing work. Completed releases remain immutable.

**Project Docker image pipeline**: Shared numbering, image ownership checks and publication with project-owned quality/publish/notes hooks and declarative image order.

**Homebrew Tap notification**: Optional notification after a completed stable Tauri release. The separate Tap verifies assets and publishes Casks.

## Release policy

Implementation agents classify all PRs, including Dependabot, with one release:patch/minor/major label and a reason. Normal read-only PR CI precedes squash merge. Actions collects pending merged PRs from latest main, takes the maximum classification and deterministically numbers them without AI.

The standard GITHUB_TOKEN atomically pushes a numbering commit and its annotated tag. Every subsequent job checks out that commit and builds, validates and publishes in the same run. A run ID identifies the same commit on rerun. Remote state is checked after uncertain push results, and old runs cannot roll back latest.

CI definitions are accepted through ordinary PR review. No separate CI admission, App controller, candidate sandbox, signed publication plan or bootstrap protocol is generated. Project publication credentials are scoped to the jobs that need them; PR CI receives read-only permissions and no secrets.

Generated docs/release.md owns setup and recovery instructions. This repository changes template source; downstream setup, credentials, protection settings and actual publication are separate work.
