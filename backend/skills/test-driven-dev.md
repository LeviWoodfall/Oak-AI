---
title: Test-Driven Development
description: RED-GREEN-REFACTOR cycle for reliable code
tools: [read_file, write_file, edit_file, run_python, run_shell]
tags: [testing, tdd, quality]
trigger: /tdd
---

# Test-Driven Development Workflow

Enforces RED-GREEN-REFACTOR:

1. **RED** — Write a failing test first
   - Create or edit the test file
   - Run the test — confirm it FAILS
   - If it passes, the test is wrong — rewrite it

2. **GREEN** — Write the minimal code to make it pass
   - Only enough code to pass the test
   - No extra features, no premature optimisation
   - Run the test — confirm it PASSES

3. **REFACTOR** — Clean up without breaking tests
   - Remove duplication
   - Improve naming
   - Run tests again — confirm still GREEN

4. **Repeat** for each behaviour

## Guidelines
- Never write implementation before tests
- One behaviour per test function
- Test file mirrors source file: `feature.py` → `test_feature.py`
- Use pytest as the test runner
- Aim for 80%+ coverage on new code
