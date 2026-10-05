/**
 * Tests for the Google Drive second-copy transport (drive.js).
 *
 *   node tests/drive.test.mjs [path-to-drive.js]
 *   Exit: 0 = all pass, 1 = one or more failures
 *
 * The module path defaults to ../source/drive.js (durable-mirror layout); pass
 * ../drive.js when running inside the flat published/active layout.
 *
 * global.fetch is stubbed with a scripted queue, so every claim below is made
 * against the exact JSON Google's Drive API would return -- including the
 * resumable initiation whose session URI arrives in the Location header, the
 * detail that decides whether a browser can do this at all. The GIS token
 * client is stubbed on globalThis.google, so the sign-in flow is exercised
 * without any popup or network.
 */
const resolved = new URL(process.argv[2] || "../source/drive.js", import.meta.url);
const {
  BACKUP_SCHEMA,
  CLIENT_KEY,
  DRIVE_FOLDER,
  DRIVE_SCOPE,
  MAX_ZIP_ENTRIES,
  buildZip,
  clearClientId,
  crc32,
  dosDateTime,
  ensureBackupFolder,
  friendlyDriveError,
  getClientId,
  hasClientId,
  requestToken,
  sanitizeClientId,
  setClientId,
  uploadBackup,
  utf8
} = await import(resolved.href);

// Node has no localStorage; the module looks it up at call time, so a
// Map-backed shim gives the client-id functions a real store (same trick as
// sync.test.mjs).
const idStore = new Map();
globalThis.localStorage = {
  getItem: key => (idStore.has(key) ? idStore.get(key) : null),
  setItem: (key, value) => idStore.set(key, String(value)),
  removeItem: key => idStore.delete(key)
};

let passed = 0;
let failed = 0;

function check(name, condition, extra) {
  if (condition) { passed++; console.log(`  ok  ${name}`); }
  else {
    failed++;
    console.log(`FAIL  ${name}${extra !== undefined ? ` -- ${extra}` : ""}`);
  }
}
function equal(name, actual, expected) {
  check(name, actual === expected, `got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`);
}

/* Scripted Google. Each entry is [method-regex, url-regex, responder].
 * responders receive the raw request body and return
 * {status, data, headers?} -- headers is a plain object keyed lowercase and
 * surfaces through response.headers.get(), which is how the resumable
 * session URI travels. */
let script = [];
let calls = [];
const REAL_FETCH = globalThis.fetch;

function installFetch() {
  globalThis.fetch = async (url, init = {}) => {
    const method = (init.method || "GET").toUpperCase();
    const u = String(url);
    calls.push({ method, url: u, body: init.body ?? null, headers: init.headers || null });
    for (const [m, p, respond] of script) {
      if (m.test(method) && p.test(u)) {
        const fn = typeof respond === "function" ? respond : () => respond;
        const out = fn(init.body);
        return {
          status: out.status,
          text: async () => out.data === undefined ? "" : JSON.stringify(out.data),
          headers: { get: name => (out.headers && out.headers[String(name).toLowerCase()]) || null }
        };
      }
    }
    return {
      status: 999,
      text: async () => JSON.stringify({ message: "unscripted " + u }),
      headers: { get: () => null }
    };
  };
}

/** Stub of the GIS token client. Returns an inspector for the captured config. */
function installGoogle(mode) {
  let config = null;
  globalThis.google = { accounts: { oauth2: {
    initTokenClient: cfg => {
      config = cfg;
      return { requestAccessToken: () => setTimeout(() => {
        if (mode === "ok") cfg.callback({ access_token: "tok-123", expires_in: 3599 });
        else if (mode === "blocked") {
          cfg.error_callback({ type: "popup_failed_to_open", message: "blocked" });
        } else if (mode === "closed") {
          cfg.error_callback({ type: "popup_closed" });
        } else {
          cfg.callback({ error: "access_denied" });
        }
      }, 0) };
    }
  } } };
  return () => config;
}

function bytesEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/* Minimal zip reader for round-tripping: find the EOCD from the tail, walk
 * the central directory, then slice each entry out of its local header. It
 * lives here (not in drive.js) so the module ships nothing it does not run. */
