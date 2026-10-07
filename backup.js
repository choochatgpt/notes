/**
 * Pure backup serializer and parser: text in, data out, no DOM and no storage.
 *
 * The backup is a sectioned CSV under a versioned `# notes-backup v3` header.
 * Folders are included even though the export is called "notes/reminders":
 * without them a restore could not rebuild the tree, and every note's folderId
 * would dangle. Reminder due dates are deliberately NOT stored -- nextDueAt is a
 * cached value that goes stale, so it is recomputed from startAt + recurrence
 * after the restore instead of being trusted from the file.
 *
 * Notes carry a mediaIds column (v2): the ids of the photos attached on the
 * device. The BYTES are never in the file -- a backup stays text-only -- but
 * keeping the ids means a same-device restore reattaches the photos, which
 * still sit in OPFS/IndexedDB where a restore never reaches.
 *
 * Folders carry an order column (v3): the position the user arranged a folder
 * into, empty for never-arranged. It is restored verbatim, so an arranged tree
 * comes back arranged. Older files are still read: columns are matched by
 * header name, so a v1 or v2 file without the column parses with every folder
 * unordered -- alphabetical, as those releases displayed them. Only a file
 * NEWER than this app is refused.
 *
 * The parser validates the whole document before anything writes to the
 * database. A malformed file produces errors, not exceptions, so the UI can
 * refuse the restore rather than half-apply it.
 */

export const SCHEMA_VERSION = 3;

/**
 * mailto: URLs are silently truncated by some mail clients somewhere between
 * ~2000 and ~8000 characters. Refuse before any of those limits rather than
 * hand the user a backup that quietly loses its tail.
 */
export const MAILTO_SAFE_LIMIT = 1800;

const SECTIONS = [
  { name: "folders", columns: ["id", "parentId", "name", "order", "createdAt", "updatedAt"] },
  { name: "notes", columns: ["id", "folderId", "title", "body", "mediaIds", "createdAt", "updatedAt"] },
  {
    name: "reminders",
    columns: ["id", "folderId", "title", "body", "enabled", "startAt",
              "timeZone", "recurrence", "createdAt", "updatedAt"]
  }
];

/** Quote a cell per RFC 4180: wrap when it holds a comma, quote or newline. */
function csvCell(value) {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function csvRow(values) {
  return values.map(csvCell).join(",");
}

/**
 * The folder order cell. A bare integer is the position the user arranged the
 * folder into; anything else -- empty for never-arranged folders and for files
 * from before this column existed, or text that never was an order -- parses
 * to null. `parseInt` would happily accept "3.5" and "0x2", so the shape is
 * checked instead.
 */
function parseOrderCell(text) {
  const value = String(text ?? "").trim();
  return /^-?\d+$/.test(value) ? Number(value) : null;
}

/**
 * The whole backup as one CSV string. Every section is always written, even
 * when empty, so a reader can tell "no reminders" from "no reminders section".
 */
export function buildBackupCsv({ folders = [], notes = [], reminders = [], exportedAt = "" } = {}) {
  const lines = [`# notes-backup v${SCHEMA_VERSION}, exported ${exportedAt}`];

  for (const section of SECTIONS) {
    lines.push(`## ${section.name}`);
    lines.push(csvRow(section.columns));

    const rows = { folders, notes, reminders }[section.name] || [];
    for (const row of rows) {
      lines.push(csvRow(section.columns.map(column => {
        if (column === "recurrence") return JSON.stringify(row.recurrence ?? null);
        if (column === "enabled") return row.enabled === false ? "false" : "true";
        if (column === "mediaIds") return (row.mediaIds || []).join(";");
        return row[column];
      })));
    }
  }
  return lines.join("\r\n") + "\r\n";
}

/**
 * Character-level CSV reader. Handles quoted cells containing commas, quotes
 * (doubled) and newlines; accepts CRLF and LF; skips a UTF-8 BOM. A quote is
 * only special at the start of a cell, so `it's` stays literal.
 */
function parseRows(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let inQuotes = false;
  let line = 1;
  let rowLine = 1;

  const endCell = () => { row.push(cell); cell = ""; };
  const endRow = () => { endCell(); rows.push({ cells: row, line: rowLine }); row = []; };

  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') { cell += '"'; i += 1; }
        else inQuotes = false;
      } else {
        if (ch === "\n") line += 1;
        cell += ch;
      }
      continue;
    }
    if (ch === '"' && cell === "") { inQuotes = true; continue; }
    if (ch === ",") { endCell(); continue; }
    if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i += 1;
      line += 1;
      endRow();
      rowLine = line;
      continue;
    }
    cell += ch;
  }
  if (inQuotes) return { rows, unterminated: true };
  if (cell !== "" || row.length) endRow();
  return { rows, unterminated: false };
}

