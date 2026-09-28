import {
  get,
  getAll,
  getAllByIndex,
  newId,
  openDatabase,
  put,
  remove,
  requestPersistentStorage,
  storageEstimate
} from "./storage.js";
import { reminderWithNextDue } from "./reminder.js";
import {
  absoluteLabel,
  collectSubtree,
  describeDeletion,
  describeRule,
  esc,
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
  reminderTitle: $("#reminder-title"),
  reminderBody: $("#reminder-body"),
  reminderStart: $("#reminder-start"),
  reminderRepeat: $("#reminder-repeat"),
  reminderInterval: $("#reminder-interval"),
  weekdayPicker: $("#weekday-picker"),
  newFolderForm: $("#new-folder-form"),
  newFolderName: $("#new-folder-name"),
  storageStatus: $("#storage-status"),
  reminderStatus: $("#reminder-status")
};

function nowIso() {
  return new Date().toISOString();
}

/** "4 notes" / "1 note" / "" -- the counts are always stated before a delete. */
function countLabel(count, noun) {
  if (!count) return "";
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

async function refreshStorageStatus() {
  const estimate = await storageEstimate();
  const persistent = navigator.storage?.persisted
    ? await navigator.storage.persisted()
    : false;

  if (!estimate) {
    els.storageStatus.textContent = "Local storage ready";
    return;
  }

  const usedMb = ((estimate.usage || 0) / 1048576).toFixed(1);
  const quotaMb = ((estimate.quota || 0) / 1048576).toFixed(0);
  els.storageStatus.textContent =
    `Local storage: ${usedMb} MB / ${quotaMb} MB${persistent ? " · persistent" : ""}`;
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

async function refreshReminderStatus(reminders) {
  const list = reminders || await getAll("reminders");
  const upcoming = sortReminders(list).filter(reminder => reminder.nextDueAt);

  if (!upcoming.length) {
    els.reminderStatus.textContent = list.length
      ? "Reminders: none upcoming"
      : "Reminder engine: idle";
    return;
  }
  const next = upcoming[0];
  els.reminderStatus.textContent =
    `Next: ${relativeFromNow(next.nextDueAt)} — ${next.title?.trim() || "Untitled reminder"}`;
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

function reminderRow(reminder) {
  const title = reminder.title?.trim() || "Untitled reminder";
  const due = reminder.nextDueAt;
  const rule = describeRule(reminder.recurrence);

  const when = due
    ? `<span class="item-when">
         <span class="when-rel">${esc(relativeFromNow(due))}</span>
         <span class="when-abs">${esc(absoluteLabel(due))}</span>
       </span>`
    : `<span class="item-when"><span class="when-abs">no future date</span></span>`;

  return `<button class="item-row ${due ? "" : "past"} ${state.mode === "edit" && state.selectedItemId === reminder.id ? "active" : ""}"
                  type="button" data-item="${esc(reminder.id)}">
    <svg class="icon"><use href="#i-clock"></use></svg>
    <span class="item-main">
      <span class="item-title">${esc(title)}</span>
      <span class="item-rule">
        <span class="chip ${due ? "accent" : "past"}">${esc(due ? rule : "Past")}</span>
      </span>
    </span>
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
    state.editChip = item.folderId
      ? folderPath(item.folderId, state.folders)
      : "Unfiled";
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
  await refreshReminderStatus();
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
  await refreshStorageStatus();
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

  await put("notes", {
    ...note,
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
  await refreshStorageStatus();
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
  await refreshStorageStatus();
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

async function init() {
  syncThemeColor();
  window.matchMedia("(prefers-color-scheme: dark)")
    .addEventListener?.("change", syncThemeColor);

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

  await refreshStorageStatus();
  await renderAll();

  $("#new-folder-btn").addEventListener("click", startNewFolder);
  $("#add-folder-inline").addEventListener("click", startNewFolder);
  els.newFolderForm.addEventListener("submit", submitNewFolder);
  els.newFolderName.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      els.newFolderName.value = "";
      els.newFolderForm.classList.add("hidden");
    }
  });

  $("#new-note-btn").addEventListener("click", createNote);
  $("#new-reminder-btn").addEventListener("click", createReminder);
  $("#backup-btn").addEventListener("click", backupPlaceholder);
  els.notesTab.addEventListener("click", () => switchKind("notes"));
  els.remindersTab.addEventListener("click", () => switchKind("reminders"));
  els.editorBack.addEventListener("click", backToBrowse);
  els.noteEditor.addEventListener("submit", saveNote);
  els.reminderEditor.addEventListener("submit", saveReminder);
  $("#delete-note-btn").addEventListener("click", deleteSelectedNote);
  $("#delete-reminder-btn").addEventListener("click", deleteSelectedReminder);
  els.reminderRepeat.addEventListener("change", syncWeekdayVisibility);
  $("#add-media-btn").addEventListener("click", () => $("#media-input").click());
  $("#media-input").addEventListener("change", () => {
    alert("Media bytes are deliberately deferred until the OPFS/IndexedDB storage path is validated.");
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("./sw.js").catch(error => {
      console.warn("Service worker registration failed:", error);
    });
  }
}

init().catch(error => {
  console.error(error);
  els.storageStatus.textContent = `Startup error: ${error.message}`;
});