function readZip(bytes) {
  let eocdAt = -1;
  const floor = Math.max(0, bytes.length - 22 - 65536);
  for (let i = bytes.length - 22; i >= floor; i--) {
    if (bytes[i] === 0x50 && bytes[i + 1] === 0x4b
      && bytes[i + 2] === 0x05 && bytes[i + 3] === 0x06) { eocdAt = i; break; }
  }
  if (eocdAt < 0) throw new Error("no EOCD found");
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const count = dv.getUint16(eocdAt + 10, true);
  const cdSize = dv.getUint32(eocdAt + 12, true);
  const cdOffset = dv.getUint32(eocdAt + 16, true);
  const entries = [];
  let at = cdOffset;
  for (let i = 0; i < count; i++) {
    if (dv.getUint32(at, true) !== 0x02014b50) throw new Error(`bad CD signature at ${at}`);
    const flags = dv.getUint16(at + 8, true);
    const method = dv.getUint16(at + 10, true);
    const crc = dv.getUint32(at + 16, true);
    const size = dv.getUint32(at + 24, true);
    const nameLen = dv.getUint16(at + 28, true);
    const extraLen = dv.getUint16(at + 30, true);
    const commentLen = dv.getUint16(at + 32, true);
    const localOffset = dv.getUint32(at + 42, true);
    const name = new TextDecoder().decode(bytes.subarray(at + 46, at + 46 + nameLen));
    if (dv.getUint32(localOffset, true) !== 0x04034b50) {
      throw new Error(`bad local signature for ${name}`);
    }
    const lNameLen = dv.getUint16(localOffset + 26, true);
    const lExtraLen = dv.getUint16(localOffset + 28, true);
    const dataStart = localOffset + 30 + lNameLen + lExtraLen;
    entries.push({ name, flags, method, crc, size, bytes: bytes.slice(dataStart, dataStart + size) });
    at += 46 + nameLen + extraLen + commentLen;
  }
  return { count, cdSize, cdOffset, entries };
}

/* ------------------------------------------------------------------ crc-32 */

equal("crc32: known vector 123456789", crc32(utf8("123456789")), 0xCBF43926);
equal("crc32: known vector quick brown fox",
  crc32(utf8("The quick brown fox jumps over the lazy dog")), 0x414FA339);
equal("crc32: empty input is zero", crc32(new Uint8Array(0)), 0);
check("crc32: results are unsigned 32-bit",
  Number.isInteger(crc32(utf8("anything"))) && crc32(utf8("anything")) >= 0);

/* --------------------------------------------------------------- dos time */

{
  const { time, date } = dosDateTime(new Date(Date.UTC(2026, 9, 5, 13, 41, 9)));
  equal("dos time: packs (13,41,9) UTC", time, (13 << 11) | (41 << 5) | (9 >> 1));
  equal("dos date: packs 2026-10-05 UTC", date, ((2026 - 1980) << 9) | (10 << 5) | 5);
}
{
  // A device with a dead battery must clamp to 1980, not wrap negative.
  const { date } = dosDateTime(new Date(Date.UTC(1979, 0, 15, 3, 2, 1)));
  equal("dos date: pre-1980 clamps to 1980", date, (1 << 5) | 15);
}

/* -------------------------------------------------------------- zip build */

