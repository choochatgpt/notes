/**
 * Tests for the one-tap sync transport (sync.js).
 *
 *   node tests/sync.test.mjs [path-to-sync.js]
 *   Exit: 0 = all pass, 1 = one or more failures
 *
 * The module path defaults to ../source/sync.js (durable-mirror layout); pass
 * ../sync.js when running inside the flat published/active layout.
 *
 * global.fetch is stubbed with a scripted queue, so every claim below is made
 * against the exact JSON the GitHub API would return -- including the 422
 * non-fast-forward race that JScan hit on a real phone and the base_tree rule
 * whose omission silently deletes the other side's branch contents.
 */
const resolved = new URL(process.argv[2] || "../source/sync.js", import.meta.url);
const {
  MAX_FILE_BYTES,
  b64,
  buildBundleManifest,
  checkPickedUp,
  clearToken,
  getToken,
  hasToken,
  newExportId,
  setToken,
  sha256hex,
  submit,
  utf8
} = await import(resolved.href);

// Node has no localStorage; the module looks it up at call time, so a
// Map-backed shim gives the token functions a real store to prove themselves
// against (a shim INSIDE sync.js would mean the tested code is not the
// shipped code).
const tokenStore = new Map();
globalThis.localStorage = {
  getItem: key => (tokenStore.has(key) ? tokenStore.get(key) : null),
  setItem: (key, value) => tokenStore.set(key, String(value)),
  removeItem: key => tokenStore.delete(key)
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

/* The scripted GitHub. Each entry is [method-regex, path-regex, responder].
 * responders receive the parsed request body and return {status, data}. */
let script = [];
let calls = [];

function installFetch() {
  globalThis.fetch = async (url, init = {}) => {
    const method = (init.method || "GET").toUpperCase();
    const path = String(url).replace("https://api.github.com", "");
    calls.push({ method, path, body: init.body ? JSON.parse(init.body) : null,
                   headers: init.headers || null });
    for (const [m, p, respond] of script) {
      if (m.test(method) && p.test(path)) {
        const fn = typeof respond === "function" ? respond : () => respond;
        const out = fn(init.body ? JSON.parse(init.body) : null);
        return {
          status: out.status,
          text: async () => out.data === undefined ? "" : JSON.stringify(out.data)
        };
      }
    }
    return { status: 999, text: async () => JSON.stringify({ message: "unscripted " + path }) };
  };
}

const REAL_FETCH = globalThis.fetch;

const EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904";
let counter = 0;
const sha = label => {
  counter += 1;
  return `${label}${String(counter).padStart(8, "0")}`.padEnd(40, "0");
};

function scriptHappyPath({ branchExists = false, parentTree = EMPTY_TREE } = {}) {
  script = [
    [/GET$/, /\/git\/ref\/heads\/notes-inbox$/,
      () => (branchExists
        ? { status: 200, data: { object: { sha: sha("ref") } } }
        : { status: 404, data: {} })],
    [/POST$/, /\/git\/commits$/, () => ({ status: 201, data: { sha: sha("bare") } })],
    [/POST$/, /\/git\/refs$/, () => ({ status: 201, data: {} })],
    [/POST$/, /\/git\/blobs$/, () => ({ status: 201, data: { sha: sha("blob") } })],
    [/^GET$/, /\/git\/commits\/[^/]+$/,
      () => ({ status: 200, data: { tree: { sha: parentTree } } })],
    [/POST$/, /\/git\/trees$/, () => ({ status: 201, data: { sha: sha("tree") } })],
    [/PATCH$/, /\/git\/refs\/heads\/notes-inbox$/,
      () => ({ status: 200, data: {} })]
  ];
}
// The commits POST route must distinguish the bare-commit POST from the
// land-commit POST: both are POST /git/commits. The happy path above lets the
// FIRST POST /git/commits (bare, only when the branch is missing) return the
// bare sha, and land's POST /git/commits also matches the same route. Keep it
// simple: bare commit creation only happens on the 404 path, and both responses
// are valid shas for their role, so one responder serves both.

/* ---------------------------------------------------------------- b64/utf8 */

equal("b64: empty", b64(new Uint8Array(0)), "");
equal("b64: one byte", b64(utf8("f")), "Zg==");
equal("b64: two bytes", b64(utf8("fo")), "Zm8=");
equal("b64: three bytes", b64(utf8("foo")), "Zm9v");
{
  const all = new Uint8Array(255);
  for (let i = 0; i < 255; i++) all[i] = i + 1;
  equal("b64: 255 arbitrary bytes match Buffer",
        b64(all), Buffer.from(all).toString("base64"));
}
equal("utf8: multibyte round trip",
      new TextDecoder().decode(utf8("héllo — ☕")),
      "héllo — ☕");

{
  const bytes = utf8("hello");
  const hex = await sha256hex(bytes);
  equal("sha256hex: known vector",
        hex, "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824");
}

/* ------------------------------------------------------------------- token */

clearToken();
equal("token: absent by default", getToken(), "");
setToken("  test-token-abc  ");
equal("token: saved trimmed", getToken(), "test-token-abc");

/* The phone failure of 2026-10-04: the pasted token carried an invisible
 * non-ASCII character and fetch refused the request with "String contains
 * non ISO-8859-1 code point" before it ever left the device. Tokens are
 * [A-Za-z0-9_-], so everything else is paste dirt and is stripped on both
 * save and read. */
{
  const { sanitizeToken } = await import(resolved.href);
  setToken("  Bearer github_pat_abc123  ");
  equal("sanitize: leading Bearer + spaces stripped", getToken(), "github_pat_abc123");
  setToken("“ghp_ABC”…");
  equal("sanitize: curly quotes and ellipsis stripped", getToken(), "ghp_ABC");
  setToken("​‌");
  equal("sanitize: zero-width-only paste stores nothing", getToken(), "");
  equal("sanitize: zero-width-only paste means no token", hasToken(), false);
  globalThis.localStorage.setItem("notes.sync.token", "​ghp_XYZ​");
  equal("sanitize: read heals a value stored polluted by an older build",
        getToken(), "ghp_XYZ");
  setToken("clean-token_for-tests");
  installFetch(); // the real fetch is still live until the submit section installs the stub
  calls = [];
  script = [
    [/GET$/, /\/git\/ref\/heads\/notes-inbox$/, { status: 200, data: { object: { sha: sha("ref") } } }],
    [/^GET$/, /\/git\/commits\/[^/]+$/, { status: 200, data: { tree: { sha: EMPTY_TREE } } }],
    [/POST$/, /\/git\/blobs$/, { status: 201, data: { sha: sha("blob") } }],
    [/POST$/, /\/git\/trees$/, { status: 201, data: { sha: sha("tree") } }],
    [/POST$/, /\/git\/commits$/, { status: 201, data: { sha: sha("land") } }],
    [/PATCH$/, /\/git\/refs\/heads\/notes-inbox$/, { status: 200, data: {} }]
  ];
  await submit({ csv: "# notes-backup v1", media: [] });
  const refCall = calls.find(c => /git\/ref/.test(c.path));
  equal("sanitize: the Authorization header carries the cleaned token",
        refCall.headers.Authorization, "Bearer clean-token_for-tests");
}

/* ------------------------------------------------------------------ submit */

installFetch();

// No token -> refuse before any network call.
clearToken();
script = [];
calls = [];
{
  let threw = "";
  try { await submit({ csv: "# notes-backup v1", media: [] }); }
  catch (e) { threw = e.code || e.message; }
  equal("submit: no token refuses with NO_TOKEN", threw, "NO_TOKEN");
  equal("submit: no token made zero network calls", calls.length, 0);
}

// Oversize file -> refuse before any network call.
setToken("test-token-abc");
calls = [];
{
  let threw = "";
  try {
    await submit({ csv: "# notes-backup v1",
                   media: [{ name: "big.jpg", bytes: new Uint8Array(MAX_FILE_BYTES + 1) }] });
  } catch (e) { threw = e.code || e.message; }
  equal("submit: oversize file refuses with FILE_TOO_LARGE", threw, "FILE_TOO_LARGE");
  equal("submit: oversize refusal made zero network calls", calls.length, 0);
}

// Happy path, branch missing: bare commit -> refs -> blobs -> tree -> commit -> ref.
scriptHappyPath({ branchExists: false });
calls = [];
{
  const mediaBytes = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3]);
  const result = await submit({
    csv: "# notes-backup v1\r\nid,title\r\n",
    media: [{ name: "p1-photo.jpg", bytes: mediaBytes, type: "image/jpeg" }],
    meta: { counts: { folders: 1, notes: 2, reminders: 3 }, appVersion: "22" }
  });
  check("submit: resolves with an export id", /^n?\d{8}-[0-9a-f]{8}$/.test(result.exportId)
        || /^[0-9a-f-]{8,}$/.test(result.exportId), result.exportId);
  equal("submit: mediaCount", result.mediaCount, 1);

  const blobCalls = calls.filter(c => c.method === "POST" && c.path.endsWith("/git/blobs"));
  equal("submit: three blobs (manifest, csv, photo)", blobCalls.length, 3);
  const decoded = blobCalls.map(c => Buffer.from(c.body.content, "base64"));
  const manifest = JSON.parse(decoded.filter(d => d.toString().includes("notes.sync.bundle/1"))[0].toString());
  equal("manifest: schema", manifest.schema, "notes.sync.bundle/1");
  equal("manifest: counts carried", JSON.stringify(manifest.counts),
        JSON.stringify({ folders: 1, notes: 2, reminders: 3 }));
  equal("manifest: two payload files listed (manifest.json itself excluded)",
        manifest.files.length, 2);
  equal("manifest: csv path", manifest.files[0].path, "backup.csv");
  equal("manifest: media path", manifest.files[1].path, "media/p1-photo.jpg");
  equal("manifest: media type recorded", manifest.files[1].type, "image/jpeg");
  const csvPayload = decoded.find(d => d.toString().startsWith("# notes-backup"));
  check("csv blob round-trips the bytes", csvPayload.toString().includes("id,title"));
  const photoPayload = decoded.find(d => !d.toString().startsWith("#") && d[0] === 0x89);
  check("photo blob round-trips binary bytes", photoPayload.length === mediaBytes.length);

  const treeCall = calls.find(c => c.method === "POST" && c.path.endsWith("/git/trees"));
  check("tree: entries land under the export id",
        treeCall.body.tree.every(e => e.path.startsWith(`${result.exportId}/`)));
  check("tree: base_tree omitted on a bare parent", !("base_tree" in treeCall.body),
        JSON.stringify(Object.keys(treeCall.body)));
  const patch = calls.find(c => c.method === "PATCH");
  check("ref: PATCHed to the new commit", patch && patch.body.sha.length === 40);
}

