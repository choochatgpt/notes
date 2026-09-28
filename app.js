import {
  getAll,
  getAllByIndex,
  newId,
  openDatabase,
  put,
  requestPersistentStorage,
  storageEstimate
} from "./storage.js";
import { reminderWithNextDue } from "./reminder.js";

const state = {
  activeKind: "notes",
  selectedFolderId: null,
  selectedItemId: null
};

const $ = selector => document.querySelector(selector);
const els = {
  folderTree: $("#folder-tree"),
  itemList: $("#item-list"),
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
  storageStatus: $("#storage-status"),
  reminderStatus: $("#reminder-status")
};

function nowIso() {
  return new Date().toISOString();
}

function localInputValue(date = new Date()) {
  const pad = value => String(value).padStart(2, "0");
  return [
    date.getFullYear(),
    "-",
    pad(date.getMonth() + 1),
    "-",
    pad(date.getDate()),
    "T",
    pad(date.getHours()),
    ":",
    pad(date.getMinutes())
  ].join("");
}

function escapeText(text) {
  const span = document.createElement("span");
  span.textContent = text ?? "";
  return span.innerHTML;
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

async function renderFolders() {
  const folders = await getAll("folders");
  const byParent = new Map();

  for (const folder of folders) {
    const parent = folder.parentId ?? null;
    if (!byParent.has(parent)) byParent.set(parent, []);
    byParent.get(parent).push(folder);
  }

  for (const list of byParent.values()) {
    list.sort((a, b) => a.name.localeCompare(b.name));
  }

  const rows = [];
  rows.push(`<div class="folder-row ${state.selectedFolderId === null ? "active" : ""}" data-folder="">Root</div>`);

  const walk = (parentId, depth) => {
    for (const folder of byParent.get(parentId) || []) {
      rows.push(
        `<div class="folder-row ${state.selectedFolderId === folder.id ? "active" : ""}" ` +
        `data-folder="${folder.id}" style="padding-left:${8 + depth * 18}px">${escapeText(folder.name)}</div>`
      );
      walk(folder.id, depth + 1);
    }
  };
  walk(null, 0);

  els.folderTree.innerHTML = rows.join("");
  els.folderTree.querySelectorAll(".folder-row").forEach(row => {
    row.addEventListener("click", async () => {
      state.selectedFolderId = row.dataset.folder || null;
      state.selectedItemId = null;
      await renderAll();
    });
  });
}

async function itemsForCurrentFolder() {
  const store = state.activeKind === "notes" ? "notes" : "reminders";
  const all = state.selectedFolderId === null
    ? (await getAll(store)).filter(item => item.folderId === null || item.folderId === undefined)
    : await getAllByIndex(store, "folderId", state.selectedFolderId);

  return all.sort((a, b) =>
    (b.updatedAt || b.createdAt || "").localeCompare(a.updatedAt || a.createdAt || "")
  );
}

async function renderItems() {
  const items = await itemsForCurrentFolder();

  if (!items.length) {
    els.itemList.innerHTML = `<p class="muted">No ${state.activeKind} in this folder.</p>`;
    return;
  }

  els.itemList.innerHTML = items.map(item => {
    const title = item.title?.trim() || (state.activeKind === "notes" ? "Untitled note" : "Untitled reminder");
    const extra = state.activeKind === "reminders" && item.nextDueAt
      ? `<small>Next: ${escapeText(new Date(item.nextDueAt).toLocaleString())}</small>`
      : "";
    return `<div class="item-row ${state.selectedItemId === item.id ? "active" : ""}" data-item="${item.id}">
      <div>${escapeText(title)}</div>${extra}
    </div>`;
  }).join("");

  els.itemList.querySelectorAll(".item-row").forEach(row => {
    row.addEventListener("click", async () => {
      state.selectedItemId = row.dataset.item;
      await openSelectedItem();
      await renderItems();
    });
  });
}

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
  const items = await getAll(store);
  const item = items.find(row => row.id === state.selectedItemId);

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

async function createFolder() {
  const name = prompt("Folder name");
  if (!name?.trim()) return;

  await put("folders", {
    id: newId(),
    parentId: state.selectedFolderId,
    name: name.trim(),
    createdAt: nowIso(),
    updatedAt: nowIso()
  });

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
  await renderItems();
  await openSelectedItem();
}

async function createReminder() {
  const start = new Date();
  start.setMinutes(start.getMinutes() + 5, 0, 0);

  let reminder = {
    id: newId(),
    folderId: state.selectedFolderId,
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
}

async function saveNote(event) {
  event.preventDefault();
  const items = await getAll("notes");
  const note = items.find(row => row.id === state.selectedItemId);
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
  const items = await getAll("reminders");
  const current = items.find(row => row.id === state.selectedItemId);
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

  reminder = reminderWithNextDue(reminder);
  await put("reminders", reminder);
  els.reminderStatus.textContent = reminder.nextDueAt
    ? `Next reminder: ${new Date(reminder.nextDueAt).toLocaleString()}`
    : "No future occurrence";
  await renderItems();
}

function syncTabs() {
  const notes = state.activeKind === "notes";
  els.notesTab.classList.toggle("active", notes);
  els.remindersTab.classList.toggle("active", !notes);
}

async function switchKind(kind) {
  state.activeKind = kind;
  state.selectedItemId = null;
  syncTabs();
  showEmptyEditor();
  await renderItems();
}

function syncWeekdayVisibility() {
  els.weekdayPicker.classList.toggle("hidden", els.reminderRepeat.value !== "weekly");
}

function backupPlaceholder() {
  alert("Backup/restore transport is intentionally not wired yet. The next phase will create a versioned local text backup first, then add email/share transport.");
}

async function init() {
  await openDatabase();

  try {
    await requestPersistentStorage();
  } catch (error) {
    console.warn("Persistent storage request failed:", error);
  }

  await refreshStorageStatus();
  await renderAll();

  $("#new-folder-btn").addEventListener("click", createFolder);
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
