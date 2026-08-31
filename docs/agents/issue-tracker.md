# Issue tracker: GitHub Issues

Issues for this repository are tracked in GitHub Issues. Infer the repository
from the configured Git remote instead of hard-coding an owner or repository
name in agent instructions.

## Conventions

- Creating an implementation Issue is optional; implementation work may proceed
  without one.
- When an implementation or review-follow-up Issue is useful, create it as a
  GitHub Issue.
- Read the full issue body, comments, and labels before acting on an issue.
- Keep issues concise and centered on the purpose, desired outcome, and problem
  or open question. Add acceptance criteria only when they clarify what done
  means.
- Avoid prescribing implementation details unless they are requirements or
  constraints. Decide the approach when implementation begins so it reflects
  the current code, tools, and constraints.
- Include source URLs or other evidence when needed to explain the purpose or
  constraints.
- Use GitHub's native issue dependencies for blocking relationships when they
  are available. Otherwise, record blockers in the issue body.
- Treat pull requests as implementation and review surfaces, not as substitutes
  for triage issues.

## Skill terminology

When a skill says to publish a ticket, create a GitHub Issue. When a skill says
to fetch a ticket, read the corresponding GitHub Issue. Prefer an available
GitHub integration and use the `gh` CLI as the fallback.
