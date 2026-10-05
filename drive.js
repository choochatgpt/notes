/**
 * drive.js — the optional second copy: after the one-tap PC sync succeeds, the
 * same bundle (backup CSV + every photo/video) is zipped and uploaded to the
 * user's own Google Drive, into an app-created "Notes Backup" folder, one ZIP
 * per sync: notes-backup-<exportId>.zip.
 *
 * Requested 2026-10-05 ("what if we can save the photos/files to onedrive and
 * google drive?"). The user first imagined typing a Google ID and password;
 * that is never acceptable — the only legitimate route is OAuth, and for a
 * static Pages app the fitting shape is Google Identity Services' token
 * client: the user consents on GOOGLE's own window, and this app receives a
 * short-lived access token (about an hour) that stays in memory only and is
 * never persisted. No password ever reaches this code path.
 *
 * THE CLIENT ID (v29). Google requires an OAuth "client" naming the app. There
 * is exactly ONE for this application and it ships in config.js (a Client ID
 * is public by design — it names the app, not any user, so it lives in the
 * public page code). The backup is PER USER: each visitor taps "Connect
 * Google Drive", picks their own account in GOOGLE's window, and the token
 * that comes back is theirs alone — so every account gets its own private
 * "Notes Backup" folder in its own My Drive and users can never see or
 * overwrite each other's backups. A device may still override the configured
 * id in localStorage: notes.drive.client — useful for trying a new client id
 * before a redeploy; it always wins, and absent/garbage reads fall back to
 * config.js. The scope is exactly drive.file, so Google only shows this app
 * the files it created in the connected account's Drive — nothing else.
 *
 * THE UPLOAD is resumable, not multipart: Drive documents a hard 5 MB cap on
 * multipart requests, and a real bundle of phone photos sails past that. The
 * resumable dance is two calls: a metadata-only POST that answers with a
 * one-time session URI in its Location header (Google exposes that header
 * cross-origin, which is what makes browser-only resumable uploads possible),
 * then one PUT of the whole zip to that session URI.
 *
 * THE ZIP is built here (store-only: no compression, the bytes are mostly
 * JPEGs that will not compress) because a browser has no zip primitive and a
 * dependency would contradict this app's no-build, no-dependency shape.
 *
 * This file is DOM-free apart from ensureGis()'s script tag, and imports
 * nothing, so the exact code that ships runs unchanged in Node tests
 * (tests/drive.test.mjs stubs global.fetch).
 */

export const DRIVE_FOLDER = "Notes Backup";
export const DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file";
export const FOLDER_MIME = "application/vnd.google-apps.folder";
export const FILES_URL = "https://www.googleapis.com/drive/v3/files";
export const UPLOAD_URL =
  "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable"
  + "&fields=id,name,parents";
export const CLIENT_KEY = "notes.drive.client";
export const BACKUP_SCHEMA = "notes.drive.backup/1";
// The EOCD entry-count field is uint16; refusing earlier beats emitting a
// zip every reader would misread.
export const MAX_ZIP_ENTRIES = 65535;

/* ----------------------------------------------------------------- client id */

export function sanitizeClientId(raw) {
  // Client IDs are [A-Za-z0-9.-] with hyphens; anything else is paste dirt.
  return String(raw || "").replace(/[^A-Za-z0-9._-]/g, "");
}

/** The app-config default from config.js ("" there = Drive not configured). */
function configClientId() {
  const cfg = globalThis.NOTES_APP_CONFIG;
  return cfg && typeof cfg.driveClientId === "string" ? cfg.driveClientId : "";
}

export function getClientId() {
  // A device override wins (set via console for testing a new client id); a
  // stored value that sanitizes to nothing is treated as absent so paste dirt
  // cannot hide a working configured default.
  try {
    const stored = sanitizeClientId(localStorage.getItem(CLIENT_KEY) || "");
    if (stored) return stored;
  } catch { }
  return sanitizeClientId(configClientId());
}

export function setClientId(value) {
  try { localStorage.setItem(CLIENT_KEY, sanitizeClientId(value)); return true; }
  catch { return false; }
}

export function clearClientId() {
  try { localStorage.removeItem(CLIENT_KEY); return true; } catch { return false; }
}

export function hasClientId() { return getClientId().length > 0; }

/* ----------------------------------------------------------------- zip bits */

/** Shared byte helper, mirroring sync.js's — the CSV enters the zip as UTF-8. */
export function utf8(text) {
  return new TextEncoder().encode(text);
}


// CRC-32 (reflected, poly 0xEDB88320) — the table is built once at load.
const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    table[n] = c >>> 0;
  }
  return table;
})();

