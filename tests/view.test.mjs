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
  APP_VERSION,
  absoluteLabel,
  collectSubtree,
  DEFAULT_RATIO,
  describeDeletion,
  describeRule,
  esc,
  folderOptions,
  folderChain,
  folderPath,
  localInputValue,
  nextRatio,
  noteSnippet,
  noteDisplayTitle,
  attachmentBadge,
  RATIOS,
  ratioToTracks,
  relativeFromNow,
  SNIPPET_LINE_CHARS,
  sortFoldersSiblings,
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

console.log("\n=== 4b. folderChain -- the breadcrumb's tappable segments ===");
{
  // folderPath for the drill-down era (v38): the same walk, but the segments
  // keep their ids and the pseudo-root "Notes" always leads, because the
  // browser must offer a way back even at the top of the tree.
  const folders = [
    { id: "w", parentId: null, name: "Work" },
    { id: "p", parentId: "w", name: "Projects" },
    { id: "a", parentId: "p", name: "Apollo" },
    { id: "h", parentId: null, name: "Home" }
  ];

  equal("at the root the chain is just the pseudo-root",
        JSON.stringify(folderChain(null, folders)),
        JSON.stringify([{ id: null, name: "Notes" }]));
  equal("...and undefined degrades the same way",
        JSON.stringify(folderChain(undefined, folders)),
        JSON.stringify([{ id: null, name: "Notes" }]));

  const chain = folderChain("a", folders);
  equal("a deep folder chains root-first",
        chain.map(seg => seg.name).join(" › "), "Notes › Work › Projects › Apollo");
  equal("every segment keeps its id, null's segment as null",
        chain.map(seg => String(seg.id)).join("|"), "null|w|p|a");

  equal("a mid-depth folder chains exactly its ancestors",
        folderChain("p", folders).map(seg => seg.name).join(" › "),
        "Notes › Work › Projects");
  equal("missing ids degrade to the bare root",
        JSON.stringify(folderChain("nope", folders)),
        JSON.stringify([{ id: null, name: "Notes" }]));
  equal("a missing folder list degrades to the bare root",
        JSON.stringify(folderChain("a", undefined)),
        JSON.stringify([{ id: null, name: "Notes" }]));

  // A dangling parentId must not hang the bar either -- the walker stops
  // where the parents stop, which is one chain that simply lacks its top.
  const dangling = [{ id: "o", parentId: "gone", name: "Orphan" }];
  equal("a folder whose parent is missing chains up to where the parents stop",
        folderChain("o", dangling).map(seg => seg.name).join(" › "),
        "Notes › Orphan");

  // The same hand-edited hazard folderPath guards: a looped parent chain
  // must terminate and every id must appear once.
  const cyc = [
    { id: "x", parentId: "y", name: "X" },
    { id: "y", parentId: "x", name: "Y" }
  ];
  const looped = folderChain("x", cyc);
  check("a parent cycle terminates with each id appearing once",
        looped.length === 3 && looped[0].name === "Notes"
          && ["X", "Y"].includes(looped[1].name)
          && ["X", "Y"].includes(looped[2].name));
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
    "the tree is walked depth-first, siblings alphabetical while none carries an order",
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

console.log(
  "\n=== 10b. sortFoldersSiblings -- arranged folders first, alphabetical tail ==="
);
{
  const group = [
    { id: "b", parentId: null, name: "Bravo" },
    { id: "a", parentId: null, name: "Alpha", order: 1 },
    { id: "z", parentId: null, name: "Zulu", order: 0 }
  ];
  const sorted = sortFoldersSiblings(group);
  equal(
    "ordered folders precede unordered ones",
    sorted.map(row => row.id).join(","),
    "z,a,b"
  );
  equal("the input array is not mutated", group.map(row => row.id).join(","), "b,a,z");

  equal(
    "ordered siblings sort by order ascending",
    sortFoldersSiblings([
      { id: "3", name: "Three", order: 2 },
      { id: "1", name: "One", order: 0 },
      { id: "2", name: "Two", order: 1 }
    ]).map(row => row.id).join(","),
    "1,2,3"
  );
  equal(
    "equal order values break on name",
    sortFoldersSiblings([
      { id: "b", name: "Charlie", order: 4 },
      { id: "a", name: "Alpha", order: 4 }
    ]).map(row => row.id).join(","),
    "a,b"
  );
  equal(
    "the unordered tail sorts alphabetically",
    sortFoldersSiblings([
      { id: "b", parentId: null, name: "Bravo", order: 0 },
      { id: "d", parentId: null, name: "Delta" },
      { id: "c", parentId: null, name: "Charlie" }
    ]).map(row => row.id).join(","),
    "b,c,d"
  );
  equal(
    "a non-numeric order does not count as ordered",
    sortFoldersSiblings([
      { id: "junk", name: "Junk", order: "3.5" },
      { id: "hex", name: "Hex", order: "0x2" },
      { id: "text", name: "Text", order: "abc" },
      { id: "real", name: "Real", order: 0 }
    ]).map(row => row.id).join(","),
    "real,hex,junk,text"
  );
  equal(
    "a single-element group sorts to itself",
    sortFoldersSiblings([{ id: "solo", name: "Solo" }]).length,
    1
  );
  equal("an empty group is an empty list", sortFoldersSiblings([]).length, 0);

  // The picker mirrors the arranged tree: the note editor's folder list must
  // read exactly like the folder column.
  const arranged = [
    { id: "b", parentId: null, name: "Bravo" },
    { id: "a", parentId: null, name: "Alpha", order: 1 },
    { id: "z", parentId: null, name: "Zulu", order: 0 }
  ];
  equal(
    "the picker mirrors the arranged tree",
    folderOptions(arranged).map(o => o.name).join(","),
    "Unfiled,Zulu,Alpha,Bravo"
  );
  // A folder created after a rearrange carries no order and appends at the end;
  // it must never wedge itself into the middle of an arrangement already made.
  const appended = arranged.concat([{ id: "new", parentId: null, name: "New" }]);
  equal(
    "a new folder without an order lands last in the picker",
    folderOptions(appended).map(o => o.name).join(","),
    "Unfiled,Zulu,Alpha,Bravo,New"
  );
}

console.log("\n=== 11. pane ratio cycle -- 20/40/60/80 top share, then wrap ===");
{
  // The user's requested ten-percent steps of the top pane, in cycle order
  // (2026-10-05 revision; before it was 20/40/60/80). The default even split
  // is now one of the stops, so the first click from a fresh device simply
  // advances 50% -> 60%.
  equal("the offered ratios, in cycle order",
        RATIOS.join(","), "10%,20%,30%,40%,50%,60%,70%,80%,90%");
  equal("the default is the even split", DEFAULT_RATIO, "50%");
  check("the default is one of the offered stops since the 10%-step revision",
        RATIOS.includes(DEFAULT_RATIO));

  equal("the first click from the even split advances to 60%",
        nextRatio(DEFAULT_RATIO), "60%");
  equal("...and the low end climbs", nextRatio("10%"), "20%");
  equal("mid-cycle advances", nextRatio("40%"), "50%");
  equal("the last share wraps to the first", nextRatio("90%"), "10%");

  const visited = [];
  let cursor = "10%";
  for (let i = 0; i < RATIOS.length; i += 1) {
    cursor = nextRatio(cursor);
    visited.push(cursor);
  }
  equal("nine clicks from the first share return to it", cursor, "10%");
  equal("...having visited every offered share exactly once (last step wraps)",
        visited.join(","), "20%,30%,40%,50%,60%,70%,80%,90%,10%");

  equal("an unknown stored value restarts at the first share",
        nextRatio("banana"), "10%");
  equal("a missing value is treated the same way", nextRatio(null), "10%");
  equal("an empty value too", nextRatio(""), "10%");
  equal("a stored ratio from the old a:b release also restarts",
        nextRatio("1:4"), "10%");
}

console.log("\n=== 12. ratioToTracks -- top share to grid tracks ===");
{
  const tracks = ratioToTracks("40%");
  equal("top track", tracks.top, "40fr");
  equal("bottom track", tracks.bottom, "60fr");
  equal("the low end splits 20/80",
        ratioToTracks("20%").top + "/" + ratioToTracks("20%").bottom,
        "20fr/80fr");
  equal("the lowest offered step splits 10/90",
        ratioToTracks("10%").top + "/" + ratioToTracks("10%").bottom,
        "10fr/90fr");
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

console.log("\n=== 12. noteSnippet -- the list preview keeps the author's line breaks ===");
equal(
  "empty bodies yield an empty preview",
  noteSnippet(""),
  ""
);
equal(
  "null/undefined yield an empty preview",
  noteSnippet(null) + "/" + noteSnippet(undefined),
  "/"
);
equal(
  "a single short line passes through unchanged",
  noteSnippet("Buy milk"),
  "Buy milk"
);
equal(
  "Enter keys survive -- the bug this replaces flattened every line into one",
  noteSnippet("first\nsecond\nthird"),
  "first\nsecond\nthird"
);
equal(
  "CRLF bodies normalise to LF, so a pasted Windows note previews the same",
  noteSnippet("alpha\r\nbeta"),
  "alpha\nbeta"
);
equal(
  "leading and trailing blank space is trimmed away",
  noteSnippet("\n\n  deep thought  \n\n"),
  "deep thought"
);
equal(
  "at most four lines are kept",
  noteSnippet("1\n2\n3\n4\n5\n6"),
  "1\n2\n3\n4…"
);
equal(
  "a line far past the char cap truncates with an ellipsis",
  noteSnippet("x".repeat(200)).length,
  SNIPPET_LINE_CHARS + 1
);
check("...and that long-line truncation actually ends in the ellipsis",
      noteSnippet("y".repeat(200)).endsWith("…"));
equal(
  "internal runs of spaces collapse but the line stays one line",
  noteSnippet("a    b"),
  "a b"
);
check(
  "four short lines fit without any ellipsis, so an ordinary note is never cut",
  noteSnippet("l1\nl2\nl3\nl4") === "l1\nl2\nl3\nl4"
);
check(
  "a five-line note keeps its first four then marks the cut",
  noteSnippet("l1\nl2\nl3\nl4\nl5") === "l1\nl2\nl3\nl4…"
);

console.log("\n=== 12d. attachmentBadge -- the note row's attachment-count line ===");
const png = () => ({ type: "image/png", name: "a.png" });
const jpeg = () => ({ type: "image/jpeg", name: "b.jpg" });
const pdfRecord = () => ({ type: "application/pdf", name: "doc.pdf" });
equal(
  "nothing attached yields no line",
  String(attachmentBadge([])) + "/" + String(attachmentBadge(undefined)),
  "null/null"
);
equal(
  "one kind reads as just that group -- the total would be the same number twice",
  attachmentBadge([png(), png()]).text,
  "2 png"
);
equal(
  "the request that seeded v34: five jpegs and two PDFs",
  attachmentBadge([
    jpeg(), jpeg(), jpeg(), jpeg(), jpeg(), pdfRecord(), pdfRecord()
  ]).text,
  "7 · 5 jpg · 2 pdf"
);
check(
  "...and its title always carries the full breakdown",
  attachmentBadge([jpeg(), jpeg(), jpeg(), jpeg(), jpeg(), pdfRecord(), pdfRecord()]).title
    === "7 attachments: 5 jpg, 2 pdf"
);
check(
  "image/jpg folds in with image/jpeg -- one jpg group, not two",
  attachmentBadge([jpeg(), { type: "image/jpg" }]).text === "2 jpg"
);
equal(
  "mixed kinds lead with the total, biggest group first",
  attachmentBadge([png(), jpeg(), jpeg()]).text,
  "3 · 2 jpg · 1 png"
);
equal(
  "a tie breaks on the label, alphabetical",
  attachmentBadge([png(), png(), jpeg(), jpeg()]).text,
  "4 · 2 jpg · 2 png",
);
equal(
  "videos group under one word whatever their subtype",
  attachmentBadge([{ type: "video/mp4" }, { type: "video/quicktime" }, { type: "video/mp4" }]).text,
  "3 video"
);
equal(
  "an unusual image subtype keeps its own name",
  attachmentBadge([{ type: "image/heic" }]).text,
  "1 heic"
);
equal(
  "a typeless record falls back to its file name's extension",
  attachmentBadge([{ type: "", name: "scan.PDF" }]).text,
  "1 pdf"
);
equal(
  "a typeless .jpeg file folds into the jpg group too",
  attachmentBadge([{ type: "", name: "photo.jpeg" }, jpeg()]).text,
  "2 jpg"
);
equal(
  "a typeless record with no extension counts as a file",
  attachmentBadge([{ type: "", name: "readme" }]).text,
  "1 file"
);
equal(
  "dangling/junk entries are filtered away rather than counted",
  attachmentBadge([{ type: "application/pdf" }, null]).text,
  "1 pdf",
);

console.log("\n=== 12f. noteDisplayTitle -- an untitled note is titled after its folder (v37) ===");
{
  // A titled note keeps its own title -- the folder only answers for the
  // untitled ones, whatever folder they sit in.
  equal("a titled note keeps its own (trimmed) title",
        noteDisplayTitle({ title: "  BD -Alton ", folderId: "f" },
          [{ id: "f", name: "2024" }]),
        "BD -Alton");
  equal("an untitled unfiled note reads Unfiled -- the pseudo-folder's own label",
        noteDisplayTitle({ title: "", folderId: null }, [{ id: "f", name: "2024" }]),
        "Unfiled");
  equal("...and one with no folderId field at all does too (older rows)",
        noteDisplayTitle({ title: null }, [{ id: "f", name: "2024" }]),
        "Unfiled");
  equal("an untitled note in a folder reads the folder's name",
        noteDisplayTitle({ title: "", folderId: "f" }, [{ id: "f", name: "2024" }]),
        "2024");
  equal("a folder outside the tree degrades to the placeholder",
        noteDisplayTitle({ title: "", folderId: "gone" }, [{ id: "f", name: "2024" }]),
        "Untitled note");
  equal("a whitespace-only folder name degrades to the placeholder too",
        noteDisplayTitle({ title: "", folderId: "f" }, [{ id: "f", name: "   " }]),
        "Untitled note");
  equal("no folders to look in degrades rather than crashing",
        noteDisplayTitle({ title: "", folderId: "f" }, null),
        "Untitled note");
  equal("undefined folders degrades the same way",
        noteDisplayTitle({ title: "", folderId: "f" }, undefined),
        "Untitled note");
  equal("a null note degrades to the placeholder",
        noteDisplayTitle(null, [{ id: "f", name: "2024" }]),
        "Untitled note");
}

console.log("\n=== 13. APP_VERSION -- the release number the user can see ===");
check("APP_VERSION is a bare number the dialog can show verbatim",
      /^\d+$/.test(APP_VERSION), APP_VERSION);
check("the version moved past the one-word-per-line release (17)",
      Number(APP_VERSION) >= 18, APP_VERSION);

console.log(`\n===== ${passed} passed, ${failed} failed =====`);
process.exit(failed ? 1 : 0);
