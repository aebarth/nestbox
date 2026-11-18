# 📦 Nestbox

**Self-hosted document control for scanned records.** Nestbox tracks your scanned PDFs
across network shares, Google Drive, and OneDrive/SharePoint — categorizes them, watches
them for changes, flags duplicates, links them to the physical box on the shelf, and keeps
an append-only audit trail suitable for regulated recordkeeping (FDA QMSR / ISO 13485-style
environments).

- **Find**: import file name, date, size, and location from every repository you point it at
- **Read**: built-in PDF viewer, plus OCR (Tesseract) on scanned documents — search your
  records by what's *inside* them, not just their names
- **Categorize**: project, record type, lot number, part number, tags, notes — with a bulk
  **Auto-categorize** flow that maps your existing folder structure to record types in one
  pass (and keeps the rules for future scans), so thousands of legacy records don't need
  hand-tagging
- **Monitor**: scheduled scans detect renamed, moved, modified, and deleted files and raise alerts
- **Deduplicate**: exact duplicates by content hash, and *near*-duplicates by text similarity —
  the same paper document scanned twice is caught even though the files' bytes differ
- **Box tracking**: every record knows which physical box it lives in; print QR labels
- **Scan-to-Shelf wizard**: scan → name & categorize → print label → assign box → file the PDF
- **Reports**: missing files, duplicates, retention-due, box inventory — CSV and PDF
- **Audit trail**: every change recorded (who/what/when, old → new); deletes are blocked at the
  database level — records are retired or superseded, never erased
- **Roles**: admin (manage users, sources, settings) and user (search, categorize, file)

Runs anywhere Docker runs: Windows, macOS, Linux.

## Quick start

Requirements: [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Compose).

```bash
git clone https://github.com/aebarth/nestbox.git
cd nestbox
cp .env.example .env
# edit .env: set SECRET_KEY (openssl rand -hex 32) and your two folders
docker compose -f docker-compose.dev.yml up -d --build
```

Open **http://localhost:3000**, create the admin account, and you're in.

Then:

1. **Admin → Sources** — add each folder / drive you want tracked
2. **Dashboard → Scan all sources now** — first import
3. Adjust the schedule in **Admin → Settings** (default: nightly scan, weekly integrity check)

### The two folders in `.env`

| Variable | What it is |
|---|---|
| `NESTBOX_SOURCES_PATH` | A host folder containing the document folders you track. Mount network shares on the **host** first (macOS: Finder ⌘K, Windows: map a drive, Linux: cifs mount) and point this at them. Inside the app they appear under `/sources`. |
| `NESTBOX_INBOX_PATH` | Where your scanner drops new PDFs. The Scan-to-Shelf wizard files them from here. |

### Cloud repositories

Each organization registers its **own** (free) API credentials — nothing is shared:

- **OneDrive / SharePoint**: [docs/microsoft-365-setup.md](docs/microsoft-365-setup.md)
- **Google Drive**: [docs/google-drive-setup.md](docs/google-drive-setup.md)

### Connecting a scanner

Nestbox doesn't talk to scanners directly — it watches folders. Point your scanner at a
folder, then make sure Nestbox tracks that folder (as the inbox, or as a source under
`/sources`).

**Example: Canon R50 → shared folder**

1. **Create a drop folder on the host** running Docker, e.g. `~/nestbox/inbox`, and share it
   over the network if the scanner isn't attached by USB:
   - Windows: right-click the folder → *Give access to* → *Specific people*
   - macOS: System Settings → General → Sharing → *File Sharing* → **+**
   - Linux: add a Samba share (`smb.conf`)
2. **Point the scanner at it.** In the Canon's settings choose *Scan to folder / SMB*, enter
   `\\<host>\inbox` (or `smb://<host>/inbox`) with a username and password, and set the output
   format to PDF. A USB-connected scanner that saves to a local path can write straight into
   the same folder instead.
3. **Tell Nestbox where it is.** In `.env`, set `NESTBOX_INBOX_PATH` to that folder and restart
   the containers (`docker compose -f docker-compose.dev.yml up -d --build`). The built-in
   *inbox* source tracks it automatically — no new source needed. If the scanner writes
   somewhere else (another PC, a mapped share), mount that share on the host first and put the
   folder under `NESTBOX_SOURCES_PATH` instead.
4. **Scan and review.** Press **Scan all sources** (Inbox page or Dashboard) — or **Scan now**
   on the source's row in *Admin → Sources*. New documents appear in the **Review Inbox**,
   ready to rename, categorize and file.

> Tip: give each scanner its own subfolder (e.g. `/sources/canon-r50`) so you can tell where a
> document came from and monitor sources independently.

## The Scan-to-Shelf workflow

1. Scan the paper record — your scanner saves a PDF into the inbox folder
2. In **Scan to Shelf**, pick the file, give it a title, project, type, lot/part number
3. Print a QR label (Avery 5160/5163 PDF) and stick it on the paper original
4. Assign the paper copy to a numbered box
5. Nestbox moves the PDF to its permanent electronic location and starts monitoring it

Scanning the QR label later opens the record in Nestbox — including which box holds the paper.

## Architecture

Four containers: PostgreSQL 16 · FastAPI backend · a worker (scheduled scans & integrity
checks) · React frontend behind nginx. All state lives in Postgres; files are never modified
except by the wizard's deliberate move step.

```
records ── file_events ── alerts          audit_log (append-only, DB-enforced)
   │                                      users / sources / projects / record_types
 boxes    duplicate_groups   scan_runs    settings (cloud credentials encrypted at rest)
```

## Development

```bash
docker compose -f docker-compose.dev.yml up -d --build   # full stack
cd backend && pip install -r requirements.txt && pytest  # backend tests
cd frontend && npm install && npm run dev                # frontend dev server (proxies to :8000)
```

API docs: http://localhost:8000/docs

## Security notes

- Passwords are bcrypt-hashed; sessions are httpOnly cookies (no tokens in the browser)
- Cloud client secrets and refresh tokens are Fernet-encrypted with a key derived from `SECRET_KEY`
- `DELETE` is rejected by database triggers on all record tables; the audit log is immutable
- Nestbox is designed for use on a trusted LAN; put it behind a reverse proxy with TLS if you
  expose it beyond one

## License

Apache 2.0 — free for any organization to use. See [LICENSE](LICENSE).

Learn more at [redhendev.com](https://redhendev.com) — built by
[Red Hen Admin](https://redhenadmin.com) for document control and recordkeeping teams.