/** Known vector: crc32(utf8("123456789")) === 0xCBF43926. */
export function crc32(bytes) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;  // JS bitwise is signed; the field is not.
}

/**
 * The zip timestamp format: 16 bits of time, 16 bits of date, second
 * resolution (the stored seconds are halved), years since 1980. UTC getters
 * keep the bytes deterministic for a given instant across time zones, and a
 * pre-1980 clock (a device with a dead battery) clamps to 1980 rather than
 * wrapping to a nonsense year.
 */
export function dosDateTime(when) {
  const d = when instanceof Date ? when : new Date(when || Date.now());
  const year = Math.max(d.getUTCFullYear(), 1980);
  const date = ((year - 1980) << 9) | ((d.getUTCMonth() + 1) << 5) | d.getUTCDate();
  const time = (d.getUTCHours() << 11) | (d.getUTCMinutes() << 5) | (d.getUTCSeconds() >> 1);
  return { time, date };
}

function concat(chunks) {
  let total = 0;
  for (const chunk of chunks) total += chunk.length;
  const out = new Uint8Array(total);
  let at = 0;
  for (const chunk of chunks) { out.set(chunk, at); at += chunk.length; }
  return out;
}

/**
 * buildZip([{name, bytes}], {when}) -> Uint8Array
 * Store-only zip: local header + raw bytes per entry, one central directory,
 * one end-of-central-directory record. No directory entries (readers create
 * intermediates implicitly), no data-descriptor flag (sizes are known before
 * the header is written — that flag is for streaming writers only), UTF-8
 * filename flag on every entry so non-ASCII media names survive.
 */
export function buildZip(entries, opts = {}) {
  if (entries.length > MAX_ZIP_ENTRIES) {
    const error = new Error(`ZIP_TOO_MANY_ENTRIES: ${entries.length}`);
    error.code = "ZIP_TOO_MANY_ENTRIES";
    throw error;
  }
  const { time, date } = dosDateTime(opts.when || new Date());
  const encoder = new TextEncoder();
  const chunks = [];
  const central = [];
  let offset = 0;
  for (const entry of entries) {
    const name = encoder.encode(entry.name);
    const bytes = entry.bytes;
    const crc = crc32(bytes);
    const local = new Uint8Array(30 + name.length);
    const v = new DataView(local.buffer);
    v.setUint32(0, 0x04034b50, true);
    v.setUint16(4, 20, true);        // version needed to extract
    v.setUint16(6, 0x0800, true);    // flags: UTF-8 names, no data descriptor
    v.setUint16(8, 0, true);         // method 0 = store
    v.setUint16(10, time, true);
    v.setUint16(12, date, true);
    v.setUint32(14, crc, true);
    v.setUint32(18, bytes.length, true);   // compressed == stored
    v.setUint32(22, bytes.length, true);
    v.setUint16(26, name.length, true);
    v.setUint16(28, 0, true);        // extra length
    local.set(name, 30);
    chunks.push(local, bytes);
    central.push({ name, crc, size: bytes.length, offset });
    offset += local.length + bytes.length;
  }
  const cdStart = offset;
  for (const record of central) {
    const cd = new Uint8Array(46 + record.name.length);
    const v = new DataView(cd.buffer);
    v.setUint32(0, 0x02014b50, true);
    v.setUint16(4, 20, true);        // version made by (plain MS-DOS)
    v.setUint16(6, 20, true);        // version needed
    v.setUint16(8, 0x0800, true);
    v.setUint16(10, 0, true);
    v.setUint16(12, time, true);
    v.setUint16(14, date, true);
    v.setUint32(16, record.crc, true);
    v.setUint32(20, record.size, true);
    v.setUint32(24, record.size, true);
    v.setUint16(28, record.name.length, true);
    v.setUint16(30, 0, true);        // extra
    v.setUint16(32, 0, true);        // comment
    v.setUint16(34, 0, true);        // disk number start
    v.setUint16(36, 0, true);        // internal attributes
    v.setUint32(38, 0, true);        // external attributes
    v.setUint32(42, record.offset, true);
    cd.set(record.name, 46);
    chunks.push(cd);
    offset += cd.length;
  }
  const eocd = new Uint8Array(22);
  const v = new DataView(eocd.buffer);
  v.setUint32(0, 0x06054b50, true);
  v.setUint16(4, 0, true);           // this disk
  v.setUint16(6, 0, true);           // disk with central directory
  v.setUint16(8, central.length, true);
  v.setUint16(10, central.length, true);
  v.setUint32(12, offset - cdStart, true);
  v.setUint32(16, cdStart, true);
  v.setUint16(20, 0, true);          // comment length
  chunks.push(eocd);
  return concat(chunks);
}