/**
 * Parse a pasted backup into ready-to-store records.
 *
 * Returns { ok, version, exportedAt, folders, notes, reminders, warnings,
 * errors }. `errors` block the restore; `warnings` are survivable repairs
 * (a reference to a folder the file does not contain) that the preview shows
 * before the user commits.
 */
export function parseBackupCsv(text) {
  const errors = [];
  const warnings = [];
  const result = {
    ok: false, version: null, exportedAt: null,
    folders: [], notes: [], reminders: [], warnings, errors
  };

  const source = String(text ?? "").replace(/^\uFEFF/, "");
  if (!source.trim()) {
    errors.push("Nothing was pasted. Copy the whole CSV from your email backup.");
    return result;
  }

  const { rows, unterminated } = parseRows(source);
  if (unterminated) {
    errors.push("A quoted cell is never closed — the file is truncated or corrupted.");
  }

  const first = rows.find(candidate => candidate.cells.some(value => value.trim() !== ""));
  // The header is one logical line that happens to contain a comma ("v1,
  // exported ..."), so CSV splitting puts the stamp in cell 1 -- rejoin before
  // reading either half of it.
  const header = first ? first.cells.join(",") : "";
  const match = /^#\s*notes-backup\s+v(\d+)/.exec(header);
  if (!match) {
    errors.push(`This is not a notes backup — it does not start with "# notes-backup v${SCHEMA_VERSION}".`);
    return result;
  }
  result.version = Number.parseInt(match[1], 10);
  const stamp = /,\s*exported\s+(.+)$/.exec(header);
  result.exportedAt = stamp ? stamp[1].trim() : null;

  // A NEWER file is refused: this app cannot know what its columns mean. An
  // OLDER one is accepted -- v1 rows simply have no mediaIds column, and every
  // column is read by header name, so nothing else has to change.
  if (result.version > SCHEMA_VERSION) {
    errors.push(`This backup is version v${versionLabel(result.version)} but this app reads v${SCHEMA_VERSION}. Update the app first.`);
    return result;
  }

  // --- split into sections -------------------------------------------------
  const sections = new Map();
  let current = null;
  for (const { cells, line } of rows) {
    const head = String(cells[0] ?? "").trim();
    if (head.startsWith("##")) {
      const name = head.slice(2).trim().toLowerCase();
      if (sections.has(name)) errors.push(`line ${line}: duplicate "## ${name}" section.`);
      current = { name, header: null, rows: [] };
      sections.set(name, current);
      continue;
    }
    if (head.startsWith("#")) continue;
    if (!current) {
      if (cells.some(value => value.trim() !== "")) {
        errors.push(`line ${line}: data appears before any "## folders" section.`);
      }
      continue;
    }
    if (!current.header) {
      current.header = cells.map(value => value.trim());
      continue;
    }
    if (cells.every(value => value.trim() === "")) continue;
    current.rows.push({ cells, line });
  }

  for (const section of SECTIONS) {
    if (!sections.has(section.name)) {
      errors.push(`The file has no "## ${section.name}" section, so it is not a complete backup.`);
    }
  }
  if (errors.length) return result;

  const cellAt = (entry, header, column) => {
    const index = header.indexOf(column);
    return index < 0 ? "" : String(entry.cells[index] ?? "");
  };

  // --- folders -------------------------------------------------------------
  {
    const { header, rows: dataRows } = sections.get("folders");
    if (header.indexOf("id") < 0) {
      errors.push('"## folders" has no id column.');
    } else {
      const seen = new Set();
      for (const entry of dataRows) {
        const id = cellAt(entry, header, "id");
        if (!id) { errors.push(`line ${entry.line}: a folder row has no id.`); continue; }
        if (seen.has(id)) { errors.push(`line ${entry.line}: duplicate folder id "${id}".`); continue; }
        seen.add(id);
        result.folders.push({
          id,
          parentId: cellAt(entry, header, "parentId") || null,
          name: cellAt(entry, header, "name"),
          order: parseOrderCell(cellAt(entry, header, "order")),
          createdAt: cellAt(entry, header, "createdAt"),
          updatedAt: cellAt(entry, header, "updatedAt")
        });
      }
    }
  }

  // --- notes ---------------------------------------------------------------
  {
    const { header, rows: dataRows } = sections.get("notes");
    if (header.indexOf("id") < 0) {
      errors.push('"## notes" has no id column.');
    } else {
      const seen = new Set();
      for (const entry of dataRows) {
        const id = cellAt(entry, header, "id");
        if (!id) { errors.push(`line ${entry.line}: a note row has no id.`); continue; }
        if (seen.has(id)) { errors.push(`line ${entry.line}: duplicate note id "${id}".`); continue; }
        seen.add(id);
        // mediaIds is a v2 column; a v1 file has none and parses as no photos.
        // Semicolon-joined because an id is a UUID, and the cell must survive
        // the same quoting rules as any other text.
        const mediaIds = cellAt(entry, header, "mediaIds")
          .split(";").filter(Boolean);
        result.notes.push({
          id,
          folderId: cellAt(entry, header, "folderId") || null,
          title: cellAt(entry, header, "title"),
          body: cellAt(entry, header, "body"),
          mediaIds,
          createdAt: cellAt(entry, header, "createdAt"),
          updatedAt: cellAt(entry, header, "updatedAt")
        });
      }
    }
  }

  // --- reminders -----------------------------------------------------------
  {
    const { header, rows: dataRows } = sections.get("reminders");
    if (header.indexOf("id") < 0) {
      errors.push('"## reminders" has no id column.');
    } else {
      const seen = new Set();
      for (const entry of dataRows) {
        const id = cellAt(entry, header, "id");
        if (!id) { errors.push(`line ${entry.line}: a reminder row has no id.`); continue; }
        if (seen.has(id)) { errors.push(`line ${entry.line}: duplicate reminder id "${id}".`); continue; }

        const startAt = cellAt(entry, header, "startAt");
        if (!startAt || Number.isNaN(Date.parse(startAt))) {
          errors.push(`line ${entry.line}: reminder "${cellAt(entry, header, "title")}" has an invalid first-due date.`);
          continue;
        }

        const rawRecurrence = cellAt(entry, header, "recurrence");
        let recurrence;
        try {
          recurrence = JSON.parse(rawRecurrence || "null");
        } catch (error) {
          errors.push(`line ${entry.line}: reminder "${cellAt(entry, header, "title")}" has a recurrence that is not valid JSON.`);
          continue;
        }
        if (!recurrence || typeof recurrence !== "object" || Array.isArray(recurrence)) {
          errors.push(`line ${entry.line}: reminder "${cellAt(entry, header, "title")}" has no usable recurrence rule.`);
          continue;
        }

        seen.add(id);
        result.reminders.push({
          id,
          folderId: cellAt(entry, header, "folderId") || null,
          title: cellAt(entry, header, "title"),
          body: cellAt(entry, header, "body"),
          enabled: cellAt(entry, header, "enabled") !== "false",
          startAt,
          timeZone: cellAt(entry, header, "timeZone"),
          recurrence,
          createdAt: cellAt(entry, header, "createdAt"),
          updatedAt: cellAt(entry, header, "updatedAt")
        });
      }
    }
  }

  // --- repair dangling references, and say so ------------------------------
  // The UI cannot create a folder that does not exist, but a hand-edited or
  // partially copied file can name one. Dropping the reference keeps the restore
  // usable (the note lands in Unfiled) as long as the preview states it.
  const folderIds = new Set(result.folders.map(folder => folder.id));
  for (const folder of result.folders) {
    if (folder.parentId && !folderIds.has(folder.parentId)) {
      warnings.push(`Folder "${folder.name || folder.id}" pointed at a missing parent and is now at the top level.`);
      folder.parentId = null;
    }
  }
  for (const note of result.notes) {
    if (note.folderId && !folderIds.has(note.folderId)) {
      warnings.push(`Note "${note.title || note.id}" pointed at a missing folder and will be Unfiled.`);
      note.folderId = null;
    }
  }

  result.ok = errors.length === 0;
  return result;
}

