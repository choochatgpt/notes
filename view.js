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

/** Short human label for a recurrence rule, e.g. "Mon, Thu" or "Every 5 years". */
export function describeRule(rule) {
  const kind = rule?.kind || "once";
  const interval = Math.max(1, Number.parseInt(rule?.interval ?? 1, 10) || 1);

  switch (kind) {
    case "daily":
      return interval === 1 ? "Daily" : `Every ${interval} days`;
    case "weekly": {
      const days = (rule?.weekdays || []).map(day => DAY_NAMES[day]).join(", ");
      if (!days) return interval === 1 ? "Weekly" : `Every ${interval} weeks`;
      return interval === 1 ? days : `${days} · ${interval}wk`;
    }
    case "monthly":
      return interval === 1 ? "Monthly" : `Every ${interval} months`;
    case "yearly":
      return interval === 1 ? "Yearly" : `Every ${interval} years`;
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
