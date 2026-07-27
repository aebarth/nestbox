# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately via
[GitHub Security Advisories](https://github.com/aebarth/nestbox/security/advisories/new)
rather than opening a public issue. We aim to acknowledge reports within a week.

## Security model

Nestbox is a **self-hosted LAN application** for a trusted team. What it defends against:

- **Credential attacks**: bcrypt-hashed passwords; login lockout (5 failures / 15 min per
  client+username); httpOnly `SameSite=Lax` session cookies (never readable by page scripts);
  the `Secure` cookie flag is set automatically when `APP_BASE_URL` is https.
- **Privilege boundaries**: admin-only routes are enforced server-side (user management,
  sources, settings, lookups, bulk categorization, record retire/supersede).
- **Injection**: all SQL is parameterized; Drive/Graph query literals are escaped;
  filenames are sanitized before use in HTTP headers; CSV exports neutralize spreadsheet
  formula injection; React escapes all rendered output.
- **Untrusted documents**: scanned PDFs stream with `Content-Security-Policy: sandbox` +
  `nosniff`, and the in-app viewer renders pages to canvas (PDF.js) rather than executing
  the browser's plugin context.
- **Path traversal**: local sources are confined to the mounted `/sources` and `/inbox`
  roots with resolved-path containment checks.
- **Records integrity**: deletes are rejected by database triggers on all record tables;
  the audit log is append-only and immutable, enforced by PostgreSQL, not application code.
- **Secrets at rest**: cloud client secrets and OAuth refresh tokens are Fernet-encrypted
  with a key derived (HKDF) from `SECRET_KEY`; secret values are never returned to the
  browser or written to logs. OAuth state tokens are signed and expire in 10 minutes.
- **Network exposure**: only the frontend (port 3000) is meant to be LAN-reachable; the
  backend port is bound to 127.0.0.1 in the shipped compose files; Postgres is not
  published at all. The frontend sets CSP, `nosniff`, frame and referrer policies.

## Deployment expectations (your side)

- Set a strong `SECRET_KEY` (`openssl rand -hex 32`) — the app logs a critical warning
  without one. Rotating it invalidates sessions and stored cloud credentials.
- Create the first admin account immediately after `docker compose up` — until it exists,
  anyone who can reach the app can claim it.
- If you expose Nestbox beyond a trusted LAN, put it behind a reverse proxy with TLS and
  set `APP_BASE_URL` to the https URL.
- Cloud client secrets expire (Entra: max 24 months) — rotate them in Admin → Settings.
- Containers currently run as root (common Docker-Desktop default; simplifies bind-mount
  permissions across macOS/Windows/Linux). On a hardened Linux host, consider adding
  `user:` mappings in compose that match your mounted folders' ownership.

## Known accepted findings

- `npm audit` flags react-router ≥7.12 for an RSC-mode CSRF advisory
  (GHSA-qwww-vcr4-c8h2). Nestbox is a pure client-side SPA — it does not use React
  Server Components or SSR, so the vulnerable code path is unreachable. Downgrading
  would reintroduce two fixed advisories, so we stay on ≥7.18.
