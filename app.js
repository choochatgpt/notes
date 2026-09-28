import {
  get,
  getAll,
  getAllByIndex,
  newId,
  openDatabase,
  put,
  remove,
  requestPersistentStorage
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
  relativeFromNow,
  sortReminders
} from "./view.js";

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

function backupPlaceholder() {
  alert("Backup/restore transport is intentionally not wired yet. The next phase will create a versioned local text backup first, then add email/share transport.");
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
  on("#backup-btn", "click", backupPlaceholder);
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
