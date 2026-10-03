/**
 * sync.js — one-tap sync: the app pushes a whole export (backup CSV + every
 * photo/video) straight to the PC through the PRIVATE relay repo.
 *
 * The user asked for this on 2026-10-03 ("the moment i click the button export
 * notes/reminders to email, it must sync all notes and jpg to my PC. just like
 * how jscan does it"), and the transport is the one the client authorised for
 * JScan on 2026-09-21 (jscanner LEARN_UPLOAD_DESIGN_REVIEW.md), because a
 * static Pages app has exactly one way to move bytes without a server:
 *
 * THE TOKEN — the user's OWN fine-grained PAT, typed in once and kept in this
 * device's localStorage only. It is NEVER placed in the source: this app is
 * served from public GitHub Pages, so anything in the JS is readable from page
 * source, and a token there would hand the private relay to the world. The
 * token scopes to the one repo, Contents: Read and write — nothing more.
 *
 * THE BYTES ride on the orphan branch `notes-inbox` of choochatgpt/ask-ai-relay
 * (never the public notes repo), one commit per export:
 *
 *     <exportId>/manifest.json   schema notes.sync.bundle/1, per-file sha256s
 *     <exportId>/backup.csv      the text-only backup CSV
 *     <exportId>/media/<name>    the photos/videos themselves
 *
 * GitHub's REST API cannot attach files to an issue, and the relay's existing
 * pickups read branches, so a branch serves both. The PC watcher
 * (tools/relay_pull_sync_inbox.py) verifies every sha256, archives the media,
 * emails the CSV through the same SMTP route the pasted inbox always used, and
 * then REWRITES the branch to a single parentless ack.json commit — the
 * rewrite is the cleanup, because a plain delete would leave the bytes
 * recoverable in GitHub history forever. checkPickedUp() reads that ack, which
 * is how the app can say the PC actually took the delivery.
 *
 * THE COMMIT IS A COMPARE-AND-SWAP LOOP, not a straight ref write. The branch
 * head is re-read immediately before every landing attempt because the PC may
 * rewrite the branch (or a second export may land) while this one's blobs are
 * still uploading; GitHub refuses a non-fast-forward ref update with 422. On a
 * lost race we re-read and rebuild — the blobs are already uploaded, so a
 * retry is three small JSON calls. `base_tree` is mandatory on every attempt:
 * omitting it would make the new tree REPLACE the branch contents and
 * silently drop the other side's data.
 *
 * This file is DOM-free so the exact code that ships runs unchanged in Node
 * tests (tests/sync.test.mjs stubs global.fetch).
 */

const REPO = "choochatgpt/ask-ai-relay";
const BRANCH = "notes-inbox";
const API = "https://api.github.com";
// Git's canonical empty tree: a parentless "bare" commit must reference it by
// SHA, because the API refuses to BUILD an empty tree ({"tree": []} is 422).
const EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904";
const TOKEN_KEY = "notes.sync.token";
// Matches the PC pickup's per-file cap: relay_pull_sync_inbox.py refuses
// anything larger, so refusing here saves the upload.
export const MAX_FILE_BYTES = 24 * 1024 * 1024;
export const MAX_CSV_BYTES = 10 * 1024 * 1024;
// How many times one export may lose the race for the branch head.
const MAX_REF_ATTEMPTS = 4;
export const BUNDLE_SCHEMA = "notes.sync.bundle/1";

/* ------------------------------------------------------------------ token */

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}
export function setToken(value) {
  try { localStorage.setItem(TOKEN_KEY, String(value || "").trim()); return true; }
  catch { return false; }
}
export function clearToken() {
  try { localStorage.removeItem(TOKEN_KEY); return true; } catch { return false; }
}
export function hasToken() { return getToken().length > 0; }

/* ------------------------------------------------------------------ bytes */

// Hand-rolled base64 so the file runs unchanged in the browser and in Node,
// where atob does not exist — a shim would mean the tested code is not the
// shipped code.
const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

export function b64(bytes) {
  let out = "";
  let i = 0;
  for (; i + 2 < bytes.length; i += 3) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
    out += B64[(n >> 18) & 63] + B64[(n >> 12) & 63] + B64[(n >> 6) & 63] + B64[n & 63];
  }
  const rem = bytes.length - i;
  if (rem === 1) {
    const a = bytes[i] << 16;
    out += B64[(a >> 18) & 63] + B64[(a >> 12) & 63] + "==";
  } else if (rem === 2) {
    const b = (bytes[i] << 16) | (bytes[i + 1] << 8);
    out += B64[(b >> 18) & 63] + B64[(b >> 12) & 63] + B64[(b >> 6) & 63] + "=";
  }
  return out;
}

