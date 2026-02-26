---
description: Scan code for security vulnerabilities and compliance issues
---

Run the Security Agent to scan for:
- OWASP Top 10 vulnerabilities
- SQL injection, XSS, CSRF risks
- Hardcoded secrets and credentials
- PII exposure and GDPR/CCPA compliance
- Authentication/authorization flaws
- Risk level assessment (LOW/MEDIUM/HIGH/CRITICAL)

Execute: `cd ~/Desktop/code-review-agents && python review_code.py security {{file}}` where {{file}} is the currently open file or the file path provided as an argument.

Show me the complete security review including risk level.
