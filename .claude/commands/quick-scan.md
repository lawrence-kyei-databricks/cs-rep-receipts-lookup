---
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

Execute: `cd ~/Desktop/code-review-agents && python review_code.py quick-scan {{file}}` where {{file}} is the currently open file or the file path provided as an argument.

Show me the scan results with issue count.
