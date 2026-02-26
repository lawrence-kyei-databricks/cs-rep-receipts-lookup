---
description: Run all three review agents for comprehensive analysis
---

Run ALL THREE review agents for comprehensive code analysis:

1. **Logic Reviewer** - Bugs, edge cases, logic issues
2. **Security Agent** - Vulnerabilities, compliance, risk assessment
3. **Databricks Architect** - Platform best practices (if applicable)

Execute: `cd ~/Desktop/code-review-agents && python review_code.py all {{file}}` where {{file}} is the currently open file or the file path provided as an argument.

Note: This takes longer as all three agents run, but provides the most thorough analysis.

Show me all review results in a structured format.
