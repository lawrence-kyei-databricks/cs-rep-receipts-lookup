---
name: review-all
description: Run all three review agents for comprehensive analysis
---

Run ALL THREE review agents for comprehensive code analysis:

1. **Logic Reviewer** - Bugs, edge cases, logic issues
2. **Security Agent** - Vulnerabilities, compliance, risk assessment
3. **Databricks Architect** - Platform best practices (if applicable)

When the user invokes this command with a file path, execute:
`cd ~/Desktop/code-review-agents && python review_code.py all <file_path>`

If no file path is provided, use the currently open file or ask the user which file to review.

Note: This takes longer as all three agents run, but provides the most thorough analysis.

Show all review results in a structured format with clear sections for each agent's findings.
