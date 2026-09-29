/**
 * Pure view helpers: formatting and ordering only.
 *
 * No DOM, no IndexedDB, no globals -- everything here is a plain function of its
 * arguments, which is what lets tests/view.test.mjs exercise it under Node.
 */

const RTF = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

const MINUTE = 60000;
const HOUR = 3600000;
const DAY = 86400000;

const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** Escape for both element text and quoted attribute values. */
export function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[ch]);
}

/** Value for a <input type="datetime-local">, in local time. */
export function localInputValue(date = new Date()) {
  const pad = value => String(value).padStart(2, "0");
  return [
    date.getFullYear(), "-", pad(date.getMonth() + 1), "-", pad(date.getDate()),
    "T", pad(date.getHours()), ":", pad(date.getMinutes())
  ].join("");
}

/** "in 3 days" / "2 hours ago". Pass `now` to make it deterministic in tests. */
export function relativeFromNow(iso, now = Date.now()) {
  const diff = new Date(iso).getTime() - now;
  if (Number.isNaN(diff)) return "";

  const abs = Math.abs(diff);
  if (abs < MINUTE) return RTF.format(Math.round(diff / 1000), "second");
  if (abs < HOUR) return RTF.format(Math.round(diff / MINUTE), "minute");
  if (abs < DAY) return RTF.format(Math.round(diff / HOUR), "hour");
  if (abs < 30 * DAY) return RTF.format(Math.round(diff / DAY), "day");
  if (abs < 365 * DAY) return RTF.format(Math.round(diff / (30 * DAY)), "month");
  return RTF.format(Math.round(diff / (365 * DAY)), "year");
}

/** Absolute companion to relativeFromNow, so the list shows both. */
export function absoluteLabel(iso) {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit"
  });
}

/**
 * Short human label for a recurrence rule, stating both the period and how often
 * it repeats: "Weekly · Tue, Thu", "Every 2 weeks · Tue, Thu", "Every 5 years".
 *
 * The period is always named, even when the weekdays already imply it. "Tue, Thu"
 * on its own says which days but not that it is weekly rather than fortnightly,
 * and a label that leaves the reader to infer the period is the one thing this
 * function exists to avoid.
 */
export function describeRule(rule) {
  const kind = rule?.kind || "once";
  const interval = Math.max(1, Number.parseInt(rule?.interval ?? 1, 10) || 1);

  // "Daily" rather than "Every 1 days"; anything above 1 states the frequency.
  const every = (once, unit) => interval === 1 ? once : `Every ${interval} ${unit}s`;

  switch (kind) {
    case "daily":
      return every("Daily", "day");
    case "weekly": {
      const days = (rule?.weekdays || []).map(day => DAY_NAMES[day]).filter(Boolean).join(", ");
      const period = every("Weekly", "week");
      return days ? `${period} · ${days}` : period;
    }
    case "monthly":
      return every("Monthly", "month");
    case "yearly":
      return every("Yearly", "year");
    default:
      return "Once";
  }
}

/**
 * Soonest upcoming first. A reminder with no future occurrence (a spent "once")
 * sorts last rather than first, which is why null maps to Infinity and not 0.
 * Ties break on title so the order is stable and reproducible.
 */
export function sortReminders(list) {
  return [...list].sort((a, b) => {
    const aNext = a.nextDueAt ? Date.parse(a.nextDueAt) : Infinity;
    const bNext = b.nextDueAt ? Date.parse(b.nextDueAt) : Infinity;
    if (aNext !== bNext) return aNext - bNext;
    return (a.title || "").localeCompare(b.title || "");
  });
}

/**
 * Every folder id in the subtree rooted at `folderId`, including the root.
 *
 * Deleting a folder deletes its contents, so the caller needs the whole set
 * before it can count what is about to go. `seen` makes a parent cycle (which
 * the UI should never create, but a hand-edited database could hold) terminate
 * and return each id once rather than looping forever.
 */