export function utf8(text) {
  return new TextEncoder().encode(text);
}

function pause(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export function sha256hex(bytes) {
  // crypto.subtle needs a secure context: GitHub Pages is https, Node has it.
  return crypto.subtle.digest("SHA-256", bytes).then(buf => {
    const v = new Uint8Array(buf);
    let out = "";
    for (let i = 0; i < v.length; i++) out += (v[i] < 16 ? "0" : "") + v[i].toString(16);
    return out;
  });
}

function b64ToText(raw) {
  // The one direction Node lacks atob for; Buffer covers it.
  if (typeof atob === "function") {
    const binary = atob(raw);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return new TextDecoder().decode(bytes);
  }
  return new TextDecoder().decode(Buffer.from(raw, "base64"));
}

/* -------------------------------------------------------------------- api */

function api(method, path, body, tok) {
  const headers = {
    "Authorization": "Bearer " + (tok || getToken()),
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
  };
  const init = { method, headers };
  if (body !== undefined && body !== null) {
    headers["Content-Type"] = "application/json";
    init.body = typeof body === "string" ? body : JSON.stringify(body);
  }
  return fetch(API + path, init).then(response => {
    if (response.status === 204) return { status: 204, data: null };
    return response.text().then(text => {
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text.slice(0, 400) }; }
      return { status: response.status, data };
    });
  });
}

/* ------------------------------------------------------------------- misc */

export function newExportId() {
  // Date prefix so the PC archive sorts naturally; random suffix for uniqueness.
  const day = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const rand = new Uint8Array(4);
  crypto.getRandomValues(rand);
  let hex = "";
  for (let i = 0; i < rand.length; i++) hex += rand[i].toString(16).padStart(2, "0");
  return `${day}-${hex}`;
}

function utcStamp() {
  return new Date().toISOString().replace(/\.\d+Z$/, "Z");
}

/* ----------------------------------------------------------------- branch */

function bareCommit(tok) {
  return api("POST", `/repos/${REPO}/git/commits`,
    { message: "notes-inbox: initialise bare intake branch", tree: EMPTY_TREE, parents: [] }, tok
  ).then(r => {
    if (r.status !== 201) {
      // GitHub answers 403/404 -- not 401 -- when a fine-grained token is
      // valid but too narrow to touch the repo, so this failure almost
      // always means permissions, not the token itself.
      const error = new Error(`bare commit failed (${r.status})`);
      error.code = "TOKEN_SCOPE";
      throw error;
    }
    return r.data.sha;
  });
}

function ensureBranch(tok) {
  return api("GET", `/repos/${REPO}/git/ref/heads/${BRANCH}`, null, tok)
    .then(r => {
      if (r.status === 200) return r.data.object.sha;
      if (r.status !== 404) throw new Error(`cannot read branch (${r.status})`);
      return bareCommit(tok).then(sha =>
        api("POST", `/repos/${REPO}/git/refs`,
          { ref: `refs/heads/${BRANCH}`, sha }, tok
        ).then(c => {
          if (c.status !== 201) throw new Error(`cannot create branch (${c.status})`);
          return sha;
        }));
    });
}

/* ----------------------------------------------------------------- submit */

export function buildBundleManifest(meta, files) {
  return {
    schema: BUNDLE_SCHEMA,
    export_id: meta.exportId,
    app_version: meta.appVersion || "unknown",
    created_utc: utcStamp(),
    counts: meta.counts || null,
    files
  };
}

/**
 * submit({csv, media, meta}) -> {exportId, commit, bytes, mediaCount}
 *   csv    : the text-only backup CSV (buildBackupCsv output)
 *   media  : [{name, bytes, type}] — bytes as Uint8Array, names already
 *            sanitised by the caller (path separators stripped)
 *   meta   : {counts: {folders, notes, reminders}, appVersion}
 * Rejects before any network call when the token is missing or any file is
 * over the cap — a half-refused export must never have started uploading.
 */
