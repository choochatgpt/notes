/**
 * Recurrence engine validation — Notes (2026-09-27-01-Notes)
 *
 * Run:  node tests/recurrence.test.mjs [path-to-reminder.js]
 * Exit: 0 = all pass, 1 = one or more failures
 *
 * The module path defaults to ../source/reminder.js (durable-mirror layout);
 * pass ../reminder.js when running inside the flat active workspace.
 *
 * Covers the schedules the user specified:
 *   - every Tue + Thu at 15:00
 *   - a yearly birthday
 *   - every 5 years (passport renewal)
 * plus the once / daily / monthly rule families and rule normalization.
 */

// Resolve against this file, not the caller's cwd, so the harness works from
// either the durable-mirror layout or the flat active workspace.
const resolved = new URL(process.argv[2] || "../source/reminder.js", import.meta.url);
const { nextOccurrence, normalizeRule, reminderWithNextDue } = await import(resolved.href);

const pad = value => String(value).padStart(2, "0");

const iso = date => {
  if (!date) return "null";
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
         `${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

/** Local-time constructor; month is 1-based here for readability. */
const at = (year, month, day, hour = 0, minute = 0) =>
  new Date(year, month - 1, day, hour, minute, 0, 0);

const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

let pass = 0;
let fail = 0;

function check(label, actual, expected) {
  const got = iso(actual);
  const ok = got === expected;
  if (ok) pass += 1; else fail += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}`);
  if (!ok) {
    console.log(`        expected ${expected}`);
    console.log(`        actual   ${got}`);
  }
}

console.log("=== weekday sanity (guards the fixtures below) ===");
for (const [y, m, d] of [[2026, 9, 28], [2026, 9, 29], [2026, 10, 1], [2026, 10, 3]]) {
  console.log(`  ${y}-${pad(m)}-${pad(d)} = ${DOW[at(y, m, d).getDay()]}`);
}

console.log("\n=== 1. ONCE ===");
check("start in the future -> that instant",
  nextOccurrence(at(2026, 10, 1, 9), { kind: "once" }, at(2026, 9, 28, 12)),
  "2026-10-01 09:00");
check("start already past -> null (never re-fires)",
  nextOccurrence(at(2026, 9, 1, 9), { kind: "once" }, at(2026, 9, 28, 12)),
  "null");

console.log("\n=== 2. DAILY INTERVAL ===");
check("every 1 day",
  nextOccurrence(at(2026, 9, 28, 8), { kind: "daily", interval: 1 }, at(2026, 9, 28, 12)),
  "2026-09-29 08:00");
check("every 3 days: Sep 28 08:00 already past -> Oct 1",
  nextOccurrence(at(2026, 9, 1, 8), { kind: "daily", interval: 3 }, at(2026, 9, 28, 12)),
  "2026-10-01 08:00");
check("every 3 days: checked before the Sep 28 occurrence -> Sep 28",
  nextOccurrence(at(2026, 9, 1, 8), { kind: "daily", interval: 3 }, at(2026, 9, 28, 7)),
  "2026-09-28 08:00");
check("start in the future wins over interval",
  nextOccurrence(at(2026, 10, 5, 8), { kind: "daily", interval: 1 }, at(2026, 9, 28, 12)),
  "2026-10-05 08:00");

console.log("\n=== 3. WEEKLY — every Tue + Thu at 15:00 (user's example) ===");
const tueThu = { kind: "weekly", interval: 1, weekdays: [2, 4] };
check("from Mon 2026-09-28 12:00 -> Tue 15:00",
  nextOccurrence(at(2026, 9, 21, 15), tueThu, at(2026, 9, 28, 12)),
  "2026-09-29 15:00");
check("from Tue 2026-09-29 16:00 (just after) -> Thu 15:00",
  nextOccurrence(at(2026, 9, 21, 15), tueThu, at(2026, 9, 29, 16)),
  "2026-10-01 15:00");
