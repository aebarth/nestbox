# Where your files live — choosing storage for Nestbox

Nestbox watches **folders**, not clouds directly. This guide explains the two levels of
storage and walks through getting scanning working with each common option: a plain local
folder, an SMB network share (NAS), or a sync-client folder (OneDrive / Dropbox / Google
Drive).

## The model: install mounts + per-org subfolders

Docker bind mounts are fixed at container start, so storage has exactly two levels:

1. **Install mounts** — three host folders you choose once in `.env`, mounted into the
   containers. This is your *substrate*: a local folder, a NAS share, or a sync-client
   folder.

   | `.env` variable | Mounted as | Holds |
   |---|---|---|
   | `NESTBOX_SOURCES_PATH` | `/sources` | Permanent document storage |
   | `NESTBOX_INBOX_PATH` | `/inbox` | Scanner drop folder (unfiled PDFs) |
   | `NESTBOX_BACKUP_PATH` | `/backups` | Backup files (default `./backups`) |

2. **Per-org locations** — each organization automatically gets its own subtree under the
   mounts: `/inbox/<org-slug>`, `/sources/<org-slug>`, `/backups/<org-slug>`. Admins can
   view, change and test these at any time in **Admin → Storage**; RLS isolates the metadata
   and the path prefix isolates the files.

Cloud Drive *sources* (Google Drive / OneDrive via API) are a third option that needs no
host folder at all — see [google-drive-setup.md](google-drive-setup.md) and
[microsoft-365-setup.md](microsoft-365-setup.md).

## Options at a glance

| Option | Inbox | Sources | Backups | How it works |
|---|---|---|---|---|
| Local folder on the Docker host | ✅ | ✅ | ✅ | Default. `.env` points at a local folder |
| Network drive (SMB/NFS, e.g. NAS) | ✅ | ✅ | ✅ | Mount the share on the **host**; point `.env` at it |
| Dropbox (desktop sync client) | ✅ | ✅ | ✅ | Point `.env` at a subfolder of your Dropbox |
| OneDrive / SharePoint (sync client) | ✅ | ✅ | ✅ | Point `.env` at a subfolder of your OneDrive |
| Google Drive (sync client) | ✅ | ✅ | ✅ | Point `.env` at a subfolder of your Drive for Desktop folder |
| Google Drive / OneDrive **API sources** | — | ✅ (per-org credentials) | — | No host folder; per-org API source in Admin → Sources |

## Option 1: plain local folder (default)

The fastest start. On the machine running Docker, create a folder and point `.env` at it:

```bash
mkdir -p ~/nestbox/{sources,inbox,backups}
```

```ini
# .env
NESTBOX_SOURCES_PATH=~/nestbox/sources
NESTBOX_INBOX_PATH=~/nestbox/inbox
NESTBOX_BACKUP_PATH=~/nestbox/backups
```

