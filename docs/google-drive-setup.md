# Connecting Nestbox to Google Drive

Nestbox connects to Google Drive with an OAuth app **you** register (free). One person
authorizes the connection in a browser; Nestbox stores an encrypted refresh token and
scans unattended after that.

## 1. Create the OAuth client

1. Go to https://console.cloud.google.com → create (or select) a project, e.g. `nestbox`
2. **APIs & Services → Library** → enable **Google Drive API**
3. **APIs & Services → OAuth consent screen**:
   - If your organization uses **Google Workspace**: choose **Internal**. Done — no
     verification, no warnings.
   - Personal / consumer Gmail: choose **External**, fill in the app name and your email.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**
   - Authorized redirect URI: `http://localhost:3000/api/connectors/google/callback`
     (replace `http://localhost:3000` with your Nestbox URL if different — it must match
     `APP_BASE_URL` in your `.env`)
5. Copy the **Client ID** and **Client secret**

## 2. External apps: publish it

If you chose **External** in step 3: on the OAuth consent screen page press
**Publish app**. While an app is in *Testing*, Google expires refresh tokens after
7 days and your scans would stop weekly.

After publishing, the consent page shows an "unverified app" warning — click
**Advanced → Go to Nestbox (unsafe)**. That's expected for a private self-hosted app;
Google's verification review is only needed for public apps.

## 3. Enter the values in Nestbox

**Admin → Settings → Google Drive**: paste the Client ID and secret → Save.

## 4. Add the source and connect

1. **Admin → Sources → Add source → Google Drive**. Optionally paste a folder ID
   (the part after `/folders/` in the folder's Drive URL) to track just that subtree.
2. Save, then press **Connect Google** on the source row and approve the consent screen.
3. Press **Test**, then run a scan from the Dashboard.

## Notes

- The account that connects needs access to the files you want tracked; scans see what
  that account sees (shared drives included).
- Revoking access: https://myaccount.google.com/permissions — then reconnect in Nestbox.
