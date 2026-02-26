---
name: review-security
description: Scan code for security vulnerabilities and compliance issues
---

Run the Security Agent to scan for:
- OWASP Top 10 vulnerabilities
- SQL injection, XSS, CSRF risks
- Hardcoded secrets and credentials
- PII exposure and GDPR/CCPA compliance
- Authentication/authorization flaws
- Risk level assessment (LOW/MEDIUM/HIGH/CRITICAL)

When the user invokes this command with a file path, execute:
`cd ~/Desktop/code-review-agents && python review_code.py security <file_path>`

If no file path is provided, use the currently open file or ask the user which file to review.

Show the complete security review including risk level assessment.
