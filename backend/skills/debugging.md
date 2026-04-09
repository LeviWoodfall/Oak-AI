---
title: Systematic Debugging
description: 4-phase root cause process for tracking down bugs
tools: [read_file, search_files, run_python, run_shell, edit_file]
tags: [debugging, troubleshooting, bugfix]
trigger: /debug
---

# Systematic Debugging Workflow

4-phase root cause analysis:

## Phase 1: Reproduce
- Get the exact error message or unexpected behaviour
- Write a minimal reproduction (test or script)
- Confirm the bug is reproducible

## Phase 2: Isolate
- Read the relevant code with read_file
- Search for related patterns with search_files
- Add targeted logging/print statements to narrow down
- Binary search: comment out halves to find the culprit

## Phase 3: Fix
- Address the ROOT CAUSE, not symptoms
- Make the minimal change that fixes the issue
- Prefer single-line fixes over refactors

## Phase 4: Verify
- Run the reproduction — confirm the bug is fixed
- Run existing tests — confirm nothing else broke
- Add a regression test if one doesn't exist

## Guidelines
- Never guess. Always reproduce first.
- Don't fix multiple things at once.
- If the fix requires changing more than 10 lines, reconsider the approach.
- Document what caused the bug and why the fix works.
