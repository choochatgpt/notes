import {
  get,
  getAll,
  getAllByIndex,
  getSetting,
  newId,
  openDatabase,
  put,
  remove,
  replaceAll,
  requestPersistentStorage,
  setSetting
} from "./storage.js";
import { reminderWithNextDue } from "./reminder.js";
import {
  absoluteLabel,
  collectSubtree,
  describeDeletion,
  describeRule,
  esc,
  folderOptions,
  folderPath,
  localInputValue,
  nextRatio,
  RATIOS,
  ratioToTracks,
  DEFAULT_RATIO,
  relativeFromNow,
  sortReminders
} from "./view.js";
import {
  buildBackupCsv,
  buildMailtoHref,
  describeRestore,
  parseBackupCsv
} from "./backup.js";

/**
 * The upper pane is either browsing (folders + list, or the reminder manager) or
 * editing one item in place. The lower pane is the agenda and is never in edit
 * mode, so a reminder coming due stays on screen while a note is open above it.
 */
const state = {
  activeKind: "notes",
  mode: "browse",
  selectedFolderId: null,
  selectedItemId: null,
  // Folders the user has closed in the tree. Session-only: a collapsed branch is
  // a view detail, not part of the note data.
  collapsed: new Set(),
  folders: [],
  folderChip: "",
  reminderChip: "",
  editChip: ""
};

const $ = selector => document.querySelector(selector);
const els = {
  folderTree: $("#folder-tree"),
  noteList: $("#note-list"),
  reminderManage: $("#reminder-manage-list"),
  agendaList: $("#agenda-list"),
  agendaContext: $("#agenda-context"),
  browseNotes: $("#browse-notes"),
  browseReminders: $("#browse-reminders"),
  editorHost: $("#editor-host"),
  editorBack: $("#editor-back"),
  listContext: $("#list-context"),
  notesTab: $("#notes-tab"),
  remindersTab: $("#reminders-tab"),
  noteEditor: $("#note-editor"),
  reminderEditor: $("#reminder-editor"),
  noteTitle: $("#note-title"),
  noteBody: $("#note-body"),
  noteFolder: $("#note-folder"),
  reminderTitle: $("#reminder-title"),
  reminderBody: $("#reminder-body"),
  reminderStart: $("#reminder-start"),
  reminderRepeat: $("#reminder-repeat"),
  reminderInterval: $("#reminder-interval"),
  weekdayPicker: $("#weekday-picker"),
  newFolderForm: $("#new-folder-form"),
  newFolderName: $("#new-folder-name"),
  statusbar: $(".statusbar"),
  appStatus: $("#app-status")
};

function nowIso() {
  return new Date().toISOString();
}

