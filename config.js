/**
 * config.js — application configuration (v29).
 *
 * There is exactly ONE OAuth Client ID for this app and it lives here, in the
 * page's own public code. That is correct, not a leak: a Client ID only names
 * the APPLICATION to Google — it is not a secret and it never identifies the
 * person backing up. Anyone who shares the app's URL gets the same Client ID;
 * per user there is only the token, which the user's own sign-in produces,
 * which this app never writes down, and whose scope (drive.file) lets it see
 * nothing but files this app itself created in that one user's Drive.
 *
 * Fill this in once (site owner): create the OAuth client in
 * console.cloud.google.com → Credentials → OAuth client ID → Web
 * application → Authorised JavaScript origin https://choochatgpt.github.io →
 * paste the value below. Everything else is per-visitor: the export panel's
 * "Connect Google Drive" button runs Google's own sign-in, the current user
 * picks their own account, and their backups go to a "Notes Backup" folder in
 * THEIR Drive only. While your Google Cloud project is still in Testing mode,
 * only accounts you listed as test users can connect; move the consent screen
 * to Production (or add them as test users) to open it up. Empty string =
 * Google Drive backup is not configured yet.
 */
globalThis.NOTES_APP_CONFIG = { driveClientId: "" };