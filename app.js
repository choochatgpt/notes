import {
  get,
  getAll,
  getAllByIndex,
  newId,
  openDatabase,
  put,
  requestPersistentStorage,
  storageEstimate
} from "./storage.js";
import { reminderWithNextDue } from "./reminder.js";
import {
  absoluteLabel,
  describeRule,
  esc,
  folderPath,
  localInputValue,
  relativeFromNow,
  sortReminders
} from "./view.js";

const state = {
  activeKind: "notes",
  selectedFolderId: null,
  selectedItemId: null,
  // Folders the user has closed in the tree. Session-only: a collapsed branch is
  // a view detail, not part of the note data.
  collapsed: new Set(),
  folders: []
};

const $ = selector => document.querySelector(selector);
const els = {
  folderTree: $("#folder-tree"),
  itemList: $("#item-list"),
  listContext: $("#list-context"),
  notesTab: $("#notes-tab"),
  remindersTab: $("#reminders-tab"),
  noteEditor: $("#note-editor"),
  reminderEditor: $("#reminder-editor"),
  emptyEditor: $("#empty-editor"),
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

  const byParent = new Map();
  for (const folder of folders) {
    const parent = folder.parentId ?? null;
    if (!byParent.has(parent)) byParent.set(parent, []);
    byParent.get(parent).push(folder);
  }
  for (const list of byParent.values()) {
    list.sort((a, b) => a.name.localeCompare(b.name));
  }

  const rootCount = counts.get(null) || 0;
  const rows = [
    `<div class="folder-row ${state.selectedFolderId === null ? "active" : ""}" data-folder="">
       <span class="folder-toggle leaf"></span>
       <svg class="icon"><use href="#i-folder"></use></svg>
       <span class="folder-name">Unfiled</span>
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
           <svg class="icon"><use href="#i-folder"></use></svg>
           <span class="folder-name">${esc(folder.name)}</span>
           ${count ? `<span class="count">${count}</span>` : ""}
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

      state.selectedFolderId = rowEl.dataset.folder || null;
      state.selectedItemId = null;
      showEmptyEditor();
      await renderItems();
      await renderFolders();
    });
  });
}

/* -------------------------------------------------------------------- lists */

async function renderNotes() {
  const notes = state.selectedFolderId === null
    ? (await getAll("notes")).filter(note => note.folderId === null || note.folderId === undefined)
    : await getAllByIndex("notes", "folderId", state.selectedFolderId);

  notes.sort((a, b) =>
    (b.updatedAt || b.createdAt || "").localeCompare(a.updatedAt || a.createdAt || "")
  );

  els.listContext.textContent = state.selectedFolderId
    ? folderPath(state.selectedFolderId, state.folders)
    : "Unfiled";

  if (!notes.length) {
    els.itemList.innerHTML = `<p class="muted">Nothing here yet — use “New note”.</p>`;
    return;
  }

  els.itemList.innerHTML = notes.map(note => {
    const title = note.title?.trim() || "Untitled note";
    const snippet = (note.body || "").replace(/\s+/g, " ").trim();
    const when = note.updatedAt ? relativeFromNow(note.updatedAt) : "";

    return `<div class="item-row ${state.selectedItemId === note.id ? "active" : ""}"
                 data-item="${esc(note.id)}">
      <div class="item-main">
        <div class="item-title">${esc(title)}</div>
        <div class="item-meta">
          ${snippet
            ? `<span class="snippet">${esc(snippet.slice(0, 90))}</span>`
            : `<span class="dim">Empty</span>`}
          ${when ? `<span class="dim">· ${esc(when)}</span>` : ""}
        </div>
      </div>
    </div>`;
  }).join("");

  wireItemRows();
}

async function renderReminders() {
  const reminders = sortReminders(await getAll("reminders"));

  els.listContext.textContent = reminders.length
    ? `${reminders.length} · soonest first`
    : "";

  if (!reminders.length) {
    els.itemList.innerHTML = `<p class="muted">No reminders yet — use “New reminder”.</p>`;
    return;
  }

  els.itemList.innerHTML = reminders.map(reminder => {
    const title = reminder.title?.trim() || "Untitled reminder";
    const due = reminder.nextDueAt;
    const meta = due
      ? `<svg class="icon tiny"><use href="#i-clock"></use></svg>
         <span class="due">${esc(relativeFromNow(due))}</span>
         <span class="dim">${esc(absoluteLabel(due))}</span>`
      : `<span class="dim">No future occurrence</span>`;

    return `<div class="item-row ${state.selectedItemId === reminder.id ? "active" : ""}"
                 data-item="${esc(reminder.id)}">
      <div class="item-main">
        <div class="item-title">${esc(title)}</div>
        <div class="item-meta">${meta}</div>
      </div>
      <span class="chip ${due ? "" : "past"}">${esc(due ? describeRule(reminder.recurrence) : "Past")}</span>
    </div>`;
  }).join("");

  wireItemRows();
}

function wireItemRows() {
  els.itemList.querySelectorAll(".item-row").forEach(rowEl => {
    rowEl.addEventListener("click", async () => {
      state.selectedItemId = rowEl.dataset.item;
      await openSelectedItem();
      await renderItems();
    });
  });
}

function renderItems() {
  return state.activeKind === "reminders" ? renderReminders() : renderNotes();
}

/* ------------------------------------------------------------------ editors */

function showEditor(kind) {
  els.emptyEditor.classList.add("hidden");
  els.noteEditor.classList.toggle("hidden", kind !== "notes");
  els.reminderEditor.classList.toggle("hidden", kind !== "reminders");
}

function showEmptyEditor() {
  els.emptyEditor.classList.remove("hidden");
  els.noteEditor.classList.add("hidden");
  els.reminderEditor.classList.add("hidden");
}

async function openSelectedItem() {
  if (!state.selectedItemId) {
    showEmptyEditor();
    return;
  }

  const store = state.activeKind === "notes" ? "notes" : "reminders";
  const item = await get(store, state.selectedItemId);

  if (!item) {
    state.selectedItemId = null;
    showEmptyEditor();
    return;
  }

  showEditor(state.activeKind);

  if (state.activeKind === "notes") {
    els.noteTitle.value = item.title || "";
    els.noteBody.value = item.body || "";
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
  syncWeekdayVisibility();
}

async function renderAll() {
  await renderFolders();
  await renderItems();
  if (!state.selectedItemId) showEmptyEditor();
}

/* ------------------------------------------------------------------ actions */

function startNewFolder() {
  if (state.activeKind !== "notes") {
    // Folders are a notes concept; make sure the tree is on screen first.
    switchKind("notes").then(() => {
      els.newFolderForm.classList.remove("hidden");
      els.newFolderName.focus();
    });
    return;
  }
  els.newFolderForm.classList.remove("hidden");
  els.newFolderName.focus();
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

  await renderFolders();
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
  state.selectedItemId = note.id;
  syncTabs();
  await renderFolders();
  await renderItems();
  await openSelectedItem();
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
  state.selectedItemId = reminder.id;
  syncTabs();
  await renderItems();
  await openSelectedItem();
  await refreshReminderStatus();
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
  await renderItems();
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
  await renderItems();
  await refreshReminderStatus();
}

function syncTabs() {
  const notes = state.activeKind === "notes";
  document.body.dataset.kind = state.activeKind;
  els.notesTab.classList.toggle("active", notes);
  els.remindersTab.classList.toggle("active", !notes);
  els.notesTab.setAttribute("aria-selected", String(notes));
  els.remindersTab.setAttribute("aria-selected", String(!notes));
}

async function switchKind(kind) {
  state.activeKind = kind;
  state.selectedItemId = null;
  syncTabs();
  els.newFolderForm.classList.add("hidden");
  showEmptyEditor();
  await renderItems();
  await refreshReminderStatus();
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

  syncTabs();
  await refreshStorageStatus();
  await renderAll();
  await refreshReminderStatus();

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
  els.noteEditor.addEventListener("submit", saveNote);
  els.reminderEditor.addEventListener("submit", saveReminder);
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