/* --------------------------------------------------------------------- api */

/**
 * One Drive request, same response discipline as sync.js's api(): the body is
 * always read as text and JSON-parsed once, so Node's scripted fetch needs to
 * supply nothing but {status, text, headers}. `headers` is passed through as
 * a {get(name)} shim because the resumable initiation answers in the
 * Location header, not the body.
 */
function gapiRequest(method, url, { token, body, contentType, extraHeaders } = {}) {
  const headers = { "Authorization": "Bearer " + (token || "") };
  if (contentType) headers["Content-Type"] = contentType;
  if (extraHeaders) {
    for (const name of Object.keys(extraHeaders)) headers[name] = extraHeaders[name];
  }
  const init = { method, headers };
  if (body !== undefined && body !== null) init.body = body;
  return fetch(url, init).then(response => {
    const responseHeaders = response.headers;
    return response.text().then(text => {
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text.slice(0, 400) }; }
      return {
        status: response.status,
        data,
        headers: { get: name => responseHeaders ? responseHeaders.get(name) : null }
      };
    });
  });
}

/* ------------------------------------------------------------------- folder */

export function findBackupFolder(token) {
  const q = `name='${DRIVE_FOLDER}' and mimeType='${FOLDER_MIME}' and trashed=false`;
  return gapiRequest("GET",
    `${FILES_URL}?q=${encodeURIComponent(q)}&fields=${encodeURIComponent("files(id,name)")}`,
    { token }
  ).then(r => {
    if (r.status !== 200) {
      const error = new Error(`folder list failed (${r.status})`);
      error.code = "LIST_FAILED";
      throw error;
    }
    const files = (r.data && r.data.files) || [];
    const hit = files.find(file => file.name === DRIVE_FOLDER && file.id);
    return hit ? hit.id : null;
  });
}

export function createBackupFolder(token) {
  return gapiRequest("POST", FILES_URL, {
    token,
    body: JSON.stringify({
      name: DRIVE_FOLDER,
      mimeType: FOLDER_MIME,
      appProperties: { notesBackup: "1" }
    }),
    contentType: "application/json; charset=UTF-8"
  }).then(r => {
    if (r.status !== 200 && r.status !== 201) {
      const error = new Error(`folder create failed (${r.status})`);
      error.code = "CREATE_FAILED";
      throw error;
    }
    return r.data && r.data.id;
  });
}

export function ensureBackupFolder(token) {
  return findBackupFolder(token).then(id => id || createBackupFolder(token));
}

/* ------------------------------------------------------------------- upload */

/**
 * uploadBackup({token, zipBytes, name, folderId})
 *   -> {fileId, name, folderId, bytes}
 * Resumable upload, single PUT (the whole zip in one shot — no chunking, the
 * relay already capped every file well under what one PUT wants to carry).
 */
export async function uploadBackup({ token, zipBytes, name, folderId }) {
  const init = await gapiRequest("POST", UPLOAD_URL, {
    token,
    body: JSON.stringify({
      name,
      mimeType: "application/zip",
      parents: [folderId],
      appProperties: { notesBackup: "1", schema: BACKUP_SCHEMA }
    }),
    contentType: "application/json; charset=UTF-8",
    extraHeaders: {
      "X-Upload-Content-Type": "application/zip",
      "X-Upload-Content-Length": String(zipBytes.length)
    }
  });
  if (init.status === 401) {
    const error = new Error("upload init rejected the sign-in (401)");
    error.code = "TOKEN_EXPIRED";
    throw error;
  }
  if (init.status !== 200) {
    const error = new Error(`upload init failed (${init.status})`);
    error.code = "UPLOAD_FAILED";
    throw error;
  }
  const session = init.headers.get("location");
  if (!session) {
    const error = new Error("the browser hid the upload session URI (Location)");
    error.code = "NO_SESSION";
    throw error;
  }
  const done = await gapiRequest("PUT", session, {
    token,
    body: zipBytes,
    contentType: "application/zip"
  });
  if (done.status !== 200 && done.status !== 201) {
    const error = new Error(`upload failed (${done.status})`);
    error.code = done.status === 401 ? "TOKEN_EXPIRED" : "UPLOAD_FAILED";
    throw error;
  }
  return {
    fileId: (done.data && done.data.id) || "",
    name: (done.data && done.data.name) || name,
    folderId,
    bytes: zipBytes.length
  };
}

/* ------------------------------------------------------------------ errors */

