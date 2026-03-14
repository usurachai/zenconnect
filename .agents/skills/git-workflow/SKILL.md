---
name: git-workflow
description: Use this skill for any git operation, branching, committing, PR creation, or CI feedback loop.
---

## Rules

### Branching
Always branch from main. Name: `feat/issue-{N}-{slug}` or `fix/issue-{N}-{slug}`.
```
git checkout main && git pull
git checkout -b feat/issue-{N}-{slug}
```

### Commits
Use conventional commits. Atomic — one logical change per commit.
```
git add -p                  # review every hunk before staging
git commit -m "feat(scope): description"
```
Types: feat / fix / refactor / chore / test / docs

### Progress comments
After every meaningful commit, leave a breadcrumb on the issue:
```
gh issue comment {N} --body "[$(date '+%Y-%m-%d %H:%M')] · what I just did · what's next"
```

### PR creation
```
gh pr create \
  --title "feat(scope): description (closes #{N})" \
  --body "## What\n...\n\nCloses #{N}" \
  --draft
```
Always include `Closes #{N}` so the issue auto-closes on merge.

### CI feedback loop
```
gh pr checks --watch
gh run view --log-failed     # read failure → fix → push → CI reruns automatically
```

### Never
- Never commit directly to main
- Never force-push a shared branch
- Never commit secrets, .env files, or __pycache__