// Existing branch with a non-empty tree: base_tree is MANDATORY, or the new
// tree would replace the branch contents and silently drop the other side's
// data (the bug JScan found and fixed during testing).
scriptHappyPath({ branchExists: true, parentTree: sha("busytree") });
calls = [];
{
  await submit({ csv: "# notes-backup v1", media: [] });
  const treeCall = calls.find(c => c.method === "POST" && c.path.endsWith("/git/trees"));
  check("tree: base_tree present on a populated parent",
        treeCall.body.base_tree === treeCall.body.base_tree && typeof treeCall.body.base_tree === "string"
        && treeCall.body.base_tree.length === 40,
        JSON.stringify(treeCall.body).slice(0, 120));
}

// The race: the PC rewrites the branch (or a second export lands) while our
// blobs upload, so the ref PATCH returns 422 non-fast-forward. The CAS loop
// re-reads the head and rebuilds; blobs are NOT re-uploaded.
{
  let patchCalls = 0;
  script = [
    [/GET$/, /\/git\/ref\/heads\/notes-inbox$/,
      { status: 200, data: { object: { sha: sha("ref") } } }],
    [/^GET$/, /\/git\/commits\/[^/]+$/,
      () => ({ status: 200, data: { tree: { sha: EMPTY_TREE } } })],
    [/POST$/, /\/git\/blobs$/, () => ({ status: 201, data: { sha: sha("blob") } })],
    [/POST$/, /\/git\/trees$/, () => ({ status: 201, data: { sha: sha("tree") } })],
    [/POST$/, /\/git\/commits$/, () => ({ status: 201, data: { sha: sha("land") } })],
    [/PATCH$/, /\/git\/refs\/heads\/notes-inbox$/,
      () => {
        patchCalls += 1;
        return patchCalls <= 2
          ? { status: 422, data: { message: "Update is not a fast forward" } }
          : { status: 200, data: {} };
      }]
  ];
  calls = [];
  const result = await submit({ csv: "# notes-backup v1", media: [] });
  equal("race: landed after two 422s", patchCalls, 3);
  const blobCount = calls.filter(c => c.path.endsWith("/git/blobs")).length;
  equal("race: blobs uploaded once, not once per attempt", blobCount, 2); // manifest + csv
  check("race: resolves with a commit", result.commit.length === 12);
}

