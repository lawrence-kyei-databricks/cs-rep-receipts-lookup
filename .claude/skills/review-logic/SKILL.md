---
name: review-logic
description: Review code for logic issues, bugs, and edge cases
---

Run the Logic Reviewer Agent to analyze code for:
- Critical logic issues and bugs
- Unhandled edge cases
- Off-by-one errors and race conditions
- Code clarity improvements

When the user invokes this command with a file path, execute:
`cd ~/Desktop/code-review-agents && python review_code.py logic <file_path>`

If no file path is provided, use the currently open file or ask the user which file to review.

Show the complete review output in a structured format.