export async function submit({ csv, media = [], meta = {} }) {
  const tok = getToken();
  if (!tok) {
    const error = new Error("NO_TOKEN");
    error.code = "NO_TOKEN";
    throw error;
  }
  const csvBytes = utf8(csv);
  if (csvBytes.length > MAX_CSV_BYTES) {
    const error = new Error("CSV_TOO_LARGE");
    error.code = "CSV_TOO_LARGE";
    throw error;
  }
  for (const item of media) {
    if (item.bytes.length > MAX_FILE_BYTES) {
      const error = new Error(`FILE_TOO_LARGE: ${item.name}`);
      error.code = "FILE_TOO_LARGE";
      error.name = item.name;
      throw error;
    }
  }

  const exportId = meta.exportId || newExportId();
  const names = ["backup.csv", ...media.map(item => `media/${item.name}`)];
  const payloads = [csvBytes, ...media.map(item => item.bytes)];
  const hashes = await Promise.all(payloads.map(bytes => sha256hex(bytes)));
  const files = names.map((path, i) => {
    const entry = { path, sha256: hashes[i], bytes: payloads[i].length };
    // Index 0 is the CSV; the media entries start at 1.
    if (i > 0 && media[i - 1].type) entry.type = media[i - 1].type;
    return entry;
  });
  const manifest = buildBundleManifest({ ...meta, exportId }, files);

  const bodyFiles = {
    "manifest.json": utf8(JSON.stringify(manifest, null, 2)),
    "backup.csv": csvBytes,
    ...Object.fromEntries(media.map(item => [`media/${item.name}`, item.bytes]))
  };

  await ensureBranch(tok);

  // Upload every byte as a blob first: the expensive part happens once, so a
  // lost ref race is retried with three small JSON calls, not re-uploads.
  const entries = await Promise.all(Object.keys(bodyFiles).map(relative =>
    api("POST", `/repos/${REPO}/git/blobs`,
      { content: b64(bodyFiles[relative]), encoding: "base64" }, tok
    ).then(r => {
      if (r.status !== 201) throw new Error(`blob ${relative} failed (${r.status})`);
      return { path: `${exportId}/${relative}`, mode: "100644", type: "blob", sha: r.data.sha };
    })
  ));

  function land(attempt) {
    return ensureBranch(tok).then(parent =>
      api("GET", `/repos/${REPO}/git/commits/${parent}`, null, tok).then(r => {
        if (r.status !== 200) throw new Error(`cannot read parent (${r.status})`);
        const body = { tree: entries };
        if (r.data.tree.sha !== EMPTY_TREE) body.base_tree = r.data.tree.sha;
        return api("POST", `/repos/${REPO}/git/trees`, body, tok).then(t => {
          if (t.status !== 201) throw new Error(`tree failed (${t.status})`);
          return t.data.sha;
        });
      }).then(tree =>
        api("POST", `/repos/${REPO}/git/commits`,
          { message: `notes sync: export ${exportId}`, tree, parents: [parent] }, tok
        ).then(c => {
          if (c.status !== 201) throw new Error(`commit failed (${c.status})`);
          return c.data.sha;
        })
      ).then(commit =>
        api("PATCH", `/repos/${REPO}/git/refs/heads/${BRANCH}`, { sha: commit }, tok)
          .then(r => {
            if (r.status === 200) return commit;
            if (r.status === 422 && attempt < MAX_REF_ATTEMPTS) {
              // Lost the race (the PC rewrote the branch, or a second export
              // landed mid-upload). Let the winner finish, re-read the head,
              // rebuild on top of what is actually there.
              return pause(250 * (attempt + 1)).then(() => land(attempt + 1));
            }
            throw new Error(`ref update failed (${r.status})`
              + (r.data && r.data.message ? `: ${r.data.message}` : ""));
          })
      )
    );
  }

  const commit = await land(0);
  return {
    exportId,
    commit: commit.slice(0, 12),
    mediaCount: media.length,
    bytes: Object.values(bodyFiles).reduce((sum, bytes) => sum + bytes.length, 0)
  };
}

/* -------------------------------------------------------------- pickup ack */

/**
 * checkPickedUp(exportId) -> {pickedUp, at} | {checked: false}
 * The PC's cleanup commit carries ack.json listing every processed export id;
 * reading it answers "did my backup reach the PC?" with no timer and no issue
 * comments.
 */
export async function checkPickedUp(exportId) {
  const tok = getToken();
  if (!tok || !exportId) return { checked: false };
  const ref = await api("GET", `/repos/${REPO}/git/ref/heads/${BRANCH}`, null, tok);
  if (ref.status !== 200) return { checked: false };
  const commit = await api("GET",
    `/repos/${REPO}/git/commits/${ref.data.object.sha}`, null, tok);
  if (commit.status !== 200) return { checked: false };
  const tree = await api("GET", `/repos/${REPO}/git/trees/${commit.data.tree.sha}`, null, tok);
  if (tree.status !== 200) return { checked: false };
  const ackEntry = (tree.data.tree || []).find(entry => entry.path === "ack.json");
  if (!ackEntry) return { pickedUp: false, at: null };
  const blob = await api("GET", `/repos/${REPO}/git/blobs/${ackEntry.sha}`, null, tok);
  if (blob.status !== 200 || blob.data.encoding !== "base64") return { checked: false };
  try {
    const ack = JSON.parse(b64ToText(blob.data.content));
    return { pickedUp: (ack.processed || []).includes(exportId), at: ack.at || null };
  } catch {
    return { checked: false };
  }
}