// Exhausted retries surface an honest error.
{
  script = [
    [/GET$/, /\/git\/ref\/heads\/notes-inbox$/,
      { status: 200, data: { object: { sha: sha("ref") } } }],
    [/^GET$/, /\/git\/commits\/[^/]+$/,
      () => ({ status: 200, data: { tree: { sha: EMPTY_TREE } } })],
    [/POST$/, /\/git\/blobs$/, () => ({ status: 201, data: { sha: sha("blob") } })],
    [/POST$/, /\/git\/trees$/, () => ({ status: 201, data: { sha: sha("tree") } })],
    [/POST$/, /\/git\/commits$/, () => ({ status: 201, data: { sha: sha("land") } })],
    [/PATCH$/, /\/git\/refs\/heads\/notes-inbox$/,
      () => ({ status: 422, data: { message: "Update is not a fast forward" } })]
  ];
  let threw = "";
  try { await submit({ csv: "# notes-backup v1", media: [] }); }
  catch (e) { threw = e.message; }
  check("race: exhausted retries reject honestly", threw.includes("ref update failed"), threw);
}

/* -------------------------------------------------------------- buildBundle */

{
  const manifest = buildBundleManifest(
    { exportId: "20261003-ab", appVersion: "22" },
    [{ path: "backup.csv", sha256: "x", bytes: 3 }]
  );
  equal("buildBundleManifest: schema pinned", manifest.schema, "notes.sync.bundle/1");
  equal("buildBundleManifest: export id carried", manifest.export_id, "20261003-ab");
  check("buildBundleManifest: created_utc is second precision Z",
        /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(manifest.created_utc),
        manifest.created_utc);
}