{
  const text = utf8("# notes-backup v2, exported 2026-10-05\r\nid,title\r\n");
  const zip = buildZip([{ name: "backup.csv", bytes: text }], { when: new Date(Date.UTC(2026, 9, 5)) });
  const parsed = readZip(zip);
  equal("zip: one entry round-trips", parsed.entries.length, 1);
  equal("zip: entry name", parsed.entries[0].name, "backup.csv");
  check("zip: entry bytes byte-equal", bytesEqual(parsed.entries[0].bytes, text));
  equal("zip: stored crc matches recomputed", parsed.entries[0].crc, crc32(text));
  equal("zip: stored size matches", parsed.entries[0].size, text.length);
}
{
  const zip = buildZip([{ name: "empty.jpg", bytes: new Uint8Array(0) }], { when: new Date() });
  const parsed = readZip(zip);
  equal("zip: empty entry survives", parsed.entries[0].size, 0);
  equal("zip: empty entry crc is zero", parsed.entries[0].crc, 0);
}
{
  const bin = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 255, 254, 1]);
  const zip = buildZip([
    { name: "backup.csv", bytes: utf8("a,b\r\n1,2\r\n") },
    { name: "media/x1-photo.jpg", bytes: bin }
  ], { when: new Date(Date.UTC(2026, 9, 5)) });
  const parsed = readZip(zip);
  equal("zip: two entries round-trip", parsed.entries.length, 2);
  check("zip: binary media bytes byte-equal", bytesEqual(parsed.entries[1].bytes, bin));
  equal("zip: media entry name keeps the media/ prefix",
    parsed.entries[1].name, "media/x1-photo.jpg");
  equal("zip: utf-8 flag is set on every entry", parsed.entries[0].flags, 0x0800);
  equal("zip: method is store (0)", parsed.entries[1].method, 0);
}
{
  const entries = [];
  for (let i = 0; i < 50; i++) {
    entries.push({ name: `media/file-${String(i).padStart(2, "0")}.jpg`, bytes: utf8(`payload ${i}`) });
  }
  const zip = buildZip(entries, { when: new Date(Date.UTC(2026, 9, 5)) });
  const parsed = readZip(zip);
  equal("zip: 50 entries counted in EOCD", parsed.count, 50);
  equal("zip: 50 central-directory records walked", parsed.entries.length, 50);
  equal("zip: first entry order preserved", parsed.entries[0].name, "media/file-00.jpg");
  equal("zip: last entry order preserved", parsed.entries[49].name, "media/file-49.jpg");
  check("zip: every entry byte-equal",
    parsed.entries.every((e, i) => bytesEqual(e.bytes, utf8(`payload ${i}`))));
  equal("zip: central directory size reaches exactly to the EOCD",
    parsed.cdOffset + parsed.cdSize, zip.length - 22);
}
check("zip: >65535 entries is refused before any bytes are built",
  (() => {
    try {
      buildZip(Array.from({ length: MAX_ZIP_ENTRIES + 1 }, (_, i) => (
        { name: `e${i}`, bytes: new Uint8Array(0) })));
      return false;
    } catch (error) { return error.code === "ZIP_TOO_MANY_ENTRIES"; }
  })());

/* -------------------------------------------------------------- client id */

equal("client id: sanitize keeps [A-Za-z0-9._-]",
  sanitizeClientId(" 1234-ab.prop-9_x "), "1234-ab.prop-9_x");
equal("client id: sanitize drops everything else", sanitizeClientId("a b/c@d#e"), "abcde");
setClientId("1234-test.apps.googleusercontent.com");
equal("client id: stored and read back",
  getClientId(), "1234-test.apps.googleusercontent.com");
equal("client id: hasClientId agrees", hasClientId(), true);
equal("client id: localStorage key is the pinned one",
  idStore.get(CLIENT_KEY), "1234-test.apps.googleusercontent.com");
idStore.set(CLIENT_KEY, "dirt​ id");
check("client id: dirty stored value heals on read", getClientId() === "dirtid",
  getClientId());
clearClientId();
equal("client id: clear empties it", getClientId(), "");
equal("client id: hasClientId false after clear", hasClientId(), false);

/* ------------------------------------------------ app-config id (v29) ----- */

{
  // The shared site's shape: NO per-device paste. With neither override nor
  // config the client id is "" (Drive unconfigured); the config.js value is
  // the default every visitor connects with; a working device override wins;
  // a garbage-only override falls back to the configured default.
  idStore.delete(CLIENT_KEY);
  globalThis.NOTES_APP_CONFIG = { driveClientId: "cfg-default.apps.googleusercontent.com" };
  equal("client id: falls back to the config.js default when no override exists",
    getClientId(), "cfg-default.apps.googleusercontent.com");
  equal("client id: hasClientId agrees with the config default", hasClientId(), true);
  setClientId("device-override.apps.googleusercontent.com");
  equal("client id: a device override wins over the config default",
    getClientId(), "device-override.apps.googleusercontent.com");
  idStore.set(CLIENT_KEY, "​");
  equal("client id: a garbage-only override falls back to config",
    getClientId(), "cfg-default.apps.googleusercontent.com");
  clearClientId();
  delete globalThis.NOTES_APP_CONFIG;
  equal("client id: neither override nor config means unconfigured", getClientId(), "");
  equal("client id: hasClientId false with nothing configured", hasClientId(), false);
}

/* --------------------------------------------------------- config.js file - */

{
  // The shipped config.js itself must parse and expose the app config, with
  // a driveClientId that is either "" (not configured) or a well-formed
  // public Client ID.
  let cfgError = null;
  try { await import(new URL("config.js", resolved).href); }
  catch (error) { cfgError = error; }
  const cfg = globalThis.NOTES_APP_CONFIG;
  check("config.js: parses and sets globalThis.NOTES_APP_CONFIG",
    !cfgError && !!cfg && typeof cfg.driveClientId === "string",
    cfgError ? String(cfgError) : JSON.stringify(cfg || null));
  check("config.js: driveClientId is empty or a full Client ID",
    cfg && (cfg.driveClientId === ""
      || /^[A-Za-z0-9._-]+\.apps\.googleusercontent\.com$/.test(cfg.driveClientId)),
    JSON.stringify(cfg && cfg.driveClientId));
  delete globalThis.NOTES_APP_CONFIG;
}

