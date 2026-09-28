/**
 * Tests for the pure view helpers.
 *
 *   node tests/view.test.mjs [path-to-view.js]
 *   Exit: 0 = all pass, 1 = one or more failures
 *
 * The module path defaults to ../source/view.js (durable-mirror layout); pass
 * ../view.js when running inside the flat published/active layout.
 *
 * Covers the behaviours the UI depends on: reminders ordered soonest first
 * (with spent one-offs last, not first), recurrence rules rendered as something
 * a human can read at a glance, relative-time buckets, folder paths, and the
 * escaping that keeps a folder name out of the markup.
 */
// Resolve against this file, not the caller's cwd, so the harness works from
// either the flat workspace layout or the durable-mirror one.
const resolved = new URL(process.argv[2] || "../source/view.js", import.meta.url);
const {
  absoluteLabel,
  collectSubtree,
  describeDeletion,
  describeRule,
  esc,
  folderPath,
  localInputValue,
  relativeFromNow,
  sortReminders
} = await import(resolved.href);

let passed = 0;
let failed = 0;

function check(name, condition) {
  if (condition) {
    passed += 1;
    console.log(`PASS  ${name}`);
  } else {
    failed += 1;
    console.log(`FAIL  ${name}`);
  }
}

function equal(name, actual, expected) {
  const ok = actual === expected;
  if (!ok) console.log(`      expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  check(name, ok);
}

const RTF = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
const DAY = 86400000;
const HOUR = 3600000;

console.log("=== 1. sortReminders -- soonest upcoming first ===");
{
  const noon = Date.parse("2026-10-01T12:00:00Z");
  const soon = { title: "Soon", nextDueAt: new Date(noon + HOUR).toISOString() };
  const later = { title: "Later", nextDueAt: new Date(noon + 5 * DAY).toISOString() };
  const last = { title: "Last", nextDueAt: new Date(noon + 40 * DAY).toISOString() };
  const spent = { title: "Spent", nextDueAt: null };

  const sorted = sortReminders([later, spent, last, soon]);
  equal("order is ascending by nextDueAt", sorted.map(r => r.title).join(","), "Soon,Later,Last,Spent");
  equal("the spent one-off lands last, not first", sorted[sorted.length - 1].title, "Spent");
}
{
  // A null must not be treated as 0 / epoch, which would float it to the top.
  const sorted = sortReminders([
    { title: "No due", nextDueAt: null },
    { title: "Has due", nextDueAt: "2026-10-01T12:00:00Z" }
  ]);
  equal("null does not sort as the epoch", sorted[0].title, "Has due");
}
{
  const sorted = sortReminders([
    { title: "Bravo", nextDueAt: "2026-10-01T12:00:00Z" },
    { title: "Alpha", nextDueAt: "2026-10-01T12:00:00Z" }
  ]);
  equal("ties break on title for a stable order", sorted.map(r => r.title).join(","), "Alpha,Bravo");
}
{
  const input = [{ title: "B", nextDueAt: null }, { title: "A", nextDueAt: null }];
  const copy = [...input];
  sortReminders(input);
  equal("input array is not mutated", input.map(r => r.title).join(","), copy.map(r => r.title).join(","));
}
check("empty list is handled", sortReminders([]).length === 0);

console.log("\n=== 2. describeRule -- recurrence in plain words ===");
equal("once", describeRule({ kind: "once" }), "Once");
equal("missing rule falls back to once", describeRule(undefined), "Once");
equal("unknown kind falls back to once", describeRule({ kind: "fortnightly" }), "Once");
equal("daily", describeRule({ kind: "daily", interval: 1 }), "Daily");
equal("every 3 days", describeRule({ kind: "daily", interval: 3 }), "Every 3 days");
equal("weekly with no weekdays", describeRule({ kind: "weekly", interval: 1, weekdays: [] }), "Weekly");
equal(
  "the user's Tue + Thu example",
  describeRule({ kind: "weekly", interval: 1, weekdays: [2, 4] }),
  "Tue, Thu"
);
equal(
  "weekly on an interval names both",
  describeRule({ kind: "weekly", interval: 2, weekdays: [2] }),
  "Tue · 2wk"
);
equal("monthly", describeRule({ kind: "monthly", interval: 1 }), "Monthly");
equal("every 6 months", describeRule({ kind: "monthly", interval: 6 }), "Every 6 months");
equal("the birthday example", describeRule({ kind: "yearly", interval: 1 }), "Yearly");
equal("the passport example", describeRule({ kind: "yearly", interval: 5 }), "Every 5 years");
equal("interval below 1 is clamped", describeRule({ kind: "daily", interval: 0 }), "Daily");
equal(
  "a non-numeric interval is clamped, not printed",
  describeRule({ kind: "daily", interval: "banana" }),
  "Daily"
);

console.log("\n=== 3. relativeFromNow -- sign and unit ===");
{
  const now = Date.parse("2026-10-01T12:00:00Z");
  const at = offset => new Date(now + offset).toISOString();

  equal("3 days ahead", relativeFromNow(at(3 * DAY), now), RTF.format(3, "day"));
  equal("3 days ago", relativeFromNow(at(-3 * DAY), now), RTF.format(-3, "day"));
  equal("2 hours ahead", relativeFromNow(at(2 * HOUR), now), RTF.format(2, "hour"));
  equal("23 hours stays in hours", relativeFromNow(at(23 * HOUR), now), RTF.format(23, "hour"));
  equal("exactly a day becomes days", relativeFromNow(at(DAY), now), RTF.format(1, "day"));
  equal("one minute short of a day is still hours, rounded", relativeFromNow(at(DAY - 60000), now), RTF.format(24, "hour"));
  equal("just over a day is days, not hours", relativeFromNow(at(DAY + 60000), now), RTF.format(1, "day"));
  equal("40 days reads as a month", relativeFromNow(at(40 * DAY), now), RTF.format(1, "month"));
  equal("400 days reads as a year", relativeFromNow(at(400 * DAY), now), RTF.format(1, "year"));
  equal("an unparseable date yields nothing", relativeFromNow("not-a-date", now), "");
  check("past and future never render the same", relativeFromNow(at(DAY), now) !== relativeFromNow(at(-DAY), now));
}

console.log("\n=== 4. folderPath -- nested names ===");
{
  const folders = [
    { id: "w", parentId: null, name: "Work" },
    { id: "p", parentId: "w", name: "Projects" },
    { id: "a", parentId: "p", name: "Apollo" },
    { id: "h", parentId: null, name: "Home" }
  ];
  equal("three levels deep", folderPath("a", folders), "Work / Projects / Apollo");
  equal("one level", folderPath("h", folders), "Home");
  equal("unknown id yields empty", folderPath("nope", folders), "");
  equal("null id yields empty", folderPath(null, folders), "");
  equal("missing folder list yields empty", folderPath("a", undefined), "");
}
{
  // A cycle would hang a naive walker, so it must be guarded.
  const cyc = [
    { id: "x", parentId: "y", name: "X" },
    { id: "y", parentId: "x", name: "Y" }
  ];
  const path = folderPath("x", cyc);
  check("a parent cycle terminates instead of hanging", path === "Y / X" || path === "X / Y");
}

console.log("\n=== 5. esc -- safe in text and in attributes ===");
equal("ampersand", esc("A & B"), "A &amp; B");
equal("angle brackets", esc("<script>"), "&lt;script&gt;");
equal("double quote cannot break out of an attribute", esc('a" onmouseover="x'), "a&quot; onmouseover=&quot;x");
equal("single quote is escaped too", esc("it's"), "it&#39;s");
equal("null becomes empty", esc(null), "");
equal("numbers survive", esc(42), "42");

console.log("\n=== 6. localInputValue -- datetime-local format ===");
{
  const value = localInputValue(new Date(2026, 9, 1, 15, 4));
  equal("pads month, day, hour and minute", value, "2026-10-01T15:04");
  check("matches the input element's expected shape", /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value));
}

console.log("\n=== 7. absoluteLabel -- non-empty and stable ===");
check("a valid date produces a label", absoluteLabel("2026-10-01T12:00:00Z").length > 0);

console.log("\n=== 8. collectSubtree -- everything a folder delete takes with it ===");
{
  const tree = [
    { id: "work", parentId: null, name: "Work" },
    { id: "hr", parentId: "work", name: "HR" },
    { id: "it", parentId: "work", name: "IT" },
    { id: "proj", parentId: "work", name: "Projects" },
    { id: "apollo", parentId: "proj", name: "Apollo" },
    { id: "zeus", parentId: "proj", name: "Zeus" },
    { id: "home", parentId: null, name: "Home" },
    { id: "friends", parentId: "home", name: "Friends" }
  ];

  const work = collectSubtree("work", tree);
  equal("a nested delete takes the whole subtree", new Set(work).size, 6);
  check("...including the folder itself", work.includes("work"));
  check("...and the deepest descendant", work.includes("apollo"));
  check("...but nothing from a sibling branch", !work.includes("home") && !work.includes("friends"));

  const leaf = collectSubtree("hr", tree);
  equal("a childless folder takes only itself", leaf.length, 1);

  equal("an unknown id takes only itself", collectSubtree("ghost", tree).length, 1);
  equal("no folders at all still terminates", collectSubtree("x", undefined).length, 1);
  equal("an empty array still terminates", collectSubtree("x", []).length, 1);

  // Deleting two branches in a row must not double-count or drop ids.
  const seen = new Set([...collectSubtree("work", tree), ...collectSubtree("home", tree)]);
  equal("two deletes cover both branches exactly once", seen.size, 8);
}
{
  // A parent cycle would hang a naive walker, so it must be guarded.
  const cyc = [
    { id: "x", parentId: "y", name: "X" },
    { id: "y", parentId: "x", name: "Y" },
    { id: "z", parentId: "x", name: "Z" }
  ];
  const ids = collectSubtree("x", cyc);
  equal("a parent cycle terminates and returns each id once", new Set(ids).size, ids.length);
  check("...and still reaches the whole component", ids.length === 3);
}

console.log("\n=== 9. describeDeletion -- counts stated before the point of no return ===");
equal(
  "subfolders and notes are both named",
  describeDeletion("Work", { subfolders: 3, notes: 12 }),
  'Delete "Work"? This also deletes 3 subfolders and 12 notes. This cannot be undone.'
);
equal(
  "one of each reads in the singular",
  describeDeletion("Work", { subfolders: 1, notes: 1 }),
  'Delete "Work"? This also deletes 1 subfolder and 1 note. This cannot be undone.'
);
equal(
  "subfolders only",
  describeDeletion("Work", { subfolders: 2, notes: 0 }),
  'Delete "Work"? This also deletes 2 subfolders. This cannot be undone.'
);
equal(
  "notes only",
  describeDeletion("Home", { notes: 5 }),
  'Delete "Home"? This also deletes 5 notes. This cannot be undone.'
);
equal(
  "an empty folder claims nothing extra",
  describeDeletion("Empty", { subfolders: 0, notes: 0 }),
  'Delete "Empty"? This cannot be undone.'
);
equal(
  "counts are omitted rather than printed as zero",
  describeDeletion("Empty", {}).includes("0 "),
  false
);
// confirm() renders plain text, so the name is deliberately NOT escaped here --
// escaping would show the user a literal "&lt;".
equal(
  "the name is carried verbatim, since confirm() is not HTML",
  describeDeletion("<b>Bold</b>", { notes: 1 }),
  'Delete "<b>Bold</b>"? This also deletes 1 note. This cannot be undone.'
);

console.log(`\n===== ${passed} passed, ${failed} failed =====`);
process.exit(failed ? 1 : 0);
