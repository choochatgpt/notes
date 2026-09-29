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
  DEFAULT_RATIO,
  describeDeletion,
  describeRule,
  esc,
  folderOptions,
  folderPath,
  localInputValue,
  nextRatio,
  RATIOS,
  ratioToTracks,
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
  "the user's Tue + Thu example names the period as well as the days",
  describeRule({ kind: "weekly", interval: 1, weekdays: [2, 4] }),
  "Weekly · Tue, Thu"
);
equal(
  "weekly on an interval states the frequency and the days",
  describeRule({ kind: "weekly", interval: 2, weekdays: [2] }),
  "Every 2 weeks · Tue"
);
equal(
  "every 2 weeks with no weekdays",
  describeRule({ kind: "weekly", interval: 2, weekdays: [] }),
  "Every 2 weeks"
);
equal(
  "an unknown weekday in the rule is dropped rather than printed as undefined",
  describeRule({ kind: "weekly", interval: 1, weekdays: [2, 99] }),
  "Weekly · Tue"
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

// The user asked the home page to state the period AND the frequency. The
// failure this guards against is a label that states only one of them: "Tue,
// Thu" says which days but leaves the reader to infer whether it repeats weekly
// or fortnightly, which is the inference this label exists to remove.
for (const [name, rule] of [
  ["weekly with days, interval 1", { kind: "weekly", interval: 1, weekdays: [2, 4] }],
  ["weekly with days, interval 3", { kind: "weekly", interval: 3, weekdays: [2] }],
  ["weekly with no days", { kind: "weekly", interval: 1, weekdays: [] }],
  ["daily", { kind: "daily", interval: 1 }],
  ["daily, interval 4", { kind: "daily", interval: 4 }],
  ["monthly", { kind: "monthly", interval: 1 }],
  ["yearly", { kind: "yearly", interval: 1 }]
]) {
  const label = describeRule(rule);
  const named = /Weekly|Daily|Monthly|Yearly|Every [0-9]+ (day|days|week|weeks|month|months|year|years)/.test(label);
  check(`the label for ${name} names both the period and the frequency`, named, label);
}

// The days survive alongside the period rather than being replaced by it.
const named = describeRule({ kind: "weekly", interval: 1, weekdays: [2, 4] });
check("the period does not displace the weekdays",
   named.includes("Tue") && named.includes("Thu"), named);

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

console.log("\n=== 10. folderOptions -- the folder picker's contents and order ===");
{
  // Unfiled first, then the tree depth-first with siblings alphabetical.
  const tree = [
    { id: "home", parentId: null, name: "Home" },
    { id: "work", parentId: null, name: "Work" },
    { id: "apollo", parentId: "work", name: "Apollo" },
    { id: "hr", parentId: "work", name: "HR" },
    { id: "q3", parentId: "apollo", name: "Q3" }
  ];
  const options = folderOptions(tree);

  equal("Unfiled is offered first, as the null id", options[0].id, null);
  equal("Unfiled is labelled", options[0].name, "Unfiled");
  equal("Unfiled sits at depth 0", options[0].depth, 0);

  equal(
    "the tree is walked depth-first, siblings alphabetical",
    options.slice(1).map(o => o.name).join(","),
    "Home,Work,Apollo,Q3,HR"
  );
  equal(
    "a child is one level deeper than its parent",
    options.find(o => o.id === "apollo").depth,
    1
  );
  equal(
    "a grandchild is two levels deeper",
    options.find(o => o.id === "q3").depth,
    2
  );
  equal(
    "ids survive, so the picker can name its destination",
    options.map(o => o.id).join(","),
    ",home,work,apollo,q3,hr"
  );
}
{
  // A note can never be moved out of a folder the picker cannot name.
  const orphan = [
    { id: "lost", parentId: "gone", name: "Lost" },
    { id: "found", parentId: null, name: "Found" }
  ];
  const options = folderOptions(orphan);
  check(
    "a folder whose parent is missing is still offered",
    options.some(o => o.id === "lost")
  );
  equal(
    "an orphan is flattened to depth 0 rather than indented under nothing",
    options.find(o => o.id === "lost").depth,
    0
  );
  equal(
    "reachable folders still come first",
    options.map(o => o.id).join(","),
    ",found,lost"
  );
}
{
  const cyc = [
    { id: "x", parentId: "y", name: "X" },
    { id: "y", parentId: "x", name: "Y" }
  ];
  const options = folderOptions(cyc);
  equal("a parent cycle terminates and lists each folder once", options.length, 3);
}
{
  // A cycle that hangs off the root must also terminate, not revisit the parent.
  const loop = [
    { id: "a", parentId: null, name: "A" },
    { id: "b", parentId: "a", name: "B" }
  ];
  loop[0].parentId = "b";
  const options = folderOptions(loop);
  equal("a cycle among reachable folders still lists each once", options.length, 3);
}
{
  equal("no folders still offers somewhere to file", folderOptions([]).length, 1);
  equal("...and that is Unfiled", folderOptions([])[0].id, null);
  equal("undefined folders is handled too", folderOptions(undefined).length, 1);
}
{
  const missingName = [{ id: "a", parentId: null, name: undefined }];
  equal(
    "a nameless folder sorts without throwing",
    folderOptions(missingName).length,
    2
  );
}

console.log("\n=== 11. pane ratio cycle -- 20/40/60/80 top share, then wrap ===");
{
  // The user's four requested shares of the top pane, in cycle order. The
  // default 50% is deliberately NOT in the list -- it is where a fresh device
  // starts (and the CSS fallback), and the first click leaves it for 20%.
  equal("the offered ratios, in cycle order",
        RATIOS.join(","), "20%,40%,60%,80%");
  equal("the default is the even split", DEFAULT_RATIO, "50%");
  check("the default is not one of the four, per the 2026-09-29 revision",
        !RATIOS.includes(DEFAULT_RATIO));

  equal("the first click from the default lands on 20%", nextRatio(DEFAULT_RATIO), "20%");
  equal("...then up the requested list", nextRatio("20%"), "40%");
  equal("mid-cycle advances", nextRatio("40%"), "60%");
  equal("the last share wraps to the first", nextRatio("80%"), "20%");

  const visited = [];
  let cursor = "20%";
  for (let i = 0; i < RATIOS.length; i += 1) {
    cursor = nextRatio(cursor);
    visited.push(cursor);
  }
  equal("four clicks from the first share return to it", cursor, "20%");
  equal("...having visited every offered share exactly once (last step wraps)",
        visited.join(","), "40%,60%,80%,20%");

  equal("an unknown stored value restarts at the first share",
        nextRatio("banana"), "20%");
  equal("a missing value is treated the same way", nextRatio(null), "20%");
  equal("an empty value too", nextRatio(""), "20%");
  equal("a stored ratio from the old a:b release also restarts",
        nextRatio("1:4"), "20%");
}

console.log("\n=== 12. ratioToTracks -- top share to grid tracks ===");
{
  const tracks = ratioToTracks("40%");
  equal("top track", tracks.top, "40fr");
  equal("bottom track", tracks.bottom, "60fr");
  equal("the low end splits 20/80",
        ratioToTracks("20%").top + "/" + ratioToTracks("20%").bottom,
        "20fr/80fr");
  equal("the high end splits 80/20",
        ratioToTracks("80%").top + "/" + ratioToTracks("80%").bottom,
        "80fr/20fr");
  equal("the default 50% splits evenly",
        ratioToTracks(DEFAULT_RATIO).top + "/" + ratioToTracks(DEFAULT_RATIO).bottom,
        "50fr/50fr");
  equal("an unparseable value falls back to the even split",
        ratioToTracks("banana").top + "/" + ratioToTracks("banana").bottom,
        "1fr/1fr");
  equal("undefined falls back too",
        ratioToTracks(undefined).top + "/" + ratioToTracks(undefined).bottom,
        "1fr/1fr");
  equal("the old a:b format from a previous release reads as the default, not junk",
        ratioToTracks("1:4").top + "/" + ratioToTracks("1:4").bottom,
        "1fr/1fr");
  equal("an out-of-range share is refused rather than asked for",
        ratioToTracks("140%").top + "/" + ratioToTracks("140%").bottom,
        "1fr/1fr");
  check("the fallback is a real fraction pair, so the grid never gets junk",
        /^(1fr)$/.test(ratioToTracks("junk!").top));
}

console.log(`\n===== ${passed} passed, ${failed} failed =====`);
process.exit(failed ? 1 : 0);
