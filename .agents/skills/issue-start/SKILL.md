---
name: issue-start
description: Use at the beginning of every session to load context from GitHub Issue and project files.
---

## Steps — run in this exact order

1. Read the issue assigned to this session:
```
   gh issue view {N} --comments
```

2. Read stable project context:
```
   cat .agents/CONTEXT.md
   tail -15 .agents/HISTORY.md
   cat .agents/SESSION.md
```

3. Confirm understanding by summarising:
   - Goal from the issue
   - Files to touch
   - Acceptance criteria
   - Any known traps

4. Create the branch:
```
   git checkout main && git pull
   git checkout -b feat/issue-{N}-{slug}
```

Only begin coding after completing all four steps.