/* ----------------------------------------------------------------- newExportId */

check("newExportId: date-prefixed and unique",
      /^[0-9]{8}-[0-9a-f]{8}$/.test(newExportId())
      && newExportId() !== newExportId(),
      newExportId());

/* ------------------------------------------------------------ checkPickedUp */

{
  // Picked up: the ack lists our export id.
  const ack = { schema: "notes.sync.ack/1", processed: ["20261003-ab"],
                source_commit: "be47224d", at: "2026-10-03T00:21:10" };
  script = [
    [/GET$/, /\/git\/ref\/heads\/notes-inbox$/,
      { status: 200, data: { object: { sha: sha("ref") } } }],
    [/^GET$/, /\/git\/commits\/[^/]+$/,
      { status: 200, data: { tree: { sha: sha("tree") } } }],
    [/^GET$/, /\/git\/trees\/[^/]+$/,
      { status: 200, data: { tree: [{ path: "ack.json", sha: sha("ackblob") }] } }],
    [/^GET$/, /\/git\/blobs\/[^/]+$/,
      { status: 200, data: { encoding: "base64",
                             content: Buffer.from(JSON.stringify(ack)).toString("base64") } }]
  ];
  const picked = await checkPickedUp("20261003-ab");
  check("ack: picked up when the id is listed", picked.pickedUp === true && picked.at === ack.at,
        JSON.stringify(picked));
  const notPicked = await checkPickedUp("99999999-ff");
  check("ack: not picked up when the id is absent", notPicked.pickedUp === false,
        JSON.stringify(notPicked));

  // No ack on the branch yet -> honestly not picked up.
  script[2] = [/^GET$/, /\/git\/trees\/[^/]+$/,
    { status: 200, data: { tree: [] } }];
  const empty = await checkPickedUp("20261003-ab");
  check("ack: missing ack.json reports not picked up",
        empty.pickedUp === false && empty.at === null, JSON.stringify(empty));

  // No token / no id -> checked:false, zero calls.
  clearToken();
  const unchecked = await checkPickedUp("20261003-ab");
  equal("ack: no token reports checked:false", unchecked.checked, false);
  setToken("test-token-abc");
}

// A token that is valid but too narrow gets 403/404 from GitHub, not 401.
// The branch bootstrap must surface that as TOKEN_SCOPE so the app can say
// "permissions", not "offline".
{
  script = [
    [/GET$/, /\/git\/ref\/heads\/notes-inbox$/, { status: 404, data: {} }],
    [/POST$/, /\/git\/commits$/, { status: 404, data: { message: "Not Found" } }]
  ];
  let threw = null;
  try { await submit({ csv: "# notes-backup v1", media: [] }); }
  catch (e) { threw = e; }
  check("token too narrow: bare-commit 404 surfaces as TOKEN_SCOPE",
        !!threw && threw.code === "TOKEN_SCOPE"
        && /bare commit failed \(404\)/.test(threw.message),
        threw ? threw.code + " " + threw.message : "nothing thrown");
}

clearToken();
globalThis.fetch = REAL_FETCH;

console.log(`\n===== ${passed} passed, ${failed} failed =====`);
process.exit(failed ? 1 : 0);
