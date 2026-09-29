/**
 * Tests for the pure backup serializer and parser.
 *
 *   node tests/backup.test.mjs [path-to-backup.js]
 *   Exit: 0 = all pass, 1 = one or more failures
 *
 * The module path defaults to ../source/backup.js (durable-mirror layout); pass
 * ../backup.js when running inside the flat published/active layout.
 *
 * These guard the two properties the restore depends on: a built CSV parses
 * back into exactly the records it was built from (round-trip), and a broken
 * one comes back as errors rather than as exceptions or half-parsed rows --
 * because the parser stands between a paste and a destructive overwrite.
 */
// Resolve against this file, not the caller's cwd, so the harness works from
// either the flat workspace layout or the durable-mirror one.
const resolved = new URL(process.argv[2] || "../source/backup.js", import.meta.url);
const {
  buildBackupCsv,
  buildMailtoHref,
  describeRestore,
  MAILTO_SAFE_LIMIT,
  parseBackupCsv,
  SCHEMA_VERSION
} = await import(resolved.href);

let passed = 0;
let failed = 0;

function check(name, condition, extra) {
  if (condition) {
    passed += 1;
    console.log(`PASS  ${name}`);
  } else {
    failed += 1;
    console.log(`FAIL  ${name}`);
    if (extra !== undefined) console.log(`      ${extra}`);
  }
}

