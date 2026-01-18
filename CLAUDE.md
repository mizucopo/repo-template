# Git Flow

- main: Production
- develop: Development
- feature/*: Features (branch from develop, merge to develop)
- release/*: Not used
- hotfix/*: Not used

## Workflow
git checkout develop && git pull
git checkout -b feature/*
gh pr create --base develop

## Quality Check
uv run task test

## Code Organization Rules
- 1 file = 1 class
- 1 class = 1 test file
- 1 test = 1 function