check("from Thu 2026-10-01 15:00 (exactly due) -> next Tue",
  nextOccurrence(at(2026, 9, 21, 15), tueThu, at(2026, 10, 1, 15)),
  "2026-10-06 15:00");
check("every 2 weeks, Tuesday only",
  nextOccurrence(at(2026, 9, 1, 15), { kind: "weekly", interval: 2, weekdays: [2] }, at(2026, 9, 28, 12)),
  "2026-09-29 15:00");

console.log("\n=== 4. MONTHLY INTERVAL ===");
check("every month, day 15",
  nextOccurrence(at(2026, 1, 15, 9), { kind: "monthly", interval: 1 }, at(2026, 9, 28, 12)),
  "2026-10-15 09:00");
check("day 31 skips Feb (Feb 31 invalid) -> Mar 31",
  nextOccurrence(at(2026, 1, 31, 9), { kind: "monthly", interval: 1 }, at(2026, 2, 1, 0)),
  "2026-03-31 09:00");
check("every 6 months",
  nextOccurrence(at(2026, 1, 10, 9), { kind: "monthly", interval: 6 }, at(2026, 2, 1, 0)),
  "2026-07-10 09:00");

console.log("\n=== 5. YEARLY — birthday (user's example) ===");
check("birthday Mar 15, next after Jan 1",
  nextOccurrence(at(1990, 3, 15, 0), { kind: "yearly", interval: 1 }, at(2026, 1, 1, 0)),
  "2026-03-15 00:00");
check("birthday Mar 15 already passed -> next year",
  nextOccurrence(at(1990, 3, 15, 0), { kind: "yearly", interval: 1 }, at(2026, 9, 28, 12)),
  "2027-03-15 00:00");
check("Feb 29 birthday skips non-leap years -> 2028",
  nextOccurrence(at(2000, 2, 29, 0), { kind: "yearly", interval: 1 }, at(2026, 1, 1, 0)),
  "2028-02-29 00:00");

console.log("\n=== 6. EVERY 5 YEARS — passport renewal (user's example) ===");
check("from 2001-06-10, checked 2026-09-28 -> 2031",
  nextOccurrence(at(2001, 6, 10, 9), { kind: "yearly", interval: 5 }, at(2026, 9, 28, 12)),
  "2031-06-10 09:00");
check("from 2001-06-10, checked 2025-01-01 -> 2026",
  nextOccurrence(at(2001, 6, 10, 9), { kind: "yearly", interval: 5 }, at(2025, 1, 1, 0)),
  "2026-06-10 09:00");

console.log("\n=== 7. RULE NORMALIZATION / ROBUSTNESS ===");
check("unknown kind falls back to once",
  normalizeRule({ kind: "bogus" }).kind === "once" ? at(2026, 1, 1) : null,
  "2026-01-01 00:00");
check("weekdays are deduped, sorted and range-filtered",
  JSON.stringify(normalizeRule({ kind: "weekly", weekdays: [4, 2, 2, 9, -1] }).weekdays) === "[2,4]"
    ? at(2026, 1, 1) : null,
  "2026-01-01 00:00");
check("interval below 1 is clamped to 1",
  normalizeRule({ kind: "daily", interval: 0 }).interval === 1 ? at(2026, 1, 1) : null,
  "2026-01-01 00:00");
check("weekly with no weekdays falls back to the start weekday (Mon)",
  nextOccurrence(at(2026, 9, 21, 15), { kind: "weekly", interval: 1, weekdays: [] }, at(2026, 9, 28, 12)),
  "2026-09-28 15:00");
check("disabled reminder yields no next due date",
  reminderWithNextDue(
    { enabled: false, startAt: at(2026, 10, 1, 9).toISOString(), recurrence: { kind: "once" } },
    at(2026, 9, 28, 12)
  ).nextDueAt === null ? at(2026, 1, 1) : null,
  "2026-01-01 00:00");

console.log(`\n===== ${pass} passed, ${fail} failed =====`);
process.exit(fail ? 1 : 0);
