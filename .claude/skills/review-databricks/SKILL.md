---
name: review-databricks
description: Review code for Databricks platform best practices
---

Run the Databricks Architect Agent to review:
- Unity Catalog governance patterns
- Delta Lake optimization opportunities
- Lakebase design patterns
- Databricks Apps best practices
- Performance tuning recommendations
- Security & compliance
- Cost optimization

When the user invokes this command with a file path, execute:
`cd ~/Desktop/code-review-agents && python review_code.py databricks <file_path>`

If no file path is provided, use the currently open file or ask the user which file to review.

Show the complete Databricks architecture review with platform-specific recommendations.
