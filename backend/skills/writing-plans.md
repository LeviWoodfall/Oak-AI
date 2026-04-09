---
title: Writing Plans
description: Break work into bite-sized tasks with exact file paths, code, and verification steps
tools: [read_file, list_directory, search_files]
tags: [planning, implementation, tasks]
trigger: /plan
---

# Writing Plans Workflow

When an approved design needs to become executable tasks:

1. **Read the codebase** — Use list_directory and read_file to understand the current structure
2. **Break into tasks** — Each task should be 2-5 minutes of work. Include:
   - Exact file paths to create or modify
   - What to change (with before/after snippets if editing)
   - Verification step (how to confirm the task is done)
3. **Order tasks** — Dependencies first. Group related changes.
4. **Present the plan** — Numbered list with checkboxes. Wait for approval.

## Task Format
```
[ ] Task 1: Create backend/feature.py
    - File: backend/feature.py
    - Action: Create new file with FeatureClass
    - Verify: import succeeds, tests pass

[ ] Task 2: Wire into main.py
    - File: backend/main.py
    - Action: Add import and route
    - Verify: /api/feature endpoint responds 200
```

## Guidelines
- Every task must have a verification step
- No task should touch more than 2 files
- If a task is too big, split it
