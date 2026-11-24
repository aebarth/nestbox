# Nestbox — Instruction Manual

**Version:** 1.x (Docker)  
**Last updated:** November 2026  
**License:** Apache 2.0

---

## Table of Contents

1. [Overview](#1-overview)
2. [System Requirements](#2-system-requirements)
3. [Installation](#3-installation)
4. [First-Time Setup](#4-first-time-setup)
5. [Daily Operations](#5-daily-operations)
   - 5.1 Scanning Documents
   - 5.2 Reviewing & Filing
   - 5.3 Organizing into Boxes
   - 5.4 Searching & Retrieving
6. [Storage Configuration](#6-storage-configuration)
7. [Organizations & Users](#7-organizations--users)
8. [Backups & Disaster Recovery](#8-backups--disaster-recovery)
9. [Updates & Upgrades](#9-updates--upgrades)
10. [Alerts & Monitoring](#10-alerts--monitoring)
11. [Reports](#11-reports)
12. [Security & Compliance](#12-security--compliance)
13. [Troubleshooting](#13-troubleshooting)
14. [Appendix: Environment Variables](#14-appendix-environment-variables)

---

## 1. Overview

Nestbox is a self-hosted document control system for scanned records. It tracks PDFs across local folders, network shares (SMB/NAS), and cloud storage (Google Drive, OneDrive, Dropbox). Key capabilities:

- **OCR full-text search** across all indexed documents
- **Duplicate detection** (hash-based)
- **Change monitoring** — alerts when a tracked file is modified or deleted externally
- **Box tracking** with printable QR labels for physical storage locations
- **Multi-organization** support (personal, business, client workspaces)
- **Database-enforced audit trail** — every create, read, update, delete is logged immutably (FDA QMSR / ISO 13485 ready)
- **In-app scanning** via SANE (Linux) or AirScan (network scanners like Canon R50)

All data stays on your hardware. No third-party services required.

---

## 2. System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| OS | Linux (any distro), macOS, Windows (WSL2) | Ubuntu 22.04+ or Debian 12+ |
| CPU | 2 cores | 4 cores |
| RAM | 2 GB | 4 GB |
| Disk | 10 GB (app + DB) | 50 GB+ depending on document volume |
| Docker | 24.0+ with Compose v2 | Latest stable |
| Network | Local LAN access to scanners/shares | Gigabit for large scans |

**Optional hardware:**
- USB or network scanner (SANE-compatible, e.g. Canon R50 via AirScan)
- NAS / network share for document storage

---

## 3. Installation

### 3.1 Clone the repository

```bash
git clone https://github.com/aebarth/nestbox.git
cd nestbox
```

### 3.2 Configure environment

Copy the example env file and edit:

```bash
cp .env.example .env
nano .env
```

Key variables (see [Appendix](#14-appendix-environment-variables) for the full list):

| Variable | Purpose | Example |
|----------|---------|---------|
| `POSTGRES_PASSWORD` | Database password (required) | `strong-random-password` |
| `NESTBOX_SECRET_KEY` | JWT signing key (required) | `openssl rand -hex 32` |
| `DATA_DIR` | Where scanned PDFs are stored | `/data/documents` |
| `SOURCES_ROOT` | Root folder the app is allowed to watch | `/data/sources` |
| `NESTBOX_VERSION` | Pin a specific release tag (optional) | `v1.0.0` |

### 3.3 Create data directories

```bash
mkdir -p /data/documents /data/backups /data/sources
```

### 3.4 Start the stack

```bash
docker compose up -d
```

Wait ~30 seconds for migrations to run, then open your browser:

```
http://localhost:8080
```

### 3.5 Verify installation

1. You should see the **First-Time Setup** screen (create admin account)
2. After login, navigate to **Admin → Updates** — it should show the installed version
3. Run the [Validation Script](validation-script.md) Part A checks

---

## 4. First-Time Setup

### 4.1 Create the admin account

On first launch, the app shows a setup screen:

- Enter your **full name**, **username**, and **password**
- This creates the install-level admin (role: `admin`)
- There is no way to access the app before this step

### 4.2 Configure storage locations

Navigate to **Storage** (sidebar, visible to admins and org owners):

1. You'll see the default local mount (`DATA_DIR`)
2. Click **Add location** to register additional folders:
   - **Local path**: a subdirectory under `SOURCES_ROOT`
   - **Network share**: SMB/NFS path (must be mounted on the host)
   - **Cloud**: Google Drive / OneDrive / Dropbox (see `docs/google-drive-setup.md` or `docs/microsoft-365-setup.md`)
3. Click **Test** on each location to confirm writability

> ⚠️ The app can only access paths under `SOURCES_ROOT`. This is a security boundary — the container cannot read outside it.

### 4.3 (Optional) Create organizations

If you need separate workspaces (e.g., Personal vs. Business):

1. Go to **Admin → Organizations**
2. Click **Create organization**
3. Name it, choose a kind (`personal` or `business`)
4. Assign yourself as owner

Each org has its own files, boxes, users, and audit trail. Switch between them using the dropdown at the top of the sidebar.

### 4.4 (Optional) Connect a scanner

For in-app scanning (Scan to Shelf):

- **Linux + SANE**: Install `sane` on the host, pass `/dev/usb` or the scanner device into the container
- **Network scanner (AirScan)**: Ensure the scanner is on the same LAN. The app discovers it via mDNS/Bonjour. No extra config needed if the network allows broadcast.

Test from **Scan to Shelf** → the scanner should appear in the device picker.

---

## 5. Daily Operations

### 5.1 Scanning Documents

**From a physical scanner (in-app):**

1. Click **Scan to Shelf** in the sidebar
2. Select your scanner from the device list
3. Set options: DPI (300 for text, 600 for signatures), color/grayscale, file format (PDF)
4. Click **Scan** — pages appear as thumbnails
5. When done, click **Finish** → documents land in the **Review Inbox**

**From a watched folder:**

- Drop PDF files into any configured storage location
- The background worker picks them up within ~30 seconds
- New files appear in the **Review Inbox** automatically

### 5.2 Reviewing & Filing

The **Review Inbox** is your triage queue:

1. Open the **Review Inbox** (sidebar)
2. For each pending document:
   - **Preview** the PDF inline
   - **Rename** if the auto-generated name isn't descriptive
   - **Categorize**: assign a record type (e.g., Invoice, Contract, Lab Result)
   - **Tag / metadata**: add free-text notes, dates, reference numbers
   - **File**: choose which box it belongs to (or leave unboxed for now)
3. Click **Approve** → the document is indexed, searchable, and its audit entry is written
4. Or **Reject** → the file is moved to a rejected folder (not deleted)

### 5.3 Organizing into Boxes

**Boxes** represent physical or logical storage containers:

1. Go to **Boxes** (sidebar)
2. Click **New box** → give it a name, optional location label, and capacity
3. A **QR code** is generated automatically — print it and affix to the physical box
4. To add documents: open a box → **Add records** → search and select
5. To remove: open a box → select records → **Remove from box**

Scanning a box's QR code with your phone opens the box directly in the app (via the `/r/:uuid` deep link).

### 5.4 Searching & Retrieving

1. Go to **Files** (sidebar)
2. Type in the search bar — results include:
   - Filename matches
   - OCR full-text matches (word highlighted)
   - Metadata/tag matches
3. Filter by: record type, box, date range, storage location
4. Click a result → PDF preview opens in the drawer
5. Use **Download** to save a local copy, or **Print** from the browser

---

## 6. Storage Configuration

Detailed guides are in `docs/storage-options.md`. Summary:

| Type | How it works | Setup doc |
|------|-------------|-----------|
| Local folder | Bind-mount into container under `SOURCES_ROOT` | `storage-options.md` §1 |
| SMB / NAS | Mount on host (CIFS/NFS), then bind-mount the subpath | `storage-options.md` §2 |
| Dropbox | rclone mount (FUSE) on host, bind-mount the remote path | `storage-options.md` §3 |
| OneDrive | rclone or Microsoft Graph sync folder | `docs/microsoft-365-setup.md` |
| Google Drive | rclone with OAuth, bind-mount the synced folder | `docs/google-drive-setup.md` |

**Managing locations from the UI:**

- **Storage** page → shows all registered locations with health (reachable / unreachable)
- Click a location → edit its label, set as default for new scans
- **Test** button pings the path and reports writability
- Deleting a location does NOT delete files — it just stops monitoring

---

## 7. Organizations & Users

### 7.1 Roles

| Role | Scope | Capabilities |
|------|-------|-------------|
| `admin` | Install-wide | Everything: manage orgs, users, storage, database, updates |
| `owner` | Per-org | Manage org members, storage locations, backups for that org |
| `member` | Per-org | Scan, review, file, search, box management |

### 7.2 Managing users (Admin → Organizations)

1. Select an organization
2. **Invite user**: enter email/username + role
3. The user sets their password on first login
4. To remove: click the user → **Deactivate** (soft-delete; audit trail preserved)

### 7.3 Multi-org workflow

- The sidebar dropdown switches your active org
- All data (files, boxes, alerts, reports) is scoped to the current org
- An admin can see all orgs; a member only sees the ones they belong to

---

## 8. Backups & Disaster Recovery

### 8.1 Types of backups

| Type | What it covers | Where it's stored | Trigger |
|------|---------------|-------------------|---------|
| **Install-level** (pg_dump) | Entire database (all orgs, audit trail, settings) | `DATA_DIR/backups/` | Manual, scheduled, or pre-update |
| **Org-level** (zip) | One org's documents + metadata export | Downloadable from UI | Manual |

### 8.2 Taking a backup

- **Install-level**: Admin → Database → "Create backup"
- **Org-level**: Backups page → select org → "Create org backup" → download the zip

### 8.3 Restoring

**Install-level restore (nuclear option — replaces ALL data):**

1. Admin → Database → find the backup row → click **Restore**
2. Type `RESTORE` to confirm
3. All active sessions are terminated; the database is dropped and reloaded
4. The app restarts with the restored data

**Org-level restore (targeted — one org's documents):**

1. Backups page → select an org backup → **Restore**
2. Choose target org (same or different)
3. Optionally check **Replace existing data** (wipes the target's current documents first)
4. Type `RESTORE` to confirm

### 8.4 Reset to clean slate

Admin → Database → Danger zone → type `RESET` → all data is dropped, schema rebuilt. A pre-reset backup is taken automatically.

---

## 9. Updates & Upgrades

### 9.1 Checking for updates

1. Go to **Admin → Updates**
2. Click **Check now** — queries GitHub Releases (5-min cache)
3. If a newer version exists: it shows the version, publish date, and release notes

### 9.2 Preparing an update (Tier 1 — guided)

1. Click **Prepare update to vX.Y.Z**
2. The system automatically creates a pre-update backup
3. You'll see the exact commands to run on the host:

```bash
NESTBOX_VERSION=vX.Y.Z docker compose pull
docker compose up -d
```

4. Copy and run those commands in your terminal
5. Wait for containers to restart (~30 s)
6. Click **Verify now** on the Updates page → green banner confirms success

### 9.3 Rolling back

If the update causes problems:

1. Edit `.env`: set `NESTBOX_VERSION=<previous-tag>`
2. Run `docker compose pull && docker compose up -d`
3. If schema changes broke things: Admin → Database → Restore the pre-update backup

### 9.4 Tier 2 (one-click, opt-in)

Requires:
- `NESTBOX_SELF_UPDATE=1` in `.env`
- Docker socket mounted (`/var/run/docker.sock`)

When enabled, a single "Update now" button pulls and recreates containers without leaving the browser. **Security note:** this grants the app container control over the Docker daemon. Only enable on trusted networks. See `SECURITY.md`.

---

## 10. Alerts & Monitoring

The **Alerts** page surfaces issues that need attention:

| Alert type | Trigger | Resolution |
|-----------|---------|------------|
| File modified externally | A tracked PDF's hash changed on disk | Review the change, re-approve or reject |
| File deleted externally | A tracked file disappeared from its location | Confirm deletion or restore the file |
| New file detected | An untracked PDF appeared in a watched folder | Review in Inbox and file it |
| Storage unreachable | A configured location stopped responding | Check mount / network, then "Test" again |

Alerts have a badge count in the sidebar. Resolve them individually or in bulk.

---

## 11. Reports

The **Reports** page generates:

- **Activity report**: all audit log entries for a date range (who did what, when)
- **Inventory report**: current file count by type, box, location
- **Compliance export**: CSV of the full audit trail (for FDA QMSR / ISO 13485 audits)

Exports are downloadable as CSV or PDF.

---

## 12. Security & Compliance

### 12.1 Authentication

- Username + password (bcrypt-hashed, never stored in plaintext)
- JWT tokens with configurable expiry
- Session invalidation on password change
- Optional: reverse proxy (nginx/traefik) for TLS termination

### 12.2 Authorization

- Role-based access control (RBAC): admin / owner / member
- Org-scoped data isolation: a member of Org A cannot see Org B's files
- API endpoints enforce role checks server-side (not just UI hiding)

### 12.3 Audit trail

- Every CRUD operation writes an immutable row to `audit_log`
- The table has a PostgreSQL trigger that **prevents DELETE and UPDATE** for all roles (including the table owner)
- Columns: timestamp, user_id, org_id, action, entity_type, entity_id, metadata (JSONB)
- This is designed to satisfy FDA 21 CFR Part 11 / QMSR electronic record requirements

### 12.4 Data protection

- All data stored locally; no external API calls except GitHub Releases (version check) and OCR (if using a cloud provider)
- Database encrypted at rest via PostgreSQL `pgcrypto` or filesystem-level encryption (LUKS/FileVault/BitLocker)
- Backups are checksummed (SHA-256) to detect tampering

### 12.5 Network security

- The app container only exposes port 8000 (API) and 8080 (frontend/nginx)
- No inbound ports needed for scanning (SANE uses USB passthrough; AirScan uses mDNS broadcast on LAN)
- Run behind a reverse proxy with TLS for internet-facing deployments

---

## 13. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| White screen on load | Frontend container not ready | `docker compose logs frontend`; wait 30 s, refresh |
| "Database not ready" error | Postgres still initializing | `docker compose logs db`; wait for `accepting connections` |
| Scanner not detected | SANE not installed or device not passed through | Check `sane-find-scanner` on host; add device to docker-compose volumes |
| Files not appearing in Inbox | Worker not running or path not under SOURCES_ROOT | `docker compose logs worker`; verify the file is under a registered location |
| Search returns no results | OCR index not built yet | Wait for the initial indexing pass (can take minutes for large libraries); check `docker compose logs worker` |
| "Storage unreachable" alert | Mount dropped or network share offline | Remount on host, then Storage page → Test |
| Update stuck / containers restarting | Bad image tag or disk full | Check `NESTBOX_VERSION` in `.env`; `df -h`; rollback per §9.3 |
| Slow PDF preview | Large file over a slow network share | Pre-cache to local SSD, or increase nginx buffer size |

**Getting help:**
- Check logs: `docker compose logs --tail=100 <service>`
- File an issue: https://github.com/aebarth/nestbox/issues (include log output + `.env` with secrets redacted)

---

## 14. Appendix: Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POSTGRES_PASSWORD` | ✅ | — | PostgreSQL superuser password |
| `NESTBOX_SECRET_KEY` | ✅ | — | JWT signing key (64 hex chars) |
| `DATA_DIR` | ✅ | `/data` | Root for app data (documents, backups) |
| `SOURCES_ROOT` | ✅ | `/data/sources` | Security boundary: only paths under here are accessible |
| `NESTBOX_VERSION` | — | `latest` | Pin a release tag (e.g., `v1.0.0`) |
| `NESTBOX_SELF_UPDATE` | — | `0` | Enable Tier 2 one-click updates (requires Docker socket) |
| `POSTGRES_HOST` | — | `db` | Database hostname (container name) |
| `POSTGRES_PORT` | — | `5432` | Database port |
| `OCR_ENGINE` | — | `tesseract` | OCR backend: `tesseract`, `cloud`, or `none` |
| `OCR_CLOUD_API_KEY` | — | — | API key if using cloud OCR |
| `SCAN_DPI_DEFAULT` | — | `300` | Default scan resolution |
| `JWT_EXPIRY_MINUTES` | — | `480` | Token lifetime (8 h default) |
| `BACKUP_SCHEDULE` | — | — | Cron expression for auto-backups (e.g., `0 3 * * *`) |
| `LOG_LEVEL` | — | `info` | `debug`, `info`, `warn`, `error` |

---

*End of manual. For storage-specific setup, see `docs/storage-options.md`. For cloud provider OAuth walkthroughs, see `docs/google-drive-setup.md` and `docs/microsoft-365-setup.md`.*
