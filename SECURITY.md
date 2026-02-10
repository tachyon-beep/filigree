# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.x (latest) | Yes |

Only the latest release receives security fixes.

## Reporting a Vulnerability

To report a security vulnerability, use [GitHub's private vulnerability reporting](https://github.com/tachyon-beep/filigree/security/advisories/new).

We aim to acknowledge reports within 7 days and provide a fix or mitigation plan within 30 days.

## Scope

Filigree is a **local-only** tool. There is no network surface unless the optional web dashboard (`filigree-dashboard`) is enabled, in which case it binds to `localhost:8377` by default.

The primary attack surface is:

- **Local file access**: Filigree reads/writes a SQLite database in `.filigree/`
- **CLI input handling**: User-supplied arguments are passed to SQLite via parameterized queries
- **MCP server**: Communicates over stdio (no network)

SQL injection is mitigated by parameterized queries throughout. If you find a case where user input reaches SQL without parameterization, please report it.
