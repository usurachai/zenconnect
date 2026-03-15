---
description: Quick knowledge capture — local file only, no GitHub issue
allowed-tools:
  - Bash
  - Write
---

# /snapshot — Knowledge Capture

Capture what was just learned. Fast, local, no overhead.

## Usage
```
/snapshot [descriptive title]
```
Example: `/snapshot jwt-expiry-edge-case`

## Steps

### 1. Get Title
From `$ARGUMENTS` or derive from recent commit messages.  
Slugify: lowercase, spaces → hyphens, strip special chars.

### 2. Get Timestamp
```bash
date +"%Y-%m/%d/%H.%M"
```

### 3. Write File
Path: `docs/learnings/YYYY-MM/DD/HH.MM_[title-slug].md`

```bash
mkdir -p "docs/learnings/$(date +%Y-%m/%d)"
```

```markdown
# [Title]

**Time**: [HH:MM]  
**Source**: [what triggered this — issue, error, experiment]

## What We Learned
- [Key insight 1]
- [Key insight 2]

## How Things Connect
- [X] relates to [Y] because [reason]
- Pattern [A] enables [B] when [condition]

## Key Discoveries
- [Technical finding with enough context to be useful cold]

## Apply When
- [Trigger condition 1]
- [Trigger condition 2]

## Tags
`tag1` `tag2` `tag3`
```

### 4. Commit
```bash
git add "docs/learnings/"
git commit -m "learn: [title-slug]"
```

## Rules
- Knowledge focus — what was learned, not what was done
- Fast — under 60 seconds total
- No GitHub issue — use `ccc` for that
- Descriptive filename — the slug is the searchable key

## Arguments
ARGUMENTS: $ARGUMENTS