function equal(name, actual, expected) {
  const ok = actual === expected;
  if (!ok) console.log(`      expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  check(name, ok);
}

const STAMP = "2026-09-29T12:00:00.000Z";

// A fixture that exercises every escaping hazard at once: a comma and quotes in
// a folder name, a newline and a comma in a note body, nesting, an empty
// folder, and both an enabled weekly reminder and a spent disabled one.
const fixture = {
  folders: [
    { id: "f1", parentId: null, name: 'Work, "HQ"', createdAt: STAMP, updatedAt: STAMP },
    { id: "f2", parentId: "f1", name: "Projects", createdAt: STAMP, updatedAt: STAMP },
    { id: "f3", parentId: null, name: "Empty", createdAt: STAMP, updatedAt: STAMP }
  ],
  notes: [
    { id: "n1", folderId: "f2", title: 'Q3, "final"', body: "line one\nline two, with comma",
      createdAt: STAMP, updatedAt: STAMP },
    { id: "n2", folderId: null, title: "", body: "", createdAt: STAMP, updatedAt: STAMP }
  ],
  reminders: [
    { id: "r1", folderId: null, title: "Standup", body: "details, daily", enabled: true,
      startAt: "2027-01-05T09:00:00.000Z", timeZone: "America/New_York",
      recurrence: { version: 1, kind: "weekly", interval: 2, weekdays: [2, 4] },
      createdAt: STAMP, updatedAt: STAMP },
    { id: "r2", folderId: null, title: "Spent one-off", body: "", enabled: false,
      startAt: "2026-01-01T00:00:00.000Z", timeZone: "UTC",
      recurrence: { version: 1, kind: "once", interval: 1, weekdays: [] },
      createdAt: STAMP, updatedAt: STAMP }
  ]
};

console.log("=== 1. build -> parse round-trip ===");
{
  const csv = buildBackupCsv({ ...fixture, exportedAt: STAMP });
  const parsed = parseBackupCsv(csv);

  check("a built backup parses cleanly", parsed.ok, parsed.errors.join(" | "));
  equal("version is read back", parsed.version, SCHEMA_VERSION);
  equal("exportedAt is read back", parsed.exportedAt, STAMP);
  equal("every folder survives", parsed.folders.length, 3);
  equal("every note survives", parsed.notes.length, 2);
  equal("every reminder survives", parsed.reminders.length, 2);

  const byId = (list, id) => list.find(row => row.id === id);
  equal("the nested folder keeps its parent",
        byId(parsed.folders, "f2").parentId, "f1");
  equal("a root folder keeps a null parent",
        byId(parsed.folders, "f1").parentId, null);
  equal("the empty folder is still there (it is part of the hierarchy)",
        byId(parsed.folders, "f3").name, "Empty");
  equal("a folder name with comma and quotes round-trips exactly",
        byId(parsed.folders, "f1").name, 'Work, "HQ"');
  equal("a note's folder reference survives",
        byId(parsed.notes, "n1").folderId, "f2");
  equal("an unfiled note keeps a null folder",
        byId(parsed.notes, "n2").folderId, null);
  equal("a title with comma and quotes round-trips exactly",
        byId(parsed.notes, "n1").title, 'Q3, "final"');
  equal("a body with an embedded newline round-trips exactly",
        byId(parsed.notes, "n1").body, "line one\nline two, with comma");

  const weekly = byId(parsed.reminders, "r1");
  equal("the recurrence object round-trips field for field",
        JSON.stringify(weekly.recurrence),
        JSON.stringify(fixture.reminders[0].recurrence));
  equal("startAt survives verbatim", weekly.startAt, fixture.reminders[0].startAt);
  equal("timeZone survives", weekly.timeZone, "America/New_York");
  equal("an enabled reminder stays enabled", weekly.enabled, true);
  equal("a disabled reminder stays disabled", byId(parsed.reminders, "r2").enabled, false);
  equal("a comma inside a reminder body round-trips",
        byId(parsed.reminders, "r1").body, "details, daily");

  // nextDueAt is a cached value that goes stale; trusting it from the file
  // would resurrect dates computed on the machine that exported it.
  check("nextDueAt is never stored in the backup",
        !("nextDueAt" in weekly), Object.keys(weekly).join(","));
}

console.log("\n=== 2. line endings and the BOM ===");
{
  const csv = buildBackupCsv({ ...fixture, exportedAt: STAMP });
  const lf = parseBackupCsv(csv.replace(/\r\n/g, "\n"));
  check("an LF-only file parses the same as CRLF", lf.ok, lf.errors.join(" | "));
  equal("...with the same note body", lf.notes[0].body, "line one\nline two, with comma");

  const bom = String.fromCharCode(0xfeff);
  const withBom = parseBackupCsv(bom + csv);
  check("a UTF-8 BOM is skipped, not treated as garbage",
        withBom.ok, withBom.errors.join(" | "));
  equal("...and the header still reads", withBom.version, SCHEMA_VERSION);

  const trailing = parseBackupCsv(csv + "\n\n");
  check("trailing blank lines do not invent a row", trailing.ok && trailing.notes.length === 2);
}

console.log("\n=== 3. malformed input comes back as errors, not exceptions ===");
{
  const notBackup = parseBackupCsv("hello, world\nthis,is,not,a,backup");
  equal("plain text is refused", notBackup.ok, false);
  check("...with a message that names the header",
        notBackup.errors.some(e => e.indexOf("notes-backup") !== -1),
        notBackup.errors.join(" | "));

  const empty = parseBackupCsv("");
  equal("an empty paste is refused", empty.ok, false);
  check("...and says nothing was pasted",
        empty.errors.some(e => e.indexOf("Nothing was pasted") !== -1),
        empty.errors.join(" | "));
  equal("undefined is refused without throwing", parseBackupCsv(undefined).ok, false);

  const wrongVersion = parseBackupCsv(
    buildBackupCsv({ ...fixture, exportedAt: STAMP })
      .replace(`# notes-backup v${SCHEMA_VERSION}`, "# notes-backup v9")
  );
  equal("a future version is refused rather than half-read", wrongVersion.ok, false);
  check("...and the message names both versions",
        wrongVersion.errors.some(e => e.indexOf("v9") !== -1 && e.indexOf(`v${SCHEMA_VERSION}`) !== -1),
        wrongVersion.errors.join(" | "));

  // A section deleted (a truncated copy/paste is the realistic case).
  const csv = buildBackupCsv({ ...fixture, exportedAt: STAMP });
  const cut = csv.slice(0, csv.indexOf("## reminders"));
  const missingSection = parseBackupCsv(cut);
  equal("a file missing a section is refused", missingSection.ok, false);
  check("...naming the section it needed",
        missingSection.errors.some(e => e.indexOf("## reminders") !== -1),
        missingSection.errors.join(" | "));

  const unterminated = parseBackupCsv(csv + '"never closed');
  equal("an unterminated quote is refused", unterminated.ok, false);

  // A duplicate must land inside the folders section, so splice it in right
  // before the next section header rather than tacking it on the end.
  const lines = csv.split("\r\n");
  const notesIdx = lines.findIndex(line => line.startsWith("## notes"));
  lines.splice(notesIdx, 0, `f1,,Top,${STAMP},${STAMP}`);
  const duplicate = parseBackupCsv(lines.join("\r\n"));
  equal("a duplicate folder id is refused", duplicate.ok, false);
  check("...with the line that caused it",
        duplicate.errors.some(e => e.indexOf("duplicate folder id") !== -1),
        duplicate.errors.join(" | "));

  const badDate = parseBackupCsv(csv.replace("2027-01-05T09:00:00.000Z", "not-a-date"));
  equal("a reminder with an unparseable first-due date is refused", badDate.ok, false);

  const badRule = parseBackupCsv(csv.replace(
    '"{""version"":1,""kind"":""weekly"",""interval"":2,""weekdays"":[2,4]}"',
    "not-json"
  ));
  equal("a reminder whose recurrence is not JSON is refused", badRule.ok, false);
}