Then `docker compose up -d`. Your scanner writes into the inbox folder (see the README's
[Connecting a scanner](../README.md#connecting-a-scanner) for the Canon R50 example).

## Option 2: SMB network share (NAS)

The most common production setup: documents live on a Synology/QNAP/other NAS, and Docker
runs on any machine that can reach it. **Mount the share on the host first**, then point
`.env` at the mounted path.

### macOS

1. Finder → **Go → Connect to Server** (`⌘K`) → `smb://<nas>/nestbox` → connect (add to Keychain
   for automatic re-mount).
2. The share appears under `/Volumes/<share>`.
3. `.env`:

   ```ini
   NESTBOX_SOURCES_PATH=/Volumes/nas/nestbox/sources
   NESTBOX_INBOX_PATH=/Volumes/nas/nestbox/inbox
   NESTBOX_BACKUP_PATH=/Volumes/nas/nestbox/backups
   ```

### Windows

1. **This PC → Map network drive** → pick a letter (e.g. `Z:`) → `\\<nas>\nestbox` → tick
   *Reconnect at sign-in*.
2. Docker Desktop: **Settings → Resources → File sharing** — make sure the mapped drive is
   shared with Docker, then *Apply & restart*.
3. `.env`:

   ```ini
   NESTBOX_SOURCES_PATH=Z:/nestbox/sources
   NESTBOX_INBOX_PATH=Z:/nestbox/inbox
   NESTBOX_BACKUP_PATH=Z:/nestbox/backups
   ```

### Linux

```bash
sudo mkdir -p /mnt/nas
sudo mount -t cifs //<nas>/nestbox /mnt/nas -o username=<user>,password=<pass>
# persist across reboots: add the same line to /etc/fstab
```

```ini
NESTBOX_SOURCES_PATH=/mnt/nas/nestbox/sources
NESTBOX_INBOX_PATH=/mnt/nas/nestbox/inbox
NESTBOX_BACKUP_PATH=/mnt/nas/nestbox/backups
```

**Then get scanning working:** your scanner (e.g. Canon R50) points at the *same* share —
`\\<nas>\nestbox\inbox` in its SMB scan-to-folder settings. Both Docker and the scanner read
and write the NAS folder; no copies involved.

## Option 3: OneDrive (sync-client root)

1. Install the **OneDrive** app on the Docker host and sign in (personal or Work/School).
2. Create a `Nestbox` folder inside your OneDrive with `sources`, `inbox` and `backups`
   subfolders.
3. **Important:** right-click the `Nestbox` folder → **Always keep on this device**. Files
   that are cloud-only ("Online only") may not be visible to Docker's bind mount.
4. `.env`:

   | OS | Path |
   |---|---|
   | Windows | `C:/Users/<you>/OneDrive/Nestbox/sources` (or `OneDrive - <company>`) |
   | macOS | `~/Library/CloudStorage/OneDrive - <account>/Nestbox/sources` |

   ```ini
   NESTBOX_SOURCES_PATH=~/Library/CloudStorage/OneDrive - Contoso/Nestbox/sources
   NESTBOX_INBOX_PATH=~/Library/CloudStorage/OneDrive - Contoso/Nestbox/inbox
   NESTBOX_BACKUP_PATH=~/Library/CloudStorage/OneDrive - Contoso/Nestbox/backups
   ```

5. `docker compose up -d`. Scanned PDFs dropped into the inbox folder sync to OneDrive
   automatically; backups ride along in the same way.

> Linux has no official OneDrive app — use an SMB share on your NAS, or mount OneDrive with
> [rclone](https://rclone.org/onedrive/) (`rclone mount`) and point `.env` at the mount.
>
> Prefer the **API** over sync for large libraries? Register a per-org OneDrive/SharePoint
> source in Admin → Sources instead — see [microsoft-365-setup.md](microsoft-365-setup.md).

## Option 4: Dropbox (sync-client root)

Same shape as OneDrive:

1. Install the Dropbox app, sign in, create `Dropbox/Nestbox/{sources,inbox,backups}`.
2. Set the folder to **Always export** / *Always keep on this device* so files are real local
   files (Docker can't see Dropbox's cloud-only placeholders).
3. `.env` points at `~/Dropbox/Nestbox/…` (macOS: `~/Library/CloudStorage/Dropbox/Nestbox/…`).

## Option 5: Google Drive

- **Sync client:** install [Drive for Desktop](https://www.google.com/drive/download/), create
  `My Drive/Nestbox/{sources,inbox,backups}`, set it to *Always export*, and point `.env` at
  the same paths as above.
- **API source (recommended for large libraries):** no host folder needed — add a Google
  Drive source per organization in Admin → Sources with its own free credentials:
  [google-drive-setup.md](google-drive-setup.md).

## Managing locations after setup (Admin → Storage)

- **Install mounts** are shown read-only (container path + host path + health). Changing them
  is a `.env` edit — see below.
- **Per-org locations** (inbox / sources root / backup path) can be changed per organization,
  with a health check and free-space reading per location and a **Test** button that runs a
  real write probe. Paths must stay under the matching install mount.
- Guardrails:
  - Changing an org's **sources root** while it has records requires explicit confirmation —
    existing files are **not** moved; record paths would go stale (run a scan to
    re-discover them).
  - Changing the **inbox** warns if the old folder still holds unfiled PDFs.
  - Changing the **backup path** never deletes existing dumps.
- Org-level backup zips (run / list / restore) live on the **Backups** page; full-database
  backups and reset live under **Admin → Database**.

## Changing the install roots (`.env`)

1. Move your files to the new host folder first (the app does not move them for you).
2. Edit `NESTBOX_SOURCES_PATH` / `NESTBOX_INBOX_PATH` / `NESTBOX_BACKUP_PATH` in `.env`.
3. Recreate the containers: `docker compose up -d` (bind mounts are fixed at start).
4. If any per-org location now points outside its new mount, fix it in **Admin → Storage** —
   health checks will flag anything missing.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Location shows **missing** in Admin → Storage | Folder doesn't exist on the host yet, or the share isn't mounted. Create/mount it, then press **Test** (creates the folder if allowed). |
| Location shows **read-only** | Permissions on the host folder/share don't allow writing to the Docker user. Fix ownership/ACLs on the host. |
| Scanner files never appear | The scanner writes somewhere else — check its SMB settings; or the sync client hasn't synced (check "Always keep on this device"). Then press **Scan now** on the inbox source. |
| Files visible in Finder/Explorer but not in Nestbox | Cloud-only placeholder files (OneDrive/Dropbox/Drive). Set the folder to always-keep/always-export and re-sync. |
| Windows mapped drive not visible to containers | Enable the drive under Docker Desktop → Settings → Resources → File sharing, then restart Docker. |

## Related docs

- [README — Connecting a scanner](../README.md#connecting-a-scanner) (Canon R50 example)
- [google-drive-setup.md](google-drive-setup.md) · [microsoft-365-setup.md](microsoft-365-setup.md)