export function collectSubtree(folderId, folders) {
  const childrenOf = new Map();
  for (const folder of folders || []) {
    const parent = folder.parentId ?? null;
    if (!childrenOf.has(parent)) childrenOf.set(parent, []);
    childrenOf.get(parent).push(folder.id);
  }

  const ids = [];
  const seen = new Set();
  const stack = [folderId];

  while (stack.length) {
    const id = stack.pop();
    if (id == null || seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
    stack.push(...(childrenOf.get(id) || []));
  }
  return ids;
}

/**
 * Every folder as one flat, ordered list for a folder picker: the tree walked
 * depth-first, each entry carrying the depth it should be indented by, with
 * "Unfiled" first as the null id.
 *
 * Siblings come out alphabetical, so the picker reads in the same order as the
 * tree. A folder whose parent is missing -- which the UI cannot create, but a
 * hand-edited database could hold -- is appended at depth 0 rather than dropped.
 * A folder the picker cannot name is a folder no note can be moved out of.
 */
export function folderOptions(folders) {
  const list = folders || [];
  const byParent = new Map();
  for (const folder of list) {
    const parent = folder.parentId ?? null;
    if (!byParent.has(parent)) byParent.set(parent, []);
    byParent.get(parent).push(folder);
  }
  const byName = (a, b) => String(a.name ?? "").localeCompare(String(b.name ?? ""));
  for (const children of byParent.values()) children.sort(byName);

  const options = [{ id: null, name: "Unfiled", depth: 0 }];
  const seen = new Set();

  // `seen` ends a parent cycle, which the tree walk would otherwise follow
  // forever. A cycle is unreachable from the root, so it lands in the pass below.
  const walk = (parentId, depth) => {
    for (const folder of byParent.get(parentId) || []) {
      if (seen.has(folder.id)) continue;
      seen.add(folder.id);
      options.push({ id: folder.id, name: folder.name, depth });
      walk(folder.id, depth + 1);
    }
  };
  walk(null, 0);

  for (const folder of list.filter(candidate => !seen.has(candidate.id)).sort(byName)) {
    options.push({ id: folder.id, name: folder.name, depth: 0 });
  }
  return options;
}

/**
 * The confirmation shown before a folder is destroyed, naming exactly what goes
 * with it. Counts are stated before the point of no return, never after.
 */
export function describeDeletion(name, { subfolders = 0, notes = 0 } = {}) {
  const parts = [];
  if (subfolders > 0) parts.push(`${subfolders} subfolder${subfolders === 1 ? "" : "s"}`);
  if (notes > 0) parts.push(`${notes} note${notes === 1 ? "" : "s"}`);

  const tail = parts.length
    ? ` This also deletes ${parts.join(" and ")}.`
    : "";
  return `Delete "${name ?? "Untitled"}"?${tail} This cannot be undone.`;
}

/**
 * The pane splits offered in Settings, in cycle order.
 *
 * The user's five requested ratios come first so the first click lands on 1:4,
 * and the current default 1:1 is appended as the last step -- without it the
 * even split would become unreachable once you cycle off it.
 */
export const RATIOS = ["1:4", "1:3", "1:2", "2:3", "3:4", "1:1"];

/** The next ratio in the cycle. An unknown value restarts at the first. */
export function nextRatio(current) {
  const index = RATIOS.indexOf(current);
  return RATIOS[(index + 1) % RATIOS.length];
}

/**
 * CSS grid tracks for a ratio: "3:4" -> 3fr top, 4fr bottom.
 * Anything unparseable falls back to the default even split.
 */
export function ratioToTracks(ratio) {
  const match = /^(\d+):(\d+)$/.exec(String(ratio ?? ""));
  if (!match) return { top: "1fr", bottom: "1fr" };
  return { top: `${match[1]}fr`, bottom: `${match[2]}fr` };
}

/** "Work / Projects / Apollo", built by walking parents up to the root. */
export function folderPath(folderId, folders) {
  const byId = new Map((folders || []).map(folder => [folder.id, folder]));
  const parts = [];
  const seen = new Set();
  let cursor = folderId;

  // `seen` guards against a parent cycle, which would otherwise hang the UI.
  while (cursor && byId.has(cursor) && !seen.has(cursor)) {
    seen.add(cursor);
    parts.unshift(byId.get(cursor).name);
    cursor = byId.get(cursor).parentId;
  }
  return parts.join(" / ");
}
