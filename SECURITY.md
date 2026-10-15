# Security policy

role-radar runs on your own machine and handles personal career data. Please report vulnerabilities
**privately** via GitHub's "Report a vulnerability" (Security tab) rather than a public issue.

Things we treat as security bugs: the local dashboard serving anything beyond loopback, any path that
lets personal data (`$PROFILE_DIR`, CVs, ledgers) be committed or sent anywhere unintended, URL-scheme
or injection issues in the fetchers, and secrets appearing in tracked files.

Never post your `profile.json`, CV, `.env`, `.mcp.json` or `ats_targets.json` in an issue.