console.log("\n=== 4. dangling references are repaired, with a warning ===");
{
  const csv = buildBackupCsv({ ...fixture, exportedAt: STAMP });
  // A note filed under a folder the file does not contain. `f2,"Q3` is the
  // exact serialisation: f2 needs no quoting, the title does.
  const lostNote = parseBackupCsv(csv.replace('f2,"Q3', 'gone,"Q3'));
  check("the file still parses", lostNote.ok, lostNote.errors.join(" | "));
  equal("...but the reference is nulled", lostNote.notes[0].folderId, null);
  check("...and the preview is told",
        lostNote.warnings.some(w => w.indexOf("Q3") !== -1 || w.indexOf("missing folder") !== -1),
        lostNote.warnings.join(" | "));

  const lostParent = parseBackupCsv(csv.replace("f2,f1,Projects", "f2,gone,Projects"));
  check("a folder whose parent is missing still parses", lostParent.ok,
        lostParent.errors.join(" | "));
  equal("...and is lifted to the top level",
        lostParent.folders.find(f => f.id === "f2").parentId, null);
  check("...with a warning about the lift",
        lostParent.warnings.length > 0, lostParent.warnings.join(" | "));
}

console.log("\n=== 5. describeRestore -- counts before the point of no return ===");
{
  const text = describeRestore({
    current: { folders: 2, notes: 1, reminders: 3 },
    incoming: { folders: 1, notes: 4, reminders: 1 },
    exportedAt: STAMP
  });
  check("the current data is counted",
        text.indexOf("2 folders, 1 note, 3 reminders") !== -1, text);
  check("the incoming data is counted",
        text.indexOf("1 folder, 4 notes, 1 reminder") !== -1, text);
  check("the export date is stated", text.indexOf(STAMP) !== -1, text);
  check("what is kept is stated", /settings are kept/i.test(text), text);
  check("irreversibility is stated", text.indexOf("cannot be undone") !== -1, text);

  const bare = describeRestore({});
  check("zero counts still read as words, not blanks",
        bare.indexOf("0 folders, 0 notes, 0 reminders") !== -1, bare);
  check("singular and plural are both handled",
        describeRestore({ current: { folders: 1, notes: 1, reminders: 1 } })
          .indexOf("1 folder, 1 note, 1 reminder") !== -1);
}

console.log("\n=== 6. buildMailtoHref -- refused rather than truncated ===");
{
  const small = buildMailtoHref({ to: "me@example.com", subject: "Notes backup", body: "# notes-backup v1\r\n" });
  check("a small backup produces a link", small.ok, JSON.stringify(small));
  check("...addressed to the user", small.href.startsWith("mailto:me@example.com?"), small.href);
  check("...carrying the subject", decodeURIComponent(small.href).indexOf("Notes backup") !== -1);
  check("...and the body", decodeURIComponent(small.href).indexOf("notes-backup") !== -1);
  equal("the limit is the documented one", small.limit, MAILTO_SAFE_LIMIT);

  const spaced = buildMailtoHref({ to: "  me@example.com  ", subject: "s", body: "b" });
  check("surrounding whitespace in the address is trimmed",
        spaced.ok && spaced.href.startsWith("mailto:me@example.com?"), spaced.href);

  const huge = buildMailtoHref({
    to: "me@example.com", subject: "Notes backup", body: "x".repeat(5000)
  });
  equal("an oversized backup is refused, not truncated", huge.ok, false);
  equal("...with the documented reason", huge.reason, "body-too-large");
  check("...reporting a length over the limit", huge.encodedLength > huge.limit,
        `${huge.encodedLength} vs ${huge.limit}`);

  // Exact boundary: the prefix length is measured, not assumed, so this stays
  // correct if the address or subject changes.
  const prefix = buildMailtoHref({ to: "me@example.com", subject: "s", body: "" });
  const room = prefix.href.length;
  const atLimit = buildMailtoHref({
    to: "me@example.com", subject: "s",
    body: "y".repeat(MAILTO_SAFE_LIMIT - room)
  });
  equal("a link exactly at the limit is allowed", atLimit.ok, true);
  const overLimit = buildMailtoHref({
    to: "me@example.com", subject: "s",
    body: "y".repeat(MAILTO_SAFE_LIMIT - room + 1)
  });
  equal("one character past the limit is refused", overLimit.ok, false);
}

console.log(`\n===== ${passed} passed, ${failed} failed =====`);
process.exit(failed ? 1 : 0);