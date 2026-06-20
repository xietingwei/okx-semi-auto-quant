# Security Policy

## Reporting a vulnerability

Do not disclose credentials, private account data, positions, or exploitable
details in a public GitHub issue.

Use
[GitHub private vulnerability reporting](https://github.com/xietingwei/okx-semi-auto-quant/security/advisories/new)
when it is available for this repository. Include:

- affected version or commit;
- reproduction steps;
- expected impact;
- suggested mitigation, if known.

## Credential safety

- Store secrets only in `.env`; it is excluded from Git.
- Use OKX API keys with the minimum required permissions.
- Do not enable withdrawal permissions.
- Start with `OKX_SIMULATED=1` and `QIS_MODE=paper`.
- Rotate a key immediately if it appears in logs, screenshots, commits, issues,
  or chat history.

## Supported version

Security fixes are applied to the latest commit on the `main` branch.

## Scope

The following are especially important:

- authentication or secret leakage;
- unintended live order execution;
- bypasses of pause or risk controls;
- command injection through hooks or configuration;
- unsafe handling of LLM or external-news content;
- exposure of local SQLite data through the web server.

Trading losses caused by market movement or model performance are not security
vulnerabilities, but deterministic risk-control bypasses are.
