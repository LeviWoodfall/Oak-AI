---
title: Code Review
description: Review code for quality, security, and maintainability
tools: [read_file, search_files, list_directory, git_diff]
tags: [review, quality, security]
trigger: /review
---

# Code Review Workflow

1. **Understand scope** — Read the git diff or specified files
2. **Check against plan** — Does the code match what was planned?
3. **Review by category:**

   **Correctness**
   - Does the logic handle edge cases?
   - Are error paths handled?
   - Could inputs cause crashes?

   **Security**
   - Input validation present?
   - No hardcoded secrets?
   - No SQL injection / path traversal?

   **Style**
   - Follows existing conventions?
   - Clear variable/function names?
   - No dead code or commented-out blocks?

   **Performance**
   - No obvious N+1 queries?
   - No unnecessary allocations in loops?
   - Appropriate data structures?

4. **Report** — List findings by severity:
   - 🔴 **Critical** — Must fix before merge
   - 🟡 **Warning** — Should fix, but not blocking
   - 🔵 **Suggestion** — Nice to have

## Guidelines
- Be specific: cite file:line for every finding
- Suggest fixes, not just problems
- Acknowledge what's done well
