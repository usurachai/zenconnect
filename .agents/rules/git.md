---
trigger: always_on
---

# Git best practices — always enforced

## Branching
- ALWAYS branch from latest main before starting any new task
- NEVER commit directly to main under any circumstance
- Branch naming: `feat/issue-{N}-{slug}` or `fix/issue-{N}-{slug}`

## Before every commit — run all three
- ruff check .
- mypy .
- pytest -x -q
Only commit if all three pass. Fix failures before committing.

## Commit format
Use conventional commits — always:
  feat(scope): description
  fix(scope): description
  chore: description
One logical change per commit. Use git add -p to stage hunks individually.

## Progress tracking
After every commit, comment on the active GitHub issue:
  gh issue comment {N} --body "[YYYY-MM-DD HH:MM] · what I just did · what's next"

## PR creation
Always create as draft first:
  gh pr create --title "..." --body "Closes #{N}" --draft
Include "Closes #{N}" in the body so the issue auto-closes on merge.

## CI feedback loop
After pushing, always run:
  gh pr checks --watch
If checks fail: read gh run view --log-failed → fix → push → repeat.
Do not ask the user. Self-correct until CI is green.

## Never
- Never force-push
- Never commit .env, secrets, __pycache__, or *.pyc
- Never skip the pre-commit checks
- Never merge with failing CI