/** "4 notes" / "1 note" / "" -- the counts are always stated before a delete. */
function countLabel(count, noun) {
  if (!count) return "";
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/**
 * nextDueAt is a cached value that goes stale the moment it passes. Recompute
 * every reminder before anything sorts or displays by it, and persist only the
 * rows that actually changed.
 */
async function refreshReminderSchedule() {
  const reminders = await getAll("reminders");
  const now = new Date();
  let changed = 0;

  for (const reminder of reminders) {
    const next = reminderWithNextDue(reminder, now);
    if (next.nextDueAt !== reminder.nextDueAt) {
      await put("reminders", next);
      changed += 1;
    }
  }
  return changed;
}

/* ------------------------------------------------------------------ folders */

async function renderFolders() {
  const [folders, notes] = await Promise.all([getAll("folders"), getAll("notes")]);
  state.folders = folders;

  const counts = new Map();
  for (const note of notes) {
    const key = note.folderId ?? null;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const rootCount = counts.get(null) || 0;
  state.folderChip = state.selectedFolderId
    ? `${folderPath(state.selectedFolderId, folders)} · ${countLabel(counts.get(state.selectedFolderId) || 0, "note") || "0 notes"}`
    : `Unfiled · ${countLabel(rootCount, "note") || "0 notes"}`;

  const byParent = new Map();
  for (const folder of folders) {
    const parent = folder.parentId ?? null;
    if (!byParent.has(parent)) byParent.set(parent, []);
    byParent.get(parent).push(folder);
  }
  for (const list of byParent.values()) {
    list.sort((a, b) => a.name.localeCompare(b.name));
  }

  const rows = [
    `<div class="folder-row ${state.selectedFolderId === null ? "active" : ""}" data-folder="">
       <span class="folder-toggle leaf"></span>
       <span class="folder-select">
         <svg class="icon"><use href="#i-folder"></use></svg>
         <span class="folder-name">Unfiled</span>
       </span>
       ${rootCount ? `<span class="count">${rootCount}</span>` : ""}
     </div>`
  ];

  const walk = (parentId, depth) => {
    for (const folder of byParent.get(parentId) || []) {
      const hasChildren = (byParent.get(folder.id) || []).length > 0;
      const open = hasChildren && !state.collapsed.has(folder.id);
      const count = counts.get(folder.id) || 0;

      rows.push(
        `<div class="folder-row ${state.selectedFolderId === folder.id ? "active" : ""}"
              data-folder="${esc(folder.id)}" style="padding-left:${9 + depth * 14}px">
           <button class="folder-toggle ${open ? "open" : ""} ${hasChildren ? "" : "leaf"}"
                   type="button" data-toggle="${esc(folder.id)}"
                   aria-label="${open ? "Collapse" : "Expand"} ${esc(folder.name)}">
             <svg class="icon"><use href="#i-chevron"></use></svg>
           </button>
           <button class="folder-select" type="button">
             <svg class="icon"><use href="#i-folder"></use></svg>
             <span class="folder-name">${esc(folder.name)}</span>
           </button>
           ${count ? `<span class="count">${count}</span>` : ""}
           <button class="folder-del" type="button" data-del="${esc(folder.id)}"
                   title="Delete folder" aria-label="Delete folder ${esc(folder.name)}">
             <svg class="icon"><use href="#i-trash"></use></svg>
           </button>
         </div>`
      );

      if (open) walk(folder.id, depth + 1);
    }
  };
  walk(null, 0);

  els.folderTree.innerHTML = rows.join("");

  els.folderTree.querySelectorAll(".folder-row").forEach(rowEl => {
    rowEl.addEventListener("click", async event => {
      const toggle = event.target.closest(".folder-toggle");
      if (toggle?.dataset.toggle) {
        const id = toggle.dataset.toggle;
        if (state.collapsed.has(id)) state.collapsed.delete(id);
        else state.collapsed.add(id);
        await renderFolders();
        return;
      }

      const del = event.target.closest(".folder-del");
      if (del?.dataset.del) {
        await deleteFolder(del.dataset.del);
        return;
      }

      // Selecting a folder means browsing it, so any open editor closes.
      state.selectedFolderId = rowEl.dataset.folder || null;
      state.mode = "browse";
      state.selectedItemId = null;
      await renderAll();
    });
  });
}

/* -------------------------------------------------------------------- lists */

async function renderNoteList() {
  const notes = state.selectedFolderId === null
    ? (await getAll("notes")).filter(note => note.folderId === null || note.folderId === undefined)
    : await getAllByIndex("notes", "folderId", state.selectedFolderId);

  notes.sort((a, b) =>
    (b.updatedAt || b.createdAt || "").localeCompare(a.updatedAt || a.createdAt || "")
  );

  if (!notes.length) {
    els.noteList.innerHTML = `<p class="muted">No notes here yet — use “New note”.</p>`;
    return;
  }

  els.noteList.innerHTML = notes.map(note => {
    const title = note.title?.trim() || "Untitled note";
    const snippet = (note.body || "").replace(/\s+/g, " ").trim();
    const when = note.updatedAt ? relativeFromNow(note.updatedAt) : "";

    return `<button class="item-row ${state.mode === "edit" && state.selectedItemId === note.id ? "active" : ""}"
                    type="button" data-item="${esc(note.id)}">
      <svg class="icon"><use href="#i-note"></use></svg>
      <span class="item-main">
        <span class="item-title">${esc(title)}</span>
        <span class="item-sub">${snippet ? esc(snippet.slice(0, 90)) : "Empty"}</span>
      </span>
      ${when ? `<span class="when-abs">${esc(when)}</span>` : "<span></span>"}
    </button>`;
  }).join("");

  wireRows(els.noteList, "notes");
}

/**
 * One line per reminder: clock, title, how often it repeats, when it is next due.
 *
 * Everything after the title is a direct child of the row so it lies along that
 * one line; the title is the only flexible part and ellipsises rather than
 * pushing the recurrence or the due time off the row. A spent one-off has no
 * future occurrence and carries no due time at all -- the chip says "Past"
 * rather than the row printing a date it does not have.
 */
function reminderRow(reminder) {
  const title = reminder.title?.trim() || "Untitled reminder";
  const due = reminder.nextDueAt;
  const rule = describeRule(reminder.recurrence);

  const when = due
    ? `<span class="item-when">
         <span class="when-rel">${esc(relativeFromNow(due))}</span>
         <span class="when-abs">${esc(absoluteLabel(due))}</span>
       </span>`
    : "";

  return `<button class="item-row reminder-row ${due ? "" : "past"} ${state.mode === "edit" && state.selectedItemId === reminder.id ? "active" : ""}"
                  type="button" data-item="${esc(reminder.id)}">
    <svg class="icon"><use href="#i-clock"></use></svg>
    <span class="item-title">${esc(title)}</span>
    <span class="chip ${due ? "accent" : "past"}">${esc(due ? rule : "Past")}</span>
    ${when}
  </button>`;
}

/** Where reminders are created, edited and deleted. Sorted soonest first too. */
async function renderReminderManage() {
  const reminders = sortReminders(await getAll("reminders"));
  state.reminderChip = reminders.length ? `${reminders.length} · soonest first` : "";

  if (!reminders.length) {
    els.reminderManage.innerHTML = `<p class="muted">No reminders yet — use “New reminder”.</p>`;
    return;
  }
  els.reminderManage.innerHTML = reminders.map(reminderRow).join("");
  wireRows(els.reminderManage, "reminders");
}

/**
 * The lower pane: upcoming reminders only, soonest first. Spent one-offs are
 * excluded here but never hidden -- the footer states how many there are and
 * where to find them.
 */
async function renderAgenda() {
  const all = await getAll("reminders");
  const upcoming = sortReminders(all).filter(reminder => reminder.nextDueAt);
  const pastCount = all.length - upcoming.length;

  els.agendaContext.textContent = upcoming.length
    ? `${upcoming.length} · soonest first`
    : "";

  if (!upcoming.length) {
    els.agendaList.innerHTML = `<p class="muted">${
      all.length
        ? "Nothing coming up."
        : "No reminders yet — use “New reminder”."
    }</p>`;
    return;
  }

  els.agendaList.innerHTML = upcoming.map(reminderRow).join("")
    + (pastCount
      ? `<p class="muted agenda-foot">${countLabel(pastCount, "past reminder")} — open the Reminders tab to see ${pastCount === 1 ? "it" : "them"}.</p>`
      : "");

  wireRows(els.agendaList, "reminders");
}

/** Opening any row -- from either pane -- swaps the upper pane into its editor. */
function wireRows(container, kind) {
  container.querySelectorAll(".item-row").forEach(rowEl => {
    rowEl.addEventListener("click", () => openItem(kind, rowEl.dataset.item));
  });
}

async function openItem(kind, id) {
  state.activeKind = kind;
  state.selectedItemId = id;
  state.mode = "edit";
  await renderAll();
  (kind === "notes" ? els.noteTitle : els.reminderTitle).focus();
}

function backToBrowse() {
  state.mode = "browse";
  state.selectedItemId = null;
  return renderAll();
}

/* ------------------------------------------------------------------ editors */

/** Upper-pane visibility: which browser is showing, or the editor instead. */
function syncUpper() {
  const notes = state.activeKind === "notes";
  const editing = state.mode === "edit";

  document.body.dataset.kind = state.activeKind;
  document.body.dataset.mode = state.mode;

  els.notesTab.classList.toggle("active", notes);
  els.remindersTab.classList.toggle("active", !notes);
  els.notesTab.setAttribute("aria-selected", String(notes));
  els.remindersTab.setAttribute("aria-selected", String(!notes));

  els.browseNotes.classList.toggle("hidden", !notes || editing);
  els.browseReminders.classList.toggle("hidden", notes || editing);
  els.editorHost.classList.toggle("hidden", !editing);
  els.editorBack.classList.toggle("hidden", !editing);

  els.noteEditor.classList.toggle("hidden", !editing || !notes);
  els.reminderEditor.classList.toggle("hidden", !editing || notes);

  els.listContext.textContent = editing
    ? state.editChip
    : (notes ? state.folderChip : state.reminderChip);
}

/** A non-breaking space: <option> collapses leading ordinary whitespace. */
const NBSP = String.fromCharCode(0xa0);
const INDENT = NBSP + NBSP;

/**
 * Fill the note editor's folder picker from the tree, marking where the note
 * currently lives. Depth becomes non-breaking-space indent, because an <option>
 * cannot be styled and leading ordinary spaces are collapsed away.
 */
function renderFolderPicker(selectedId) {
  if (!els.noteFolder) return;

  const current = selectedId ?? null;
  els.noteFolder.replaceChildren();
  for (const entry of folderOptions(state.folders)) {
    const option = document.createElement("option");
    option.value = entry.id ?? "";
    option.textContent = INDENT.repeat(entry.depth) + String(entry.name ?? "");
    option.selected = (entry.id ?? null) === current;
    els.noteFolder.append(option);
  }
}

async function loadEditor() {
  const store = state.activeKind === "notes" ? "notes" : "reminders";
  const item = await get(store, state.selectedItemId);

  if (!item) {
    state.mode = "browse";
    state.selectedItemId = null;
    state.editChip = "";
    return;
  }

  if (state.activeKind === "notes") {
    els.noteTitle.value = item.title || "";
    els.noteBody.value = item.body || "";
    // A note pointing at a folder that no longer exists falls back to Unfiled,
    // so the picker and the chip cannot disagree with each other and saving the
    // note can never write the dangling id back.
    const folderId = state.folders.some(folder => folder.id === item.folderId)
      ? item.folderId
      : null;
    renderFolderPicker(folderId);
    state.editChip = folderId ? folderPath(folderId, state.folders) : "Unfiled";
    return;
  }

  els.reminderTitle.value = item.title || "";
  els.reminderBody.value = item.body || "";
  els.reminderStart.value = item.startAt ? localInputValue(new Date(item.startAt)) : localInputValue();
  els.reminderRepeat.value = item.recurrence?.kind || "once";
  els.reminderInterval.value = item.recurrence?.interval || 1;
  const days = new Set(item.recurrence?.weekdays || []);
  document.querySelectorAll("#weekday-picker input").forEach(box => {
    box.checked = days.has(Number(box.value));
  });
  state.editChip = item.nextDueAt
    ? relativeFromNow(item.nextDueAt)
    : describeRule(item.recurrence);
  syncWeekdayVisibility();
}

async function renderAll() {
  await renderFolders();
  await renderNoteList();
  await renderReminderManage();
  await renderAgenda();
  await syncUpper();
  if (state.mode === "edit") {
    // loadEditor fills the fields and computes the context chip, so the pane
    // chrome is synced again once it has run.
    await loadEditor();
    await syncUpper();
  }
}

/* ------------------------------------------------------------------ actions */

function startNewFolder() {
  const reveal = () => {
    els.newFolderForm.classList.remove("hidden");
    els.newFolderName.focus();
  };
  // Folders are a notes concept; make sure the tree is on screen first.
  if (state.activeKind !== "notes" || state.mode === "edit") {
    switchKind("notes").then(reveal);
    return;
  }
  reveal();
}

async function submitNewFolder(event) {
  event.preventDefault();
  const name = els.newFolderName.value.trim();
  if (!name) {
    els.newFolderForm.classList.add("hidden");
    return;
  }

  const parentId = state.selectedFolderId;
  await put("folders", {
    id: newId(),
    parentId,
    name,
    createdAt: nowIso(),
    updatedAt: nowIso()
  });

  els.newFolderName.value = "";
  els.newFolderForm.classList.add("hidden");
  // Reveal the folder that was just created under its parent.
  if (parentId) state.collapsed.delete(parentId);

  await renderAll();
}

/**
 * Deletes a folder, its subfolders and every note inside them. The confirmation
 * names the exact counts first, because this cannot be undone.
 */
async function deleteFolder(folderId) {
  const folders = await getAll("folders");
  const folder = folders.find(candidate => candidate.id === folderId);
  if (!folder) return;

  const doomedIds = new Set(collectSubtree(folderId, folders));
  const doomedNotes = (await getAll("notes")).filter(note => doomedIds.has(note.folderId));

  const confirmed = confirm(describeDeletion(folder.name, {
    subfolders: doomedIds.size - 1,
    notes: doomedNotes.length
  }));
  if (!confirmed) return;

  for (const note of doomedNotes) await remove("notes", note.id);
  for (const id of doomedIds) await remove("folders", id);

  // If anything showing was inside the deleted subtree, fall back to Unfiled.
  if (doomedIds.has(state.selectedFolderId)) state.selectedFolderId = null;
  // Deleting an absent key is a no-op, so this needs no membership test.
  for (const id of doomedIds) state.collapsed.delete(id);
  if (doomedNotes.some(note => note.id === state.selectedItemId)) {
    state.mode = "browse";
    state.selectedItemId = null;
  }

  await renderAll();
}

async function createNote() {
  const note = {
    id: newId(),
    folderId: state.selectedFolderId,
    title: "",
    body: "",
    createdAt: nowIso(),
    updatedAt: nowIso()
  };
  await put("notes", note);
  state.activeKind = "notes";
  state.mode = "edit";
  state.selectedItemId = note.id;
  await renderAll();
  els.noteTitle.focus();
}

async function createReminder() {
  const start = new Date();
  start.setMinutes(start.getMinutes() + 5, 0, 0);

  let reminder = {
    id: newId(),
    // Reminders are a flat list; they deliberately do not participate in folders.
    folderId: null,
    title: "",
    body: "",
    enabled: true,
    startAt: start.toISOString(),
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    recurrence: { version: 1, kind: "once", interval: 1, weekdays: [] },
    createdAt: nowIso(),
    updatedAt: nowIso()
  };
  reminder = reminderWithNextDue(reminder, new Date(Date.now() - 1));

  await put("reminders", reminder);
  state.activeKind = "reminders";
  state.mode = "edit";
  state.selectedItemId = reminder.id;
  await renderAll();
  els.reminderTitle.focus();
}

async function saveNote(event) {
  event.preventDefault();
  const note = await get("notes", state.selectedItemId);
  if (!note) return;

  // The picker's "Unfiled" option carries the empty string, which is normalised
  // back to null here: notes are indexed by folderId, and the Unfiled list is
  // read off that same index, so the two have to agree.
  const folderId = els.noteFolder?.value || null;

  await put("notes", {
    ...note,
    folderId,
    title: els.noteTitle.value,
    body: els.noteBody.value,
    updatedAt: nowIso()
  });
  await renderAll();
}

async function deleteSelectedNote() {
  const note = await get("notes", state.selectedItemId);
  if (!note) return;

  const title = note.title?.trim() || "Untitled note";
  if (!confirm(`Delete “${title}”? This cannot be undone.`)) return;

  await remove("notes", note.id);
  state.mode = "browse";
  state.selectedItemId = null;
  await renderAll();
}

function selectedWeekdays() {
  return [...document.querySelectorAll("#weekday-picker input:checked")]
    .map(box => Number(box.value));
}

async function saveReminder(event) {
  event.preventDefault();
  const current = await get("reminders", state.selectedItemId);
  if (!current || !els.reminderStart.value) return;

  const start = new Date(els.reminderStart.value);
  let reminder = {
    ...current,
    title: els.reminderTitle.value,
    body: els.reminderBody.value,
    startAt: start.toISOString(),
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    recurrence: {
      version: 1,
      kind: els.reminderRepeat.value,
      interval: Math.max(1, Number.parseInt(els.reminderInterval.value, 10) || 1),
      weekdays: selectedWeekdays()
    },
    updatedAt: nowIso()
  };

  // Compute against "now" so a schedule edited into the past resolves forward.
  reminder = reminderWithNextDue(reminder);
  await put("reminders", reminder);
  await renderAll();
}

async function deleteSelectedReminder() {
  const reminder = await get("reminders", state.selectedItemId);
  if (!reminder) return;

  const title = reminder.title?.trim() || "Untitled reminder";
  if (!confirm(`Delete “${title}”? This cannot be undone.`)) return;

  await remove("reminders", reminder.id);
  state.mode = "browse";
  state.selectedItemId = null;
  await renderAll();
}

async function switchKind(kind) {
  state.activeKind = kind;
  // Switching tabs always leaves the editor: the other tab is a list.
  state.mode = "browse";
  state.selectedItemId = null;
  els.newFolderForm.classList.add("hidden");
  await renderAll();
}

function syncWeekdayVisibility() {
  els.weekdayPicker.classList.toggle("hidden", els.reminderRepeat.value !== "weekly");
}

/* ------------------------------------------------------- settings & backup */

let exportBlobUrl = null;
// The exact text the last successful preview saw. A restore is only allowed to
// act on that text: if the textarea changed afterwards, the preview describes a
// file that is no longer there and the button goes inert again.
let previewedText = null;
// Held separately so a copy confirmation can be shown without erasing the
// oversize warning that explains why "Open email" is missing.
let oversizeNote = "";

function countText(count, noun) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/**
 * Put a top share on the split. The default 50% is the CSS fallback, so
 * resetting the properties would also be correct -- but setting them
 * explicitly keeps the chip and the tracks reading from the same value.
 * Anything outside the offered four (including the old a:b stored values)
 * reads as the default rather than as junk on screen.
 */
function applyPaneRatio(ratio) {
  const shell = $(".app-shell");
  const tracks = ratioToTracks(ratio);
  if (shell) {
    shell.style.setProperty("--pane-top", tracks.top);
    shell.style.setProperty("--pane-bottom", tracks.bottom);
  }
  const chip = $("#ratio-value");
  if (chip) chip.textContent = RATIOS.includes(ratio) ? ratio : DEFAULT_RATIO;
}

async function cyclePaneRatio() {
  const next = nextRatio(await getSetting("paneRatio", DEFAULT_RATIO));
  await setSetting("paneRatio", next);
  applyPaneRatio(next);
}

function openSettings() {
  const dialog = $("#settings-dialog");
  if (!dialog) return;
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

function closeSettings() {
  const dialog = $("#settings-dialog");
  if (!dialog) return;
  if (dialog.open && typeof dialog.close === "function") dialog.close();
  else dialog.removeAttribute("open");
}

function showPanel(which) {
  $("#export-panel")?.classList.toggle("hidden", which !== "export");
  $("#import-panel")?.classList.toggle("hidden", which !== "import");
}

function updateExportNote(copyMessage = "") {
  const note = $("#export-size-note");
  if (!note) return;
  note.textContent = oversizeNote && copyMessage
    ? `${oversizeNote} ${copyMessage}`
    : (oversizeNote || copyMessage);
}

function syncMailtoLink(csv) {
  const link = $("#open-email-btn");
  if (!link) return;

  const built = buildMailtoHref({
    to: $("#backup-email")?.value || "",
    subject: "Notes backup",
    body: csv
  });

  if (built.ok) {
    oversizeNote = "";
    link.href = built.href;
    link.removeAttribute("aria-disabled");
    link.classList.remove("hidden");
  } else {
    // Refuse rather than truncate: a backup that silently loses its tail is
    // worse than one that will not open. Copy and Download still have it all.
    oversizeNote = `Too large for an email app (${built.encodedLength} characters, limit ${built.limit}).`;
    link.href = "#";
    link.setAttribute("aria-disabled", "true");
    link.classList.add("hidden");
  }
  updateExportNote();
}

/**
 * Whether this browser can hand a real file to another app through the Web
 * Share API. Where it cannot (most desktop browsers), the Share button hides
 * itself: a visible control that opens nothing is the "the feature does not
 * exist" failure this project has already hit twice -- the hover-only folder
 * delete and the dead toolbar.
 */
function canShareCsvFiles() {
  try {
    if (typeof navigator.canShare !== "function") return false;
    return navigator.canShare({
      files: [new File(["# notes-backup v1"], "probe.csv", { type: "text/csv" })]
    });
  } catch (error) {
    return false;
  }
}

/**
 * Rebuild the export view from live data: the CSV itself, the download blob,
 * and the mailto handoff. Called every time the panel opens so the preview the
 * user sees is never staler than the database behind it.
 */
async function refreshExportCsv() {
  // Feature-detect first, synchronously, so the panel never shows a Share
  // button that this browser cannot act on.
  const share = $("#share-csv-btn");
  if (share) share.classList.toggle("hidden", !canShareCsvFiles());

  const [folders, notes, reminders] = await Promise.all([
    getAll("folders"), getAll("notes"), getAll("reminders")
  ]);
  const csv = buildBackupCsv({ folders, notes, reminders, exportedAt: nowIso() });

  const box = $("#export-csv");
  if (box) box.value = csv;

  const download = $("#download-csv-btn");
  if (download) {
    if (exportBlobUrl) URL.revokeObjectURL(exportBlobUrl);
    exportBlobUrl = URL.createObjectURL(
      new Blob([csv], { type: "text/csv;charset=utf-8" })
    );
    download.href = exportBlobUrl;
    download.download = `notes-backup-${nowIso().slice(0, 10)}.csv`;
  }

  syncMailtoLink(csv);
}

async function showExportPanel() {
  showPanel("export");
  await refreshExportCsv();
}

function showImportPanel() {
  showPanel("import");
  // Anything previewed belongs to whatever was in the box then.
  previewedText = null;
  const button = $("#restore-btn");
  if (button) button.disabled = true;
}

async function saveBackupEmail() {
  const address = $("#backup-email")?.value.trim() || "";
  await setSetting("backupEmail", address);
  const box = $("#export-csv");
  if (box) syncMailtoLink(box.value);
}

async function copyExportCsv() {
  const box = $("#export-csv");
  const text = box?.value || "";
  if (!text) return;

  let message;
  try {
    await navigator.clipboard.writeText(text);
    message = "Copied to the clipboard.";
  } catch (error) {
    // The clipboard API needs permission and a secure context; selecting the
    // box and falling back keeps the button useful either way.
    box.focus();
    box.select();
    let copied = false;
    try {
      copied = typeof document.execCommand === "function" && document.execCommand("copy");
    } catch (fallbackError) {
      copied = false;
    }
    message = copied
      ? "Copied to the clipboard."
      : "Copying was blocked — the text is selected, copy it with Ctrl+C.";
  }
  updateExportNote(message);
}

/**
 * Hand the CSV to another app as a real file. Unlike mailto (a URL, which
 * some OS/browser/client combinations silently truncate), a shared file keeps
 * every byte, so there is no size ceiling here. The user picks the target in
 * the system share sheet -- including their mail app, which attaches the file.
 */
async function shareExportCsv() {
  const csv = $("#export-csv")?.value || "";
  if (!csv) return;

  const file = new File([csv], `notes-backup-${nowIso().slice(0, 10)}.csv`,
                        { type: "text/csv;charset=utf-8" });
  if (typeof navigator.share !== "function" || !navigator.canShare({ files: [file] })) {
    updateExportNote("Sharing files is not supported in this browser — use Copy or Download.");
    return;
  }

  try {
    await navigator.share({ files: [file], title: "Notes backup" });
    updateExportNote("Shared.");
  } catch (error) {
    // AbortError is the user dismissing the share sheet — not a failure.
    if (error && error.name === "AbortError") return;
    // NotAllowedError is the browser or OS refusing the share. Seen in the
    // wild on Samsung Internet, which answers canShare(files) yes and then
    // denies the call itself — so the refusal must name the way out, not
    // echo a bare denial.
    if (error && error.name === "NotAllowedError") {
      const detail = error.message ? ` (${error.message})` : "";
      updateExportNote("The browser refused the share" + detail
        + " — use Download .csv and attach the file in your mail app,"
        + " or Copy CSV and paste it into the email.");
      return;
    }
    updateExportNote(`Sharing failed: ${error && error.message ? error.message : "unknown error"}`);
  }
}

function renderPreviewError(message) {
  const out = $("#preview-out");
  if (out) out.innerHTML = `<p class="bad">${esc(message)}</p>`;
}

/**
 * Parse the pasted text and show exactly what a restore would do: incoming
 * counts against current counts, plus every repair the parser had to make. The
 * restore button is enabled only for text that parses and only for the text
 * that was just previewed.
 */
async function previewRestore() {
  const text = $("#restore-input")?.value || "";
  const parsed = parseBackupCsv(text);
  const button = $("#restore-btn");
  const out = $("#preview-out");

  previewedText = parsed.ok ? text : null;
  if (button) button.disabled = !parsed.ok;
  if (!out) return;

  if (!parsed.ok) {
    out.innerHTML = parsed.errors.map(line => `<p class="bad">${esc(line)}</p>`).join("");
    return;
  }

  const [folders, notes, reminders] = await Promise.all([
    getAll("folders"), getAll("notes"), getAll("reminders")
  ]);
  const incoming = [
    countText(parsed.folders.length, "folder"),
    countText(parsed.notes.length, "note"),
    countText(parsed.reminders.length, "reminder")
  ].join(", ");
  const current = [
    countText(folders.length, "folder"),
    countText(notes.length, "note"),
    countText(reminders.length, "reminder")
  ].join(", ");

  out.innerHTML = [
    `<p>Backup holds <strong>${esc(incoming)}</strong>${parsed.exportedAt ? ` (exported ${esc(parsed.exportedAt)})` : ""}.</p>`,
    `<p>This device currently holds <strong>${esc(current)}</strong>.</p>`,
    ...parsed.warnings.map(warning => `<p class="warn">${esc(warning)}</p>`),
    '<p class="muted">Restoring deletes everything on this device first. Your settings are kept.</p>'
  ].join("");
}

/**
 * Replace every folder, note and reminder with the previewed backup.
 *
 * The whole document is re-parsed here rather than trusted from the preview:
 * the confirmation, the commit and the preview must all describe the same
 * bytes, or a stale preview could authorise a different paste.
 */
async function restoreBackup() {
  const text = $("#restore-input")?.value || "";
  if (previewedText !== text) {
    previewedText = null;
    const button = $("#restore-btn");
    if (button) button.disabled = true;
    renderPreviewError("The pasted text changed after it was previewed — press Preview import again.");
    return;
  }

  const parsed = parseBackupCsv(text);
  if (!parsed.ok) {
    previewedText = null;
    const button = $("#restore-btn");
    if (button) button.disabled = true;
    renderPreviewError(parsed.errors.join(" "));
    return;
  }

  const [currentFolders, currentNotes, currentReminders] = await Promise.all([
    getAll("folders"), getAll("notes"), getAll("reminders")
  ]);

  const confirmed = confirm(describeRestore({
    current: {
      folders: currentFolders.length,
      notes: currentNotes.length,
      reminders: currentReminders.length
    },
    incoming: {
      folders: parsed.folders.length,
      notes: parsed.notes.length,
      reminders: parsed.reminders.length
    },
    exportedAt: parsed.exportedAt
  }));
  if (!confirmed) return;

  await replaceAll({
    folders: parsed.folders,
    notes: parsed.notes,
    reminders: parsed.reminders
  });

  // Every selection pointed at rows that no longer exist.
  state.selectedFolderId = null;
  state.selectedItemId = null;
  state.mode = "browse";
  state.collapsed.clear();

  // The backup never carries nextDueAt; it is rebuilt from startAt + rule.
  await refreshReminderSchedule();
  await renderAll();

  previewedText = null;
  const button = $("#restore-btn");
  if (button) button.disabled = true;
  const out = $("#preview-out");
  if (out) {
    out.innerHTML = `<p class="muted">Restored ${esc([
      countText(parsed.folders.length, "folder"),
      countText(parsed.notes.length, "note"),
      countText(parsed.reminders.length, "reminder")
    ].join(", "))}.</p>`;
  }
}

/** Keep the phone's status-bar tint in step with the automatic dark theme. */
function syncThemeColor() {
  const meta = document.querySelector('meta[name="theme-color"]');
  if (!meta) return;
  meta.content = window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "#0e1421"
    : "#f5f5f5";
}

/**
 * Attach a listener, tolerating an element that is not there.
 *
 * A published update can briefly pair a new index.html with a still-cached
 * app.js. Throwing on a missing element at wiring time would leave every control
 * dead, so a mismatch degrades instead: the missing element is reported and the
 * controls that do exist keep working.
 */
function on(selector, event, handler) {
  const el = $(selector);
  if (!el) {
    console.error(`Missing element ${selector}; its ${event} handler was not attached.`);
    return;
  }
  el.addEventListener(event, domEvent => {
    // Every handler here is async or calls something that is. An unawaited
    // rejection would otherwise be completely silent -- the button would simply
    // appear to do nothing, which is the hardest kind of failure to diagnose.
    try {
      const result = handler(domEvent);
      if (result && typeof result.catch === "function") {
        result.catch(error => reportFailure(error, `"${selector}" ${event}`));
      }
    } catch (error) {
      reportFailure(error, `"${selector}" ${event}`);
    }
  });
}

/**
 * Wired before anything that can fail. This used to run after the first render,
 * so one thrown error left the whole toolbar inert with nothing on screen
 * explaining why -- the failure looked like "the buttons do nothing".
 */
function wireControls() {
  on("#new-folder-btn", "click", startNewFolder);
  on("#add-folder-inline", "click", startNewFolder);
  on("#new-note-btn", "click", createNote);
  on("#new-reminder-btn", "click", createReminder);
  on("#settings-btn", "click", openSettings);
  on("#settings-close", "click", closeSettings);
  on("#ratio-btn", "click", cyclePaneRatio);
  on("#export-btn", "click", showExportPanel);
  on("#import-btn", "click", showImportPanel);
  on("#copy-csv-btn", "click", copyExportCsv);
  on("#share-csv-btn", "click", shareExportCsv);
  on("#backup-email", "change", saveBackupEmail);
  on("#preview-btn", "click", previewRestore);
  on("#restore-btn", "click", restoreBackup);
  on("#delete-note-btn", "click", deleteSelectedNote);
  on("#delete-reminder-btn", "click", deleteSelectedReminder);
  on("#editor-back", "click", backToBrowse);
  on("#notes-tab", "click", () => switchKind("notes"));
  on("#reminders-tab", "click", () => switchKind("reminders"));
  on("#new-folder-form", "submit", submitNewFolder);
  on("#note-editor", "submit", saveNote);
  on("#reminder-editor", "submit", saveReminder);
  on("#reminder-repeat", "change", syncWeekdayVisibility);
  on("#add-media-btn", "click", () => $("#media-input")?.click());
  on("#media-input", "change", () => {
    alert("Media bytes are deliberately deferred until the OPFS/IndexedDB storage path is validated.");
  });
  on("#new-folder-name", "keydown", event => {
    if (event.key === "Escape") {
      els.newFolderName.value = "";
      els.newFolderForm.classList.add("hidden");
    }
  });
}

/**
 * The status bar is hidden at rest, so writing to it has to reveal it. Nothing
 * else shows it: it exists for failures, and a failure that stayed hidden would
 * be the dead-toolbar outage all over again -- the app looks fine and silently
 * does nothing.
 */
function showError(message) {
  if (!els.appStatus) return;
  els.appStatus.textContent = message;
  if (els.statusbar) els.statusbar.hidden = false;
}

/** A failed action says so, instead of looking like a button that does nothing. */
function reportFailure(error, what) {
  console.error(`${what} failed:`, error);
  showError(`${what} failed: ${error.message}`);
}

/** A startup failure the user can act on, not a line of small print. */
function reportStartupFailure(error) {
  console.error(error);
  document.body.dataset.fatal = "true";
  showError(
    `Startup error: ${error.message} — reload the page. A new version may have been published and this one could not start.`
  );
}

async function init() {
  syncThemeColor();
  window.matchMedia("(prefers-color-scheme: dark)")
    .addEventListener?.("change", syncThemeColor);

  wireControls();

  await openDatabase();

  // Device preferences must be in place before the first paint: the pane split
  // and the last address the backup was exported to.
  applyPaneRatio(await getSetting("paneRatio", DEFAULT_RATIO));
  const savedEmail = await getSetting("backupEmail", "");
  if (savedEmail) {
    const input = $("#backup-email");
    if (input) input.value = savedEmail;
  }

  try {
    await requestPersistentStorage();
  } catch (error) {
    console.warn("Persistent storage request failed:", error);
  }

  // Cached due dates must be current before anything sorts by them.
  const rescheduled = await refreshReminderSchedule();
  if (rescheduled) {
    console.info(`Recomputed ${rescheduled} reminder due date(s).`);
  }

  await renderAll();

  // An explicit readiness signal. The browser check used to infer that startup
  // had finished from the status bar's storage text changing -- a proxy that
  // vanished with the text, and a poor one anyway: it raced nothing and proved
  // nothing. A failed startup sets data-fatal instead, so a probe can wait for
  // either and never guess from a string the app happens to print.
  document.body.dataset.ready = "true";

  if ("serviceWorker" in navigator) {
    // A device that is already running an older build is controlled by the old
    // worker for the whole of this page load, so it can pair fresh markup with
    // stale script. Once the new worker takes over, reload once so the page is
    // served entirely from the new release.
    const hadController = Boolean(navigator.serviceWorker.controller);
    let reloading = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (!hadController || reloading) return;
      reloading = true;
      location.reload();
    });

    navigator.serviceWorker.register("./sw.js").catch(error => {
      console.warn("Service worker registration failed:", error);
    });
  }
}

init().catch(reportStartupFailure);
