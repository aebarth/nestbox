# Nestbox Validation Script

Run this script **before** applying an update to confirm the current install is healthy, and **after** to verify the new version works correctly. Each section has a pass/fail box — stop and troubleshoot if any check fails before proceeding.

---

## Part A — Pre-Update Checks (run on the CURRENT version)

### A1. System health

| # | Check | Command / Action | Expected result | Pass |
|---|-------|-----------------|-----------------|------|
| 1 | All containers running | `docker compose ps` | All services show `Up` (no `Restarting` or `Exited`) | ☐ |
| 2 | No error logs in last hour | `docker compose logs --since=1h backend \| grep -i "error\|fatal"` | No output, or only benign warnings | ☐ |
| 3 | Disk space ≥ 2 GB free on data volume | `df -h /path/to/nestbox/data` (or the bind-mount path) | ≥ 2 GB available | ☐ |
| 4 | PostgreSQL accepting connections | `docker compose exec db pg_isready` | `accepting connections` | ☐ |
| 5 | Frontend responds | Open browser → app URL | Dashboard loads, no white screen | ☐ |

### A2. Data integrity

| # | Check | Command / Action | Expected result | Pass |
|---|-------|-----------------|-----------------|------|
| 6 | Database backup exists (recent) | Admin → Database → Backups list | At least one backup < 7 days old | ☐ |
| 7 | Manual backup succeeds NOW | Admin → Database → "Create backup" | New row appears, no error toast | ☐ |
| 8 | File count matches expectations | Admin → Dashboard or Files page | Record count looks correct (spot-check 3–5 known files) | ☐ |
| 9 | Audit trail is recording | `docker compose exec db psql -U nestbox -c "SELECT COUNT(*) FROM audit_log ORDER BY id DESC LIMIT 1;"` | Count > 0 and recent timestamps | ☐ |

### A3. Feature smoke test (quick pass)

| # | Check | Action | Expected result | Pass |
|---|-------|--------|-----------------|------|
| 10 | Login works | Sign out → sign back in with admin credentials | Dashboard loads | ☐ |
| 11 | File search returns results | Files page → type a known filename or keyword | Results appear within 2 s | ☐ |
| 12 | PDF preview opens | Click a known PDF record | Drawer opens, PDF renders | ☐ |
| 13 | Box list loads | Boxes page | Boxes with QR labels visible | ☐ |
| 14 | Alerts page loads | Alerts page | No error (empty list is fine) | ☐ |
| 15 | Org switcher works (multi-org) | If > 1 org: use the dropdown in sidebar | All pages re-scope to new org | ☐ |

### A4. Pre-update snapshot

| # | Action | Command / Action | Pass |
|---|--------|-----------------|------|
| 16 | Record current version | Note from Admin → Updates (or `docker compose exec backend python -c "from services.updates import get_installed_version; print(get_installed_version())"`) | ☐ |
| 17 | Take a fresh backup NOW | Admin → Database → Create backup (label it "pre-update") | ☐ |
| 18 | Note the pinned tag in `.env` | `grep NESTBOX_VERSION .env` (if present) | ☐ |

> **If any A-section check fails: do NOT proceed with the update.** Fix the issue first, or restore from backup and re-validate.

---

## Part B — Post-Update Checks (run on the NEW version)

### B1. Startup & migration

| # | Check | Command / Action | Expected result | Pass |
|---|-------|-----------------|-----------------|------|
| 19 | All containers running | `docker compose ps` | All services `Up`, no restart loop | ☐ |
| 20 | Migrations completed | `docker compose logs backend \| grep -i "migration\|schema"` | "migrations applied" or similar, no errors | ☐ |
| 21 | Version updated | Admin → Updates page | Shows the NEW version (matches what you pinned) | ☐ |
| 22 | Verify button confirms | Admin → Updates → "Verify now" | Green banner: "Update verified — running vX.Y.Z" | ☐ |

### B2. Core functionality

| # | Check | Action | Expected result | Pass |
|---|-------|--------|-----------------|------|
| 23 | Login works | Sign out → sign in | Dashboard loads | ☐ |
| 24 | File list + search | Files page → search a known term | Results return, PDFs open | ☐ |
| 25 | Review Inbox | Inbox page | Pending items visible (or empty state) | ☐ |
| 26 | Scan to Shelf (wizard) | Wizard page → start a scan session | Scanner detected (or "no scanner" message if headless) | ☐ |
| 27 | Box management | Boxes page → open a box, add/remove a record | Changes persist after refresh | ☐ |
| 28 | Alerts | Alerts page → resolve an alert (if any) | Status updates, count decrements | ☐ |
| 29 | Reports | Reports page | Generates without error | ☐ |

### B3. Admin features

| # | Check | Action | Expected result | Pass |
|---|-------|--------|-----------------|------|
| 30 | Organizations | Admin → Organizations | Lists all orgs, can create a test org | ☐ |
| 31 | Storage / mounts | Admin → Storage (or Storage page) | Mounts listed with health status | ☐ |
| 32 | Database backup + restore | Admin → Database → Create backup → Restore it | Backup created, restore succeeds (data intact) | ☐ |
| 33 | Org backup | Backups page → create org backup | Zip generated, downloadable | ☐ |
| 34 | Updates page | Admin → Updates | Shows version, "Check now" works, no errors | ☐ |

### B4. Multi-org isolation (if applicable)

| # | Check | Action | Expected result | Pass |
|---|-------|--------|-----------------|------|
| 35 | Org A data not visible in Org B | Switch orgs → Files page | Only the current org's records appear | ☐ |
| 36 | Non-admin cannot access admin pages | Log in as a member role → navigate to /admin | Redirected to `/` (403) | ☐ |

### B5. Rollback readiness (confirm before closing out)

| # | Check | Command / Action | Expected result | Pass |
|---|-------|-----------------|-----------------|------|
| 37 | Old backup still restorable | Admin → Database → confirm the "pre-update" backup is listed | Visible, checksum intact | ☐ |
| 38 | Rollback command ready | `NESTBOX_VERSION=<old-tag> docker compose pull && docker compose up -d` | (Don't run — just confirm you have the old tag) | ☐ |

---

## Sign-off

| Role | Name | Date | Result |
|------|------|------|--------|
| Operator | _________________ | _______ | ☐ Pass / ☐ Fail |
| Admin (if different) | _________________ | _______ | ☐ Pass / ☐ Fail |

**If any B-section check fails:**
1. Check `docker compose logs backend --tail=50` for errors
2. If data is corrupted → stop containers, restore the pre-update backup from Part A step 17
3. Pin the old version: `NESTBOX_VERSION=<old-tag>` in `.env`, then `docker compose up -d`
4. File a GitHub issue with the log output
