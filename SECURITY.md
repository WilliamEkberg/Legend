# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Legend, please report it responsibly. **Do not open a public GitHub issue.**

### How to Report

1. **GitHub Security Advisories (preferred):** Go to the [Security Advisories](../../security/advisories/new) page and create a new private advisory.
2. **Email:** Contact the maintainers directly at the email listed in the repository profile.

### What to Include

- A description of the vulnerability and its potential impact
- Steps to reproduce the issue
- Any relevant logs, screenshots, or proof-of-concept code

### Scope

The following are in scope for security reports:

- Authentication or authorization bypasses
- Injection vulnerabilities (SQL, command, XSS, etc.)
- Sensitive data exposure (API keys, credentials, PII leaks)
- Path traversal or arbitrary file access
- Vulnerabilities in dependencies used by Legend

The following are **out of scope**:

- Issues in third-party services or upstream dependencies (report those to the relevant project)
- Denial of service via resource exhaustion on local-only deployments
- Social engineering attacks

### Response Expectations

- We will acknowledge your report within **5 business days**
- We will provide an initial assessment within **10 business days**
- We will work with you to understand and resolve the issue before any public disclosure

Thank you for helping keep Legend and its users safe.