/** App-voice sentences for every coded failure this module throws. */
export function friendlyDriveError(error) {
  if (error?.code === "NOT_CONFIGURED") {
    return "Google Drive copy is off — paste your Client ID above to turn it on.";
  }
  if (error?.code === "POPUP_BLOCKED") {
    return "Google's sign-in window was blocked — allow popups for this site, "
      + "then tap the sync button again.";
  }
  if (error?.code === "POPUP_CLOSED") {
    return "the Google sign-in window closed before it finished — tap the sync "
      + "button to try again.";
  }
  if (error?.code === "ACCESS_DENIED") {
    return "Google sign-in was not completed — the Drive copy needs your OK on "
      + "Google's consent screen.";
  }
  if (error?.code === "TOKEN_EXPIRED") {
    return "Google sign-in expired — tap the sync button again to reconnect.";
  }
  if (error?.code === "NO_SESSION") {
    return "the browser hid Google's upload session — the PC copy succeeded; "
      + "tap the sync button to try the Drive copy again.";
  }
  if (error instanceof TypeError) {
    return "couldn't reach Google (" + (error.message || "network error")
      + ") — check that you are online. The PC copy already succeeded.";
  }
  if (error?.code === "LIST_FAILED" || error?.code === "CREATE_FAILED"
    || error?.code === "UPLOAD_FAILED") {
    const base = `Google Drive refused the request (${error.message})`;
    return /\(403\)/.test(error.message)
      ? base + " — the Drive account may be out of space, or consent was withdrawn."
      : base + " — tap the sync button to try again.";
  }
  if (error?.code === "ZIP_TOO_MANY_ENTRIES") {
    return "too many photos/files for one zip — export in batches from the photo viewer.";
  }
  if (error?.code === "NO_GIS") {
    return "Google's sign-in could not load — check that you are online, then "
      + "tap the sync button again.";
  }
  return error?.message || String(error);
}

/* ---------------------------------------------------------------------- gis */

/**
 * Google Identity Services' oauth2 namespace, found in whichever global the
 * runtime has: window in the browser, globalThis in the Node tests (which
 * stub it to exercise requestToken without a real popup).
 */
function gisObject() {
  const g = globalThis.google;
  return (g && g.accounts && g.accounts.oauth2) || null;
}

let gisPromise = null;

/**
 * The GIS library, loaded once on demand. The <script> tag is only injected
 * when the user has actually configured the Drive copy — until then the app
 * makes no request to Google at all.
 */
export function ensureGis() {
  const ready = gisObject();
  if (ready) return Promise.resolve(ready);
  if (gisPromise) return gisPromise;
  gisPromise = new Promise((resolve, reject) => {
    if (typeof document === "undefined") {
      const error = new Error("Google sign-in is not available here");
      error.code = "NO_GIS";
      reject(error);
      return;
    }
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.onload = () => {
      const oauth2 = gisObject();
      if (oauth2) { resolve(oauth2); return; }
      gisPromise = null;
      const error = new Error("Google sign-in script loaded without oauth2");
      error.code = "NO_GIS";
      reject(error);
    };
    script.onerror = () => {
      gisPromise = null;
      const error = new Error("the Google sign-in script failed to load");
      error.code = "NO_GIS";
      reject(error);
    };
    document.head.appendChild(script);
  });
  return gisPromise;
}

/**
 * requestToken(clientId) -> Promise<accessToken>
 * Opens GOOGLE's own sign-in/consent window. Call this only from inside a
 * user gesture (the sync tap) — browsers refuse the popup otherwise, and a
 * surprise popup is not this app's style anyway.
 */
export async function requestToken(clientId) {
  const oauth2 = await ensureGis();
  return new Promise((resolve, reject) => {
    let settled = false;
    const client = oauth2.initTokenClient({
      client_id: clientId,
      scope: DRIVE_SCOPE,
      callback: response => {
        // GIS answers failures (access_denied …) through the same callback;
        // whichever channel speaks first wins.
        if (settled) return;
        settled = true;
        if (response && response.access_token) { resolve(response.access_token); return; }
        const error = new Error((response && response.error) || "sign-in did not complete");
        error.code = "ACCESS_DENIED";
        reject(error);
      },
      error_callback: err => {
        if (settled) return;
        settled = true;
        const error = new Error(
          err && err.type ? `google sign-in: ${err.type}` : "google sign-in popup failed");
        error.code = (err && err.type === "popup_failed_to_open")
          ? "POPUP_BLOCKED" : "POPUP_CLOSED";
        reject(error);
      }
    });
    client.requestAccessToken();
  });
}
