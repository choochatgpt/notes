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

/**
 * The release number the Settings dialog shows ("Version 19").
 *
 * The user asked to be able to see which revision is running. A number on
 * screen is only an answer if it is the same number the offline cache is
 * pinned to, so sw.js's CACHE_NAME is named after this and
 * tools/static_check.py fails the build when the two drift. Bump this and
 * CACHE_NAME together on every release that changes a shell asset.
 */
export const APP_VERSION = "37";

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
 * The one sibling-folder order the tree and the picker both display, so the
 * picker always reads exactly like the folder tree.
 *
 * A folder the user has arranged carries a finite integer `order` (written
 * only by a move) and these come first, ascending, ties on name. Folders
 * without one -- never arranged, restored from an older backup, or brand new
 * -- follow alphabetically, so a fresh folder appends at the end of a
 * group instead of shuffling an arrangement already made. A text `order` must
 * be a bare integer, the same shapes the backup parser accepts.
 */
export function sortFoldersSiblings(list) {
  const orderOf = row => {
    const value = row?.order;
    if (typeof value === "number" && Number.isInteger(value)) return value;
    if (typeof value === "string" && /^-?\d+$/.test(value.trim())) return Number(value);
    return null;
  };
  return [...list].sort((a, b) => {
    const aOrder = orderOf(a);
    const bOrder = orderOf(b);
    if (aOrder !== null && bOrder !== null && aOrder !== bOrder) return aOrder - bOrder;
    if (aOrder !== null || bOrder !== null) return aOrder === null ? 1 : -1;
    return String(a.name ?? "").localeCompare(String(b.name ?? ""));
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
 * Siblings come out in the same arranged order the folder tree shows
 * (sortFoldersSiblings), so the picker reads exactly like the tree.
 * A folder whose parent is missing -- which the UI cannot create, but a
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
  for (const [parent, children] of byParent) {
    byParent.set(parent, sortFoldersSiblings(children));
  }

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

  for (const folder of sortFoldersSiblings(list.filter(candidate => !seen.has(candidate.id)))) {
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
 * The note-list preview bounds. A note typed with the Enter key must not read
 * as one long line in the list -- the preview keeps the author's line breaks
 * -- but it also must not let one huge note eat the list, so it stops after
 * SNIPPET_LINES lines and truncates any single line past SNIPPET_LINE_CHARS,
 * an ellipsis marking everything cut off.
 */
export const SNIPPET_LINES = 4;
export const SNIPPET_LINE_CHARS = 120;

/** The list preview text for a note body: line breaks kept, bounded, or "". */
export function noteSnippet(body) {
  const text = String(body ?? "").replace(/\r\n?/g, "\n").trim();
  if (!text) return "";
  const lines = text.split("\n");
  const kept = lines.slice(0, SNIPPET_LINES).map(line => {
    const clean = line.replace(/[ \t]+/g, " ").trim();
    return clean.length > SNIPPET_LINE_CHARS
      ? clean.slice(0, SNIPPET_LINE_CHARS).trimEnd() + "…"
      : clean;
  });
  const snippet = kept.join("\n");
  return lines.length > SNIPPET_LINES ? snippet + "…" : snippet;
}

/**
 * The pane splits offered in Settings, in cycle order: the share of the screen
 * the top (notes) pane claims. The user's ten-percent steps from 10% through
 * 90%, wrapping 90% -> 10% (2026-10-05 revision; before it was 20/40/60/80).
 */
export const RATIOS = ["10%", "20%", "30%", "40%", "50%", "60%", "70%", "80%", "90%"];

/**
 * The split before anything is chosen: the even split. It is the CSS fallback
 * and the chip's value until the first click, so a fresh device (or an
 * unreadable stored value) starts even rather than at an arbitrary share.
 * Since the 10%-step revision it is also one of the offered stops, so the
 * first click from it simply advances to 60%.
 */
export const DEFAULT_RATIO = "50%";

/** The next ratio in the cycle. An unknown value (not a stop) restarts at the first. */
export function nextRatio(current) {
  const index = RATIOS.indexOf(current);
  return RATIOS[(index + 1) % RATIOS.length];
}

/**
 * CSS grid tracks for a top share: "40%" -> 40fr top, 60fr bottom.
 * Anything unparseable -- including the old a:b ratio format from releases
 * before 2026-09-29 -- falls back to the default even split.
 */
export function ratioToTracks(ratio) {
  const match = /^(\d{1,3})%$/.exec(String(ratio ?? ""));
  const pct = match ? Number(match[1]) : NaN;
  if (!Number.isFinite(pct) || pct <= 0 || pct >= 100) {
    return { top: "1fr", bottom: "1fr" };
  }
  return { top: `${pct}fr`, bottom: `${100 - pct}fr` };
}

/**
 * The folder:contents splits offered in Settings (v31), in cycle order: the
 * share the TOP folder panel claims inside the notes pane (since v33 the
 * split is top/bottom; before that it was left/right). The user's
 * five-percent-window stops 30% through 70%, wrapping 70% -> 30% (2026-10-06).
 */
export const FOLDER_RATIOS = ["30%", "40%", "50%", "60%", "70%"];

/**
 * The split before anything is chosen: 40:60, the share the density pass
 * shipped on the user's request (2026-10-05) -- 40% of the notes pane for the
 * folder tree (its top share since v33). It is the CSS fallback (the
 * same 4fr/6fr the markup's chip carries) and the chip's value until the
 * first click, and like the pane ratio it is one of the offered stops, so
 * the first click simply advances to 50%.
 */
export const DEFAULT_FOLDER_RATIO = "40%";

/** The next folder ratio in the cycle. An unknown value (not a stop) restarts at the first. */
export function nextFolderRatio(current) {
  const index = FOLDER_RATIOS.indexOf(current);
  return FOLDER_RATIOS[(index + 1) % FOLDER_RATIOS.length];
}

/**
 * CSS grid tracks for the folder (top) share: "40%" -> 40fr folder, 60fr
 * content. Anything unparseable or out of range falls back to the default
 * 40:60 rather than to junk tracks.
 */
export function folderRatioToTracks(ratio) {
  const match = /^(\d{1,3})%$/.exec(String(ratio ?? ""));
  const pct = match ? Number(match[1]) : NaN;
  if (!Number.isFinite(pct) || pct <= 0 || pct >= 100) {
    return { folder: "4fr", content: "6fr" };
  }
  return { folder: `${pct}fr`, content: `${100 - pct}fr` };
}

/**
 * The attachment-count line under a note's title in the list, "7 · 5 jpg ·
 * 2 pdf", from the media records the note ACTUALLY has.
 *
 * The caller passes loaded records, not the note's raw id list -- a dangling
 * id is the note's problem (delete still names the id count); the badge
 * reports what is really there, so it never shows a ghost attachment.
 *
 * Labels come from the record's type first and fall back to the file name's
 * extension for a record with no type (the common shapes: jpeg/jpg fold into
 * "jpg"). One kind reads as just that group -- "5 jpg" -- because the total
 * would be the same number twice; two or more kinds lead with the total so
 * the line is one glance, not arithmetic. The `title` always carries the
 * full breakdown, including for the single-group cases.
 */
export function attachmentBadge(records) {
  const list = (records || []).filter(Boolean);
  if (!list.length) return null;

  const counts = new Map();
  for (const record of list) {
    const label = attachmentLabel(record);
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  const groups = [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const parts = groups.map(([label, count]) => `${count} ${label}`);
  const total = list.length;

  return {
    text: groups.length === 1 ? parts[0] : `${total} · ${parts.join(" · ")}`,
    title: `${total} attachment${total === 1 ? "" : "s"}: ${parts.join(", ")}`
  };
}

/** A record's group label: type first, then the name's extension. */
function attachmentLabel(record) {
  const type = String(record?.type || "");
  if (type === "image/jpeg" || type === "image/jpg") return "jpg";
  if (type === "image/png") return "png";
  if (type === "application/pdf") return "pdf";
  if (type.startsWith("video/")) return "video";
  if (type.startsWith("image/")) return type.slice("image/".length).toLowerCase() || "image";

  const name = String(record?.name || "");
  const dot = name.lastIndexOf(".");
  if (dot !== -1 && dot < name.length - 1) {
    const ext = name.slice(dot + 1).toLowerCase();
    if (ext === "jpg" || ext === "jpeg") return "jpg";
    return ext || "file";
  }
  return "file";
}

/**
 * The title a note displays (v37): its own title, else the name of the FOLDER
 * it lives in -- an untitled note is the folder's content, so the folder's
 * name is the title the row and the delete confirmation both show. Not a
 * stored title: the label follows the note's CURRENT folder, so moving the
 * note moves its name with it. Unfiled is the pseudo-folder's own label (the
 * tree, the picker and the breadcrumb all use the same word); a folder that
 * is not in the tree, or whose name cannot be read, degrades to the old
 * "Untitled note" placeholder rather than an invented one.
 */
export function noteDisplayTitle(note, folders) {
  const own = String(note?.title ?? "").trim();
  if (own) return own;
  if (note == null) return "Untitled note";
  if (note.folderId == null) return "Unfiled";
  const folder = Array.isArray(folders)
    ? folders.find(f => f && f.id === note.folderId)
    : null;
  const name = typeof folder?.name === "string" ? folder.name.trim() : "";
  return name || "Untitled note";
}

/**
 * The three counts a folder row's chips display (v35): the notes directly
 * inside it, its DIRECT subfolders, and the attachments carried by the whole
 * subtree -- "the folder as a whole" is what a row says when it is collapsed.
 *
 * Notes and subfolders are direct counts on purpose: the row already answers
 * the drill-down question (the child is one tap away in the tree), while the
 * attachment load is a property of the branch as a whole -- a folder with no
 * direct note can still sit on top of a subtree of photos, and that is what
 * the clip chip is for. The subtree walk is collectSubtree, the same
 * cycle-safe walk delete uses, so a hand-edited parent cycle cannot hang the
 * tree. Unfiled (null) has notes by definition and no child folders.
 */
export function folderBadgeCounts(folders, notes, folderId) {
  const folderList = folders || [];
  const noteList = notes || [];

  // Unfiled is a leaf pseudo-folder: the root folders appear BESIDE it in the
  // tree and the picker (both walk null at depth 0), not beneath it, so it has
  // no child folders -- only the notes that name no folder, and whatever
  // those notes carry. (collectSubtree cannot answer this: the children table
  // keys on parentId, and Unfiled is not a parent.)
  if (folderId == null) {
    const own = noteList.filter(note => (note.folderId ?? null) === null);
    return {
      notes: own.length,
      subfolders: 0,
      attachments: own.reduce((sum, note) => sum + (note.mediaIds || []).length, 0)
    };
  }

  const subtree = new Set(collectSubtree(folderId, folderList));

  const directNotes = noteList.filter(
    note => (note.folderId ?? null) === (folderId ?? null)
  ).length;
  const directFolders = folderList.filter(
    folder => (folder.parentId ?? null) === (folderId ?? null)
      && folder.id !== (folderId ?? null)
  ).length;
  const attachments = noteList.reduce(
    (sum, note) => subtree.has(note.folderId)
      ? sum + (note.mediaIds || []).length
      : sum,
    0
  );

  return { notes: directNotes, subfolders: directFolders, attachments };
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
