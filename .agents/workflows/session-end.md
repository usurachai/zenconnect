---
description: Use after gh pr merge to update logs and spawn the next GitHub issue.
---


## Steps

1. Confirm merge:
```
   gh pr merge --squash --delete-branch
```

2. Append to HISTORY.md (one line, compact):
```
   echo "[$(date '+%Y-%m-%d %H:%M')] Closed #{N}: {summary} – merged #{PR}" >> .agents/HISTORY.md
```

3. Create next issue with full context (use this template):
```
   gh issue create \
     --title "{next goal}" \
     --body "## Goal\n{one sentence}\n\n## Context\n- Follows from #{N}\n- Files: ...\n\n## Acceptance criteria\n- [ ] ...\n\n## Known traps\n..." \
     --label "agent-ready"
```

4. Rewrite SESSION.md:
```
   cat > .agents/SESSION.md << EOF
   ## Last — $(date '+%Y-%m-%d %H:%M')
   Closed: #{N} — {summary}

   ## Next
   Issue: #{N+1}
   Start: gh issue view {N+1} --comments
   EOF
```

5. Commit and push:
```
   git add .agents/HISTORY.md .agents/SESSION.md
   git commit -m "chore: session log – closed #{N}, spawned #{N+1}"
   git push
```
```

---

## Trigger the loop from Agent Manager

In the Manager Surface you spawn agents and give them high-level objectives — each spawns a dedicated agent instance with its own workspace. Artifacts like task lists, implementation plans, and browser recordings let you verify the agent's logic at a glance, and you can leave feedback directly on the Artifact like commenting on a doc. 

Your Agent Manager prompt for each session is now just two lines:
```
Use the issue-start skill for issue #42.
Use the git-workflow skill throughout. Use session-end skill when done.