# Connecting Nestbox to OneDrive / SharePoint

Nestbox reads (and, for the wizard, writes) your Microsoft 365 files through Microsoft
Graph using an **app-only** connection: a Global Administrator approves it once, and
scheduled scans then run unattended — no user has to stay signed in.

You need: a Microsoft 365 tenant and a Global Administrator account (~10 minutes).

## 1. Register the app in Entra ID

1. Go to https://entra.microsoft.com → **Identity → Applications → App registrations → New registration**
2. Name: `Nestbox`
3. Supported account types: **Accounts in this organizational directory only** (single tenant)
4. Redirect URI: leave empty → **Register**
5. On the Overview page, copy the **Application (client) ID** and **Directory (tenant) ID**

## 2. Grant permissions

1. **API permissions → Add a permission → Microsoft Graph → Application permissions**
2. Add: `Files.ReadWrite.All` and `Sites.ReadWrite.All`
3. Click **Grant admin consent for <your org>** (requires the admin role)

> `ReadWrite` (not just `Read`) is needed only so the Scan-to-Shelf wizard can file PDFs
> into OneDrive/SharePoint destinations. If you'll only ever scan/monitor, `Files.Read.All`
> + `Sites.Read.All` also work — the wizard's cloud destinations then won't.

## 3. Create a client secret

1. **Certificates & secrets → New client secret** — pick an expiry your policy allows
2. Copy the secret **Value** immediately (it's shown only once)

## 4. Enter the values in Nestbox

**Admin → Settings → Microsoft 365**: paste the Tenant ID, Client ID, and secret → Save.
The secret is encrypted at rest and never returned to the browser.

## 5. Add sources

**Admin → Sources → Add source → OneDrive / SharePoint**:

- For a **SharePoint document library**: type a keyword from the site name into *Find a
  drive* and pick the library.
- For a **user's OneDrive**: type their email address.
- Optionally limit to a folder path inside the drive (e.g. `Quality/Records`).

Press **Test**, then run a scan from the Dashboard.

## Renewal

Client secrets expire (max 24 months). Put a reminder in your calendar; when it expires,
create a new secret and paste it into Settings. Scans fail with a clear auth error until
you do.