/* ------------------------------------------------------- friendly errors */

check("friendly: NOT_CONFIGURED names the Client ID box",
  friendlyDriveError({ code: "NOT_CONFIGURED" }).includes("Client ID"));
check("friendly: POPUP_BLOCKED tells the user to allow popups",
  friendlyDriveError({ code: "POPUP_BLOCKED" }).includes("popups"));
check("friendly: POPUP_CLOSED invites another tap",
  friendlyDriveError({ code: "POPUP_CLOSED" }).includes("tap the sync button"));
check("friendly: ACCESS_DENIED points at the consent screen",
  friendlyDriveError({ code: "ACCESS_DENIED" }).includes("consent"));
check("friendly: TOKEN_EXPIRED invites reconnect",
  friendlyDriveError({ code: "TOKEN_EXPIRED" }).includes("expired"));
check("friendly: NO_SESSION keeps the PC success intact in the sentence",
  friendlyDriveError({ code: "NO_SESSION" }).includes("PC copy succeeded"));
check("friendly: TypeError becomes a network sentence",
  friendlyDriveError(new TypeError("Failed to fetch")).includes("reach Google"));
check("friendly: UPLOAD_FAILED(403) mentions space or consent",
  friendlyDriveError({ code: "UPLOAD_FAILED", message: "upload failed (403)" })
    .includes("space"));
check("friendly: UPLOAD_FAILED(other) invites another tap",
  friendlyDriveError({ code: "UPLOAD_FAILED", message: "upload failed (500)" })
    .includes("tap the sync button"));

/* ---------------------------------------------------------- uploadBackup */

const LOC = { location: "https://www.googleapis.com/upload/session/probe-1" };

