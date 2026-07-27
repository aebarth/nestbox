# Changelog

## 1.0.0 — unreleased

Initial release.

- In-app PDF viewer (PDF.js — no browser plugin required)
- OCR / text extraction: embedded text layer via pypdf, Tesseract fallback for raw
  scans; full-text content search from the Files page
- Content-based near-duplicate detection: normalized-text fingerprints + trigram
  similarity catch the same paper document scanned more than once
- Auto-categorize onboarding flow: map folders to record types in bulk with fuzzy
  suggestions from folder names; mappings persist as rules applied to future scans
- Track scanned PDFs across local folders / network shares, Google Drive, and
  OneDrive/SharePoint (Microsoft Graph)
- Categorization: project, record type, lot number, part number, tags, notes
- Scheduled monitoring: rename/move/modify/delete detection with an alert
  review workflow; weekly content-integrity check
- Content-hash duplicate detection (provider checksums used for cloud files)
- Physical box tracking with QR label PDFs (Avery 5160/5163)
- Scan-to-Shelf wizard: inbox → categorize → label → box → file
- Reports (CSV + PDF): all records, missing, duplicates, uncategorized,
  retention due, box inventory
- Append-only audit trail enforced by Postgres triggers; no hard deletes
- Admin/user roles, bcrypt + httpOnly-cookie sessions
- Docker Compose deployment; prebuilt multi-arch images on ghcr.io
