---
name: quick-scan
description: Quick security scan for obvious vulnerabilities
---

Run a fast security scan to quickly detect:
- Hardcoded secrets and passwords
- SQL injection vulnerabilities
- Command injection risks
- Path traversal issues
- Insecure random number generation
- Weak cryptography

This is MUCH faster than `/review-security` but less detailed.

When the user invokes this command with a file path, execute:
`cd ~/Desktop/code-review-agents && python review_code.py quick-scan <file_path>`

If no file path is provided, use the currently open file or ask the user which file to review.

Show the scan results with issue count and severity levels.