function scriptHappyFolder({ exists = false } = {}) {
  script = [
    [/^GET$/, /drive\/v3\/files\?q=/,
      { status: 200, data: { files: exists ? [{ id: "fld0", name: DRIVE_FOLDER }] : [] } }],
    [/^POST$/, /drive\/v3\/files$/,
      { status: 200, data: { id: "fld1", name: DRIVE_FOLDER } }],
    [/^POST$/, /upload\/drive\/v3\/files\?uploadType=resumable/, { status: 200, data: {}, headers: LOC }],
    [/^PUT$/, /upload\/session\//, { status: 200, data: { id: "file1", name: "notes-backup-x.zip" } }]
  ];
}

installFetch();

{
  scriptHappyFolder({});
  calls = [];
  const zip = buildZip([{ name: "backup.csv", bytes: utf8("a,b\r\n") }], { when: new Date() });
  const folderId = await ensureBackupFolder("tok-abc");   // list empty -> create fld1
  const result = await uploadBackup({
    token: "tok-abc", zipBytes: zip, name: "notes-backup-20261005-abcdef12.zip", folderId
  });
  equal("upload: happy path resolves the file id", result.fileId, "file1");
  equal("upload: happy path reports the created folder id", result.folderId, "fld1");
  equal("upload: happy path reports the zip size", result.bytes, zip.length);
  const order = calls.map(c => `${c.method} ${c.url.split("?")[0]}`);
  check("upload: call order is list -> create -> initiate -> PUT",
    /GET.*files/.test(order[0]) && /POST.*files/.test(order[1])
    && /POST.*upload/.test(order[2]) && /PUT.*session/.test(order[3]), order.join(" | "));
  check("upload: every call carries the bearer token",
    calls.every(c => c.headers.Authorization === "Bearer tok-abc"));
  check("upload: the token rides only the Authorization header",
    calls.every(c => c.url.indexOf("tok-abc") === -1
      && !(typeof c.body === "string" && c.body.indexOf("tok-abc") !== -1)));
  const listUrl = calls[0].url;
  check("upload: folder list queries the pinned name, folder mime and trashed=false",
    listUrl.includes(encodeURIComponent(`name='${DRIVE_FOLDER}'`))
    && listUrl.includes(encodeURIComponent("trashed=false")));
  const createBody = JSON.parse(calls[1].body);
  equal("upload: folder create names the pinned folder", createBody.name, DRIVE_FOLDER);
  equal("upload: folder create uses the folder mime",
    createBody.mimeType, "application/vnd.google-apps.folder");
  const initBody = JSON.parse(calls[2].body);
  equal("upload: metadata names the file", initBody.name, "notes-backup-20261005-abcdef12.zip");
  check("upload: metadata parents the folder", initBody.parents[0] === "fld1");
  equal("upload: metadata carries the schema marker", initBody.appProperties.schema, BACKUP_SCHEMA);
  equal("upload: initiate sets X-Upload-Content-Type",
    calls[2].headers["X-Upload-Content-Type"], "application/zip");
  equal("upload: initiate sets X-Upload-Content-Length",
    calls[2].headers["X-Upload-Content-Length"], String(zip.length));
  const put = calls[3];
  equal("upload: PUT content type is application/zip", put.headers["Content-Type"], "application/zip");
  check("upload: PUT body is the zip byte-for-byte", bytesEqual(put.body, zip));
}
{
  scriptHappyFolder({ exists: true });
  calls = [];
  const folderId = await ensureBackupFolder("tok-abc");
  equal("upload: existing folder is found, not recreated", folderId, "fld0");
  check("upload: no folder create call when one exists",
    calls.filter(c => c.method === "POST").length === 0);
}
{
  script = [
    [/^GET$/, /drive\/v3\/files\?q=/, { status: 200, data: { files: [] } }],
    [/^POST$/, /drive\/v3\/files$/, { status: 200, data: { id: "fld1" } }],
    [/^POST$/, /upload\/drive\/v3\/files\?uploadType=resumable/,
      { status: 200, data: {}, headers: {} }]
  ];
  let threw = null;
  try {
    await uploadBackup({ token: "t", zipBytes: new Uint8Array([1]), name: "n.zip", folderId: "f" });
  } catch (error) { threw = error; }
  equal("upload: hidden Location header fails as NO_SESSION", threw?.code, "NO_SESSION");
}
{
  script = [
    [/^POST$/, /upload\/drive\/v3\/files\?uploadType=resumable/, { status: 401, data: {} }]
  ];
  let threw = null;
  try {
    await uploadBackup({ token: "t", zipBytes: new Uint8Array([1]), name: "n.zip", folderId: "f" });
  } catch (error) { threw = error; }
  equal("upload: 401 initiation fails as TOKEN_EXPIRED", threw?.code, "TOKEN_EXPIRED");
}
{
  script = [
    [/^POST$/, /upload\/drive\/v3\/files\?uploadType=resumable/,
      { status: 200, data: {}, headers: LOC }],
    [/^PUT$/, /upload\/session\//, { status: 500, data: {} }]
  ];
  let threw = null;
  try {
    await uploadBackup({ token: "t", zipBytes: new Uint8Array([1]), name: "n.zip", folderId: "f" });
  } catch (error) { threw = error; }
  equal("upload: failed PUT surfaces as UPLOAD_FAILED", threw?.code, "UPLOAD_FAILED");
}

/* ---------------------------------------------------------- requestToken */

{
  const getCfg = installGoogle("ok");
  const token = await requestToken("1234-test.apps.googleusercontent.com");
  equal("gis: resolves the access token", token, "tok-123");
  equal("gis: client id is passed through",
    getCfg().client_id, "1234-test.apps.googleusercontent.com");
  equal("gis: scope is exactly drive.file", getCfg().scope, DRIVE_SCOPE);
}
{
  installGoogle("blocked");
  let threw = null;
  try { await requestToken("x"); } catch (error) { threw = error; }
  equal("gis: popup_failed_to_open -> POPUP_BLOCKED", threw?.code, "POPUP_BLOCKED");
}
{
  installGoogle("closed");
  let threw = null;
  try { await requestToken("x"); } catch (error) { threw = error; }
  equal("gis: popup_closed -> POPUP_CLOSED", threw?.code, "POPUP_CLOSED");
}
{
  installGoogle("denied");
  let threw = null;
  try { await requestToken("x"); } catch (error) { threw = error; }
  equal("gis: callback error response -> ACCESS_DENIED", threw?.code, "ACCESS_DENIED");
}

globalThis.fetch = REAL_FETCH;
delete globalThis.google;

console.log(`\n===== ${passed} passed, ${failed} failed =====`);
process.exit(failed ? 1 : 0);