function versionLabel(version) {
  return Number.isFinite(version) ? String(version) : "?";
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

/**
 * The confirmation shown before everything is replaced, in the same spirit as
 * describeDeletion: counts are stated before the point of no return, and what
 * is kept (settings) is stated too.
 */
export function describeRestore({ current = {}, incoming = {}, exportedAt = null } = {}) {
  const now = [
    plural(current.folders || 0, "folder"),
    plural(current.notes || 0, "note"),
    plural(current.reminders || 0, "reminder")
  ].join(", ");
  const then = [
    plural(incoming.folders || 0, "folder"),
    plural(incoming.notes || 0, "note"),
    plural(incoming.reminders || 0, "reminder")
  ].join(", ");
  const stamp = exportedAt ? ` (exported ${exportedAt})` : "";
  return `Replace everything? This permanently deletes all ${now} on this device `
    + `and restores ${then} from the backup${stamp}. Attached files are not `
    + "included in a backup. Your settings are kept. This cannot be undone.";
}

/**
 * A mailto: handoff of the CSV as the message body. `mailto:` cannot attach
 * files, and long URLs get truncated by some clients, so an oversized body is
 * refused outright -- the caller falls back to the clipboard copy instead. A truncated
 * backup that looks complete is worse than one that will not open.
 */
export function buildMailtoHref({ to = "", subject = "", body = "", limit = MAILTO_SAFE_LIMIT } = {}) {
  const address = String(to).trim();
  const href = `mailto:${address}?subject=${encodeURIComponent(subject)}`
    + `&body=${encodeURIComponent(body)}`;
  if (href.length > limit) {
    return { ok: false, reason: "body-too-large", encodedLength: href.length, limit };
  }
  return { ok: true, href, encodedLength: href.length, limit };
}