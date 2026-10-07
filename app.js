import {
  get,
  getAll,
  getAllByIndex,
  getSetting,
  newId,
  openDatabase,
  opfsDelete,
  opfsGet,
  opfsPut,
  put,
  remove,
  replaceAll,
  requestPersistentStorage,
  setSetting
} from "./storage.js";
import { reminderWithNextDue } from "./reminder.js";
import {
  APP_VERSION,
  absoluteLabel,
  collectSubtree,
  describeDeletion,
  describeRule,
  esc,
  folderOptions,
  folderBadgeCounts,
  folderPath,
  localInputValue,
  nextRatio,
  nextFolderRatio,
  noteSnippet,
  attachmentBadge,
  RATIOS,
  ratioToTracks,
  FOLDER_RATIOS,
  folderRatioToTracks,
  DEFAULT_RATIO,
  DEFAULT_FOLDER_RATIO,
  relativeFromNow,
  sortFoldersSiblings,
  sortReminders
} from "./view.js";
import {
  buildBackupCsv,
  buildMailtoHref,
  describeRestore,
  parseBackupCsv
} from "./backup.js";
import {
  MAX_FILE_BYTES as SYNC_MAX_FILE_BYTES,
  checkPickedUp,
  clearToken,
  hasToken,
  newExportId,
  sanitizeToken,
  setToken,
  submit as syncSubmit
} from "./sync.js";
import {
  buildZip,
  ensureBackupFolder,
  friendlyDriveError,
  getClientId,
  hasClientId,
  requestToken,
  uploadBackup,
  utf8
} from "./drive.js";

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
  // The folder row currently being renamed and its in-progress text, so a
  // re-render (any action after an edit) does not swallow what was typed.
  // Cleared by tab switch, folder delete, restore and the save/cancel buttons.
  renamingFolderId: null,
  renamingDraft: null,
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
  mediaStrip: $("#media-strip"),
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
  // Unfiled has no child folders (the tree's root folders are not "inside"
  // it), so its only extra chip is the attachment load of its own notes.
  const unfiledBadges = folderBadgeCounts(folders, notes, null);
  const unfiledAttachChip = unfiledBadges.attachments
    ? `<span class="count" title="${esc(unfiledBadges.attachments)} attachment${unfiledBadges.attachments === 1 ? "" : "s"} in Unfiled"><svg class="icon"><use href="#i-clip"></use></svg>${unfiledBadges.attachments}</span>`
    : "";
  state.folderChip = state.selectedFolderId
    ? `${folderPath(state.selectedFolderId, folders)} · ${countLabel(counts.get(state.selectedFolderId) || 0, "note") || "0 notes"}`
    : `Unfiled · ${countLabel(rootCount, "note") || "0 notes"}`;

  const byParent = new Map();
  for (const folder of folders) {
    const parent = folder.parentId ?? null;
    if (!byParent.has(parent)) byParent.set(parent, []);
    byParent.get(parent).push(folder);
  }
  for (const [parent, list] of byParent) {
    byParent.set(parent, sortFoldersSiblings(list));
  }

  const rows = [
    `<div class="folder-row ${state.selectedFolderId === null ? "active" : ""}" data-folder="">
       <span class="folder-toggle leaf"></span>
       <span class="folder-select">
         <svg class="icon"><use href="#i-folder"></use></svg>
         <span class="folder-name">Unfiled</span>
       </span>
       ${rootCount ? `<span class="count">${rootCount}</span>` : ""}
       ${unfiledAttachChip}
     </div>`
  ];

  // The v35 row chips beside the note count: the direct child-folder count
  // and the attachment load of the WHOLE subtree (a collapsed folder still
  // says what it carries). Zero chips stay hidden -- the row is already
  // crowded at phone width.
  const badgesOf = folder => {
    const badges = folderBadgeCounts(folders, notes, folder.id);
    const folderChip = badges.subfolders
      ? `<span class="count" title="${esc(badges.subfolders)} subfolder${badges.subfolders === 1 ? "" : "s"}"><svg class="icon"><use href="#i-folder"></use></svg>${badges.subfolders}</span>`
      : "";
    const attachChip = badges.attachments
      ? `<span class="count" title="${esc(badges.attachments)} attachment${badges.attachments === 1 ? "" : "s"} in this folder"><svg class="icon"><use href="#i-clip"></use></svg>${badges.attachments}</span>`
      : "";
    return { folderChip, attachChip };
  };

  const walk = (parentId, depth) => {
    for (const folder of byParent.get(parentId) || []) {
      const hasChildren = (byParent.get(folder.id) || []).length > 0;
      const open = hasChildren && !state.collapsed.has(folder.id);
      const count = counts.get(folder.id) || 0;
      const badges = badgesOf(folder);

      if (folder.id === state.renamingFolderId) {
        // The rename row swaps the row controls for a small form. There is no
        // Cancel button in the tree chrome around it: Escape cancels on a
        // keyboard, and the visible ✕ answers every phone keyboard, which has
        // no Escape key. Nothing happens on blur -- blur fires before a Save
        // click lands and would commit half-typed text.
        rows.push(
          `<div class="folder-row editing" data-folder="${esc(folder.id)}"
                data-renaming="${esc(folder.id)}" style="padding-left:${5 + depth * 8}px">
             <span class="folder-toggle leaf"></span>
             <form class="folder-rename-form" autocomplete="off">
               <input class="folder-rename-input" type="text"
                      value="${esc(state.renamingDraft ?? folder.name)}"
                      aria-label="Folder name">
               <button class="btn small primary" type="submit">Save</button>
               <button class="folder-rename-cancel" type="button"
                       title="Cancel renaming" aria-label="Cancel renaming">&#x2715;</button>
             </form>
           </div>`
        );
        if (open) walk(folder.id, depth + 1);
        continue;
      }

      rows.push(
        `<div class="folder-row ${state.selectedFolderId === folder.id ? "active" : ""}"
              data-folder="${esc(folder.id)}" style="padding-left:${5 + depth * 8}px">
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
           ${badges.folderChip}
           ${badges.attachChip}
           <button class="folder-up" type="button" data-up="${esc(folder.id)}"
                   title="Move up" aria-label="Move ${esc(folder.name)} up">
             <svg class="icon"><use href="#i-chevron"></use></svg>
           </button>
           <button class="folder-down" type="button" data-down="${esc(folder.id)}"
                   title="Move down" aria-label="Move ${esc(folder.name)} down">
             <svg class="icon"><use href="#i-chevron"></use></svg>
           </button>
           <button class="folder-rename" type="button" data-rename="${esc(folder.id)}"
                   title="Rename folder" aria-label="Rename folder ${esc(folder.name)}">
             <svg class="icon"><use href="#i-pencil"></use></svg>
           </button>
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
    if (rowEl.dataset.renaming) wireRenameRow(rowEl);
    rowEl.addEventListener("click", async event => {
      // A renaming row is one form: its submit/cancel handlers are attached
      // above, and the select-or-toggle branches below must not fire through it.
      if (rowEl.dataset.renaming) return;

      const up = event.target.closest(".folder-up");
      if (up?.dataset.up) {
        await moveFolder(up.dataset.up, -1);
        return;
      }
      const down = event.target.closest(".folder-down");
      if (down?.dataset.down) {
        await moveFolder(down.dataset.down, 1);
        return;
      }
      const rename = event.target.closest(".folder-rename");
      if (rename?.dataset.rename) {
        await beginFolderRename(rename.dataset.rename);
        return;
      }

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

  // The attachment badge needs each note's media types. Only notes that
  // carry ids pay a read; dangling ids count nothing here (delete still names
  // the id count, because that is what it removes) -- the badge reports what
  // is actually on the device.
  const mediaByNote = new Map((await Promise.all(
    notes
      .filter(note => note.mediaIds?.length)
      .map(note => loadNoteMedia(note).then(records => [note.id, records]))
  )));

  els.noteList.innerHTML = notes.map(note => {
    const title = note.title?.trim() || "Untitled note";
    const snippet = noteSnippet(note.body || "");
    const when = note.updatedAt ? relativeFromNow(note.updatedAt) : "";
    const badge = attachmentBadge(mediaByNote.get(note.id) || []);

    return `<button class="item-row ${state.mode === "edit" && state.selectedItemId === note.id ? "active" : ""}"
                    type="button" data-item="${esc(note.id)}">
      <svg class="icon"><use href="#i-note"></use></svg>
      <span class="item-main">
        <span class="item-title">${esc(title)}</span>
        ${badge ? `<span class="item-attach" title="${esc(badge.title)}"><svg class="icon"><use href="#i-clip"></use></svg>${esc(badge.text)}</span>` : ""}
        <span class="item-sub">${snippet ? esc(snippet) : "Empty"}</span>
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

/**
 * Upper-pane visibility. Since v35 the two editors swap differently:
 * the note editor takes over the note LIST's panel -- the folder tree above
 * it stays, so you keep your bearings while a note is open -- while the
 * reminder editor still replaces the reminder manager wholesale, because
 * reminders have no tree to keep.
 */
function syncUpper() {
  const notes = state.activeKind === "notes";
  const editing = state.mode === "edit";
  const remindersEditing = editing && !notes;

  document.body.dataset.kind = state.activeKind;
  document.body.dataset.mode = state.mode;

  els.notesTab.classList.toggle("active", notes);
  els.remindersTab.classList.toggle("active", !notes);
  els.notesTab.setAttribute("aria-selected", String(notes));
  els.remindersTab.setAttribute("aria-selected", String(!notes));

  // The notes browser (tree + list slot) now shows whenever the notes tab is
  // on; inside it, the list and the editor swap. The reminders side keeps the
  // old full-pane swap.
  els.browseNotes.classList.toggle("hidden", !notes);
  els.noteList.classList.toggle("hidden", editing);
  els.noteEditor.classList.toggle("hidden", !editing || !notes);
  els.browseReminders.classList.toggle("hidden", notes || remindersEditing);
  els.editorHost.classList.toggle("hidden", !remindersEditing);
  els.editorBack.classList.toggle("hidden", !editing);
  els.reminderEditor.classList.toggle("hidden", !remindersEditing);

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
    // Load the records once: the strip and the top attach line must render
    // from the same snapshot, or they can disagree mid-flight.
    const records = await loadNoteMedia(item);
    renderMediaStrip(records);
    renderEditorAttach(records);
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
  // One editing interaction at a time: an open rename row ends when the new
  // folder form takes over the column.
  clearRenameState();
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
  if (doomedIds.has(state.renamingFolderId)) clearRenameState();

  await renderAll();
}

function clearRenameState() {
  state.renamingFolderId = null;
  state.renamingDraft = null;
}

/** Swaps a folder row into the inline rename form and puts the caret in it. */
async function beginFolderRename(folderId) {
  const folder = state.folders.find(candidate => candidate.id === folderId);
  if (!folder) return;
  state.renamingFolderId = folderId;
  state.renamingDraft = null;
  await renderFolders();
  const input = els.folderTree.querySelector(".folder-rename-input");
  if (input) {
    input.focus();
    input.select();
  }
}

/** Attaches the rename form's wiring to the freshly rendered editing row. */
function wireRenameRow(rowEl) {
  const folderId = rowEl.dataset.renaming;
  const form = rowEl.querySelector(".folder-rename-form");
  const input = rowEl.querySelector(".folder-rename-input");
  if (!form || !input) return;

  form.addEventListener("submit", event => {
    event.preventDefault();
    submitRenameFolder(folderId, input.value)
      .catch(error => reportFailure(error, "folder rename"));
  });
  input.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      event.preventDefault();
      cancelFolderRename();
    }
  });
  input.addEventListener("input", () => {
    state.renamingDraft = input.value;
  });
  const cancel = rowEl.querySelector(".folder-rename-cancel");
  if (cancel) cancel.addEventListener("click", cancelFolderRename);
}

/**
 * Commits a rename, or cancels the edit. An empty name keeps the old one
 * silently -- the same no-error convention as submitting an empty new-folder
 * form -- and an unchanged name is not written at all. Identity is the id, so
 * two folders may share a name, exactly as at creation.
 */
async function submitRenameFolder(folderId, name) {
  const folder = (await getAll("folders")).find(candidate => candidate.id === folderId);
  clearRenameState();
  if (folder && name && name !== folder.name) {
    await put("folders", { ...folder, name, updatedAt: nowIso() });
  }
  await renderAll();
}

function cancelFolderRename() {
  clearRenameState();
  renderFolders().catch(error => reportFailure(error, "folder rename"));
}

/**
 * Moves a folder one slot among its siblings, using the exact order the tree
 * displays (sortFoldersSiblings). Positions are 0..n-1 assigned across the
 * visible siblings, so the first move also freezes the previous alphabetical
 * display as the baseline; after that a brand-new folder without an order
 * appends at the end and never shuffles an arrangement already made. A lone
 * folder and a tap at the edge both write nothing: order is only ever written
 * when something actually moves. No confirmation -- rearranging is fully
 * reversible, next to a delete which absolutely is not.
 */
async function moveFolder(folderId, delta) {
  const folders = await getAll("folders");
  const moved = folders.find(candidate => candidate.id === folderId);
  if (!moved) return;

  const parent = moved.parentId ?? null;
  const group = sortFoldersSiblings(
    folders.filter(candidate => (candidate.parentId ?? null) === parent)
  );
  const index = group.indexOf(moved);
  const target = index + delta;
  if (group.length < 2 || target < 0 || target >= group.length) return;

  group.splice(target, 0, group.splice(index, 1)[0]);
  for (let i = 0; i < group.length; i += 1) {
    const row = group[i];
    if (row.order === i) continue;
    await put("folders", { ...row, order: i });
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
  // Attachments are named when they exist: the note's bytes go with it, and
  // the confirmation is the only warning before that happens. The count is
  // the id list -- that, not whatever the badge could load, is what goes.
  const attachCount = (note.mediaIds || []).length;
  const attachments = attachCount
    ? ` Its ${attachCount} attachment${attachCount === 1 ? "" : "s"} will be removed too.`
    : "";
  if (!confirm(`Delete “${title}”?${attachments} This cannot be undone.`)) return;

  for (const id of note.mediaIds || []) {
    await remove("media", id);
    await opfsDelete(id);
  }
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

/* ------------------------------------------------------------------- media */

/**
 * Attached photos and videos. The model is "immediate": picking files updates
 * the note record and the strip right away, before Save. That is deliberate --
 * the note already exists on disk when the editor opens (createNote persisted
 * it), so there is no draft state to lose, and closing without saving cannot
 * orphan bytes that were never referenced.
 *
 * Storage split: a small media record (id, metadata, thumbnail dataURL) in the
 * IndexedDB "media" store, and the full bytes in OPFS under the same id. The
 * text backup carries only the ids, never the bytes.
 */

/**
 * A small JPEG data URL for the strip. The full-size file is never decoded
 * into the note list -- a photo album's worth of originals would overrun
 * memory -- so the strip shows a bounded thumbnail and the viewer reads the
 * OPFS bytes on demand.
 */
async function makeThumbnail(file) {
  if (!file.type.startsWith("image/")) return "";
  const url = URL.createObjectURL(file);
  try {
    const image = await new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("The image could not be read."));
      img.src = url;
    });
    const max = 320;
    const scale = Math.min(1, max / Math.max(image.naturalWidth || 1, image.naturalHeight || 1));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round((image.naturalWidth || max) * scale));
    canvas.height = Math.max(1, Math.round((image.naturalHeight || max) * scale));
    canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.72);
  } catch (error) {
    // An undecodable image keeps its place in the strip and stays openable in
    // the viewer; a broken thumbnail must not fail the whole attach.
    console.warn("Thumbnail generation failed:", error);
    return "";
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** The media records a note references, in attached order, existing ones only. */
async function loadNoteMedia(note) {
  const records = [];
  for (const id of note.mediaIds || []) {
    const record = await get("media", id);
    if (record) records.push(record);
  }
  return records;
}

/** The tile text for a record with no thumbnail: its type, else its extension. */
function docTileLabel(record) {
  const type = record?.type || "";
  if (type === "application/pdf") return "PDF";
  const name = String(record?.name || "");
  const dot = name.lastIndexOf(".");
  if (dot !== -1 && dot < name.length - 1) return name.slice(dot + 1).toUpperCase();
  if (type.startsWith("image/")) return (type.slice("image/".length) || "IMAGE").toUpperCase();
  return (type.split("/").pop() || "FILE").toUpperCase();
}

function renderMediaStrip(records) {
  if (!els.mediaStrip) return;
  els.mediaStrip.replaceChildren();
  els.mediaStrip.classList.toggle("hidden", records.length === 0);
  for (const record of records) {
    const cell = document.createElement("div");
    cell.className = "media-thumb";
    cell.dataset.media = record.id;
    cell.title = record.name || "Attached media";

    if (record.thumb) {
      const img = document.createElement("img");
      img.src = record.thumb;
      img.alt = record.name || "Attached photo";
      cell.append(img);
    } else if ((record.type || "").startsWith("video/")) {
      // Videos carry no frame thumbnail; a play glyph marks them.
      const tag = document.createElement("span");
      tag.className = "media-video-tag";
      tag.textContent = "▶";
      tag.setAttribute("aria-label", "Video");
      cell.append(tag);
    } else {
      // Documents (and any record the engine could not thumbnail) get a
      // labelled tile -- "PDF" -- rather than a broken img: the bytes are
      // there, the strip just has no picture for them.
      const label = docTileLabel(record);
      const tag = document.createElement("span");
      tag.className = "media-file-tag";
      tag.textContent = label;
      tag.setAttribute("aria-label", label);
      cell.append(tag);
    }

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "media-remove";
    remove.title = "Remove";
    remove.setAttribute("aria-label", "Remove this attachment");
    remove.textContent = "×";
    remove.addEventListener("click", event => {
      event.stopPropagation();
      removeNoteMedia(record.id);
    });
    cell.append(remove);

    cell.addEventListener("click", () => viewMedia(record.id));
    els.mediaStrip.append(cell);
  }
}

/**
 * The attachment count at the top of the open note (v35). Same badge as the
 * rows show, same records as the strip -- so the line the panel swaps in
 * says what it is carrying, and it recounts live when files are attached or
 * removed (addNoteMedia/removeNoteMedia re-run loadEditor's renders).
 */
function renderEditorAttach(records) {
  const line = $("#editor-attach");
  if (!line) return;
  const badge = attachmentBadge(records);
  line.classList.toggle("hidden", !badge);
  if (!badge) {
    line.removeAttribute("title");
    return;
  }
  line.querySelector(".editor-attach-text").textContent = badge.text;
  line.setAttribute("title", badge.title);
}

/** Attach the picked files to the open note, bytes first, then the record. */
async function addNoteMedia(fileList) {
  // Snapshot the FileList before the first await: the input can be reset
  // while the note is being read, and a cleared list would attach nothing.
  const files = [...fileList].filter(file =>
    file && (file.type.startsWith("image/") || file.type.startsWith("video/")
             || file.type === "application/pdf"));
  if (!files.length) return;

  const note = await get("notes", state.selectedItemId);
  if (!note) return;

  const ids = [...(note.mediaIds || [])];
  for (const file of files) {
    const id = newId();
    await opfsPut(id, file);
    await put("media", {
      id,
      noteId: note.id,
      name: file.name || "",
      type: file.type || "",
      size: file.size || 0,
      thumb: await makeThumbnail(file),
      addedAt: nowIso()
    });
    ids.push(id);
  }

  await put("notes", { ...note, mediaIds: ids, updatedAt: nowIso() });
  await renderAll();
}

/** Detach one photo: its record, its bytes, and its id on the note. */
async function removeNoteMedia(mediaId) {
  const note = await get("notes", state.selectedItemId);
  if (!note) return;

  await remove("media", mediaId);
  await opfsDelete(mediaId);
  await put("notes", {
    ...note,
    mediaIds: (note.mediaIds || []).filter(id => id !== mediaId),
    updatedAt: nowIso()
  });
  await renderAll();
}

// The viewer's object URL, held so closing the dialog can revoke it -- an
// unreleased URL per view would leak the decoded bytes for the page's life.
// mediaCurrentId says which attachment the viewer is on, so Save to device
// knows which bytes to hand out.
let mediaObjectUrl = null;
let mediaCurrentId = null;

/** Open the viewer on the stored bytes. Reads OPFS on demand, never before. */
async function viewMedia(mediaId) {
  const record = await get("media", mediaId);
  const file = await opfsGet(mediaId);
  const dialog = $("#media-dialog");
  const img = $("#media-view");
  const video = $("#media-video");
  if (!record || !file || !dialog || !img || !video) return;

  mediaCurrentId = mediaId;
  if (mediaObjectUrl) URL.revokeObjectURL(mediaObjectUrl);
  mediaObjectUrl = URL.createObjectURL(file);

  const isVideo = (record.type || "").startsWith("video/");
  // PDFs (v34) render through the iframe: the <img> branch would decode
  // nothing and show its alt text instead. #media-hint names the Save to
  // device way out for engines that embed PDFs nowhere.
  const isPdf = (record.type || "") === "application/pdf";
  const frame = $("#media-frame");
  const hint = $("#media-hint");
  if (!frame || !hint) return;

  img.classList.toggle("hidden", isVideo || isPdf);
  video.classList.toggle("hidden", !isVideo);
  frame.classList.toggle("hidden", !isPdf);
  hint.classList.toggle("hidden", !isPdf);
  if (isVideo) {
    video.src = mediaObjectUrl;
  } else if (isPdf) {
    frame.src = mediaObjectUrl;
  } else {
    img.src = mediaObjectUrl;
    img.alt = record.name || "Attached photo";
  }
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

function closeMedia() {
  const dialog = $("#media-dialog");
  if (dialog?.open && typeof dialog.close === "function") dialog.close();
  else dialog?.removeAttribute("open");
  const video = $("#media-video");
  if (video && typeof video.pause === "function") video.pause();
  if (mediaObjectUrl) {
    URL.revokeObjectURL(mediaObjectUrl);
    mediaObjectUrl = null;
  }
  mediaCurrentId = null;
  const img = $("#media-view");
  if (img) img.src = "";
  const frame = $("#media-frame");
  if (frame) frame.src = "";
  const hint = $("#media-hint");
  if (hint) hint.classList.add("hidden");
}

/**
 * Hand a copy of the viewed bytes to the browser's own download path: an
 * object URL plus a transient anchor, revoked once the click has landed. This
 * is the sanctioned way media leaves the app (2026-10-02) -- the user saves a
 * photo and uploads it onward themselves; the app never uploads anything.
 * The original file name is kept so the gallery entry is recognisable.
 */
async function saveMediaToDevice() {
  if (!mediaCurrentId) return;
  const record = await get("media", mediaCurrentId);
  const file = await opfsGet(mediaCurrentId);
  if (!record || !file) return;
  const url = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = url;
  link.download = record.name
    || ((record.type || "").startsWith("video/") ? "video"
        : (record.type || "") === "application/pdf" ? "document"
        : "photo");
  document.body.append(link);
  link.click();
  link.remove();
  // Revoking synchronously can cancel the download on some engines; give the
  // browser a beat to start it.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function switchKind(kind) {
  state.activeKind = kind;
  // Switching tabs always leaves the editor: the other tab is a list.
  state.mode = "browse";
  state.selectedItemId = null;
  els.newFolderForm.classList.add("hidden");
  clearRenameState();
  await renderAll();
}

function syncWeekdayVisibility() {
  els.weekdayPicker.classList.toggle("hidden", els.reminderRepeat.value !== "weekly");
}

/* ------------------------------------------------------- settings & backup */

// The exact text the last successful preview saw. A restore is only allowed to
// act on that text: if the textarea changed afterwards, the preview describes a
// file that is no longer there and the button goes inert again.
let previewedText = null;
// Held separately so a copy confirmation can be shown without erasing the
// oversize warning that explains why Export CSV will not open.
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

/**
 * Put a top share on the folder/contents rows (v33: the folder tree sits
 * above the note list). The default 40:60 is the CSS fallback, so resetting
 * the properties would also be correct -- but setting them explicitly keeps
 * the chip and the tracks reading from the same value. Anything outside the
 * offered five reads as the default rather than as junk tracks.
 */
function applyFolderRatio(ratio) {
  const region = $(".browse-notes");
  const tracks = folderRatioToTracks(ratio);
  if (region) {
    region.style.setProperty("--folder-track", tracks.folder);
    region.style.setProperty("--content-track", tracks.content);
  }
  const chip = $("#folder-ratio-value");
  if (chip) {
    chip.textContent = FOLDER_RATIOS.includes(ratio) ? ratio : DEFAULT_FOLDER_RATIO;
  }
}

async function cycleFolderRatio() {
  const next = nextFolderRatio(await getSetting("folderRatio", DEFAULT_FOLDER_RATIO));
  await setSetting("folderRatio", next);
  applyFolderRatio(next);
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
    // worse than one that will not open. The clipboard copy still has it all.
    oversizeNote = `Too large for an email app (${built.encodedLength} characters, limit ${built.limit}) — use Share CSV for backup and paste it into the email.`;
    link.href = "#";
    link.setAttribute("aria-disabled", "true");
    link.classList.add("hidden");
  }
  updateExportNote();
}

/**
 * Rebuild the export view from live data: the CSV itself and the mailto
 * handoff. Called every time the panel opens so the preview the user sees is
 * never staler than the database behind it.
 */
async function refreshExportCsv() {
  const [folders, notes, reminders] = await Promise.all([
    getAll("folders"), getAll("notes"), getAll("reminders")
  ]);
  const csv = buildBackupCsv({ folders, notes, reminders, exportedAt: nowIso() });

  const box = $("#export-csv");
  if (box) box.value = csv;

  syncMailtoLink(csv);
}

async function showExportPanel() {
  showPanel("export");
  // The Connect button's enabled state tracks configuration at open time
  // (a mid-run config change lands on the next open, never mid-flight).
  refreshDriveUi();
  await refreshExportCsv();
  // The user asked (2026-10-03) for the export click itself to back up
  // everything: with a destination configured this button runs it on its own.
  // driveAllowed:false — an auto-run must never pop Google's sign-in window;
  // it backs up to Drive only while the sign-in from an earlier tap still
  // lives, and says "next tap" otherwise.
  await runBackupAll({ driveAllowed: false });
}

/* ---- one-tap backup: independent destinations (sync.js, drive.js) ------- */

let syncBusy = false;

function setSyncStatus(message) {
  const el = $("#sync-status");
  if (el) el.textContent = message;
}

/**
 * Attachment filenames are user data; strip everything that could break a git
 * path or a Windows filename, and keep the id prefix so two photos that share
 * an original name can never overwrite each other in the PC archive.
 */
function syncMediaName(record) {
  const safe = String(record.name || "").replace(/[\\/:*?"<>|#\s]+/g, "-");
  const knownExt = (record.type || "").startsWith("video/") ? ".mp4"
    : (record.type || "").startsWith("image/") ? ".jpg"
    : (record.type || "") === "application/pdf" ? ".pdf" : "";
  const withExt = safe || `media${knownExt}`;
  const ext = withExt.includes(".") ? "" : knownExt;
  return `${record.id}-${withExt}${ext}`;
}

function friendlySyncError(error) {
  if (error?.code === "NO_TOKEN") {
    return "add your backup token below (one-time setup), then tap the button again.";
  }
  if (error?.code === "FILE_TOO_LARGE" || error?.code === "CSV_TOO_LARGE") {
    return `${error.name || "a file"} is over the ${Math.round(SYNC_MAX_FILE_BYTES / (1024 * 1024))} MB per-file cap — use the photo viewer's "Save to device" for it, or trim it.`;
  }
  if (error instanceof TypeError && /ISO-8859-1|RequestInit/.test(error.message || "")) {
    // Not a network problem: a header value (always the token) carries a
    // non-ASCII character, so fetch refused the request before it left.
    return "the saved token contains a stray character that can't be sent "
      + "— tap Remove below, then paste the token again.";
  }
  if (error instanceof TypeError) {
    // fetch rejects this way for any network-level failure: offline, DNS, or a
    // network that blocks api.github.com even though websites load. Say what
    // actually happened -- the raw message is the diagnosis.
    return "couldn't reach github.com's API (" + (error.message || "network error")
      + ") — check that you are online. If websites load but this keeps failing, "
      + "switch between Wi-Fi and mobile data: some networks block GitHub's API.";
  }
  if (error?.code === "TOKEN_SCOPE") {
    return "GitHub refused the branch setup (" + (error.message || "no access")
      + ") — the token's permissions are too narrow. It must allow only ask-ai-relay "
      + "with Contents: Read and write.";
  }
  if (/failed \(401\)/.test(error?.message || "") || /401/.test(error?.message || "")) {
    return "the token was rejected (401) — check it still exists on github.com and paste a fresh one.";
  }
  if (/ref update failed/.test(error?.message || "")) {
    return `the branch moved too many times (${error.message}) — wait a minute, then tap again.`;
  }
  return error?.message || String(error);
}

/* Destination status lines. Every line names its destination first, so a
 * Drive failure can never be mistaken for a relay failure and one
 * destination's outcome is never reported inside the other's sentence. */
const DRIVE_NOT_CONFIGURED =
  "Google Drive: Not configured — the app's Client ID is missing from config.js "
  + "(the site owner sets it once; no visitor pastes anything).";
const RELAY_NOT_CONFIGURED =
  "PC relay: Not configured — add a backup token above (one-time setup) to "
  + "also keep a copy on the PC.";

/**
 * The bundle is built exactly once per tap and handed to every configured
 * destination, so all of them carry the same exportId and the same bytes.
 * The export id lives here (not inside the relay submission) so that the
 * Drive ZIP's name no longer depends on the relay having succeeded.
 */
async function collectBackupBundle() {
  const [folders, notes, reminders] = await Promise.all([
    getAll("folders"), getAll("notes"), getAll("reminders")
  ]);
  const csv = buildBackupCsv({ folders, notes, reminders, exportedAt: nowIso() });
  const records = await getAll("media");
  const media = [];
  const skipped = [];
  for (const record of records) {
    const file = await opfsGet(record.id);
    if (!file) { skipped.push(record.name || record.id); continue; }
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (bytes.length > SYNC_MAX_FILE_BYTES) {
      skipped.push(`${record.name || record.id} (over 24 MB)`);
      continue;
    }
    media.push({ name: syncMediaName(record), bytes, type: record.type || "" });
  }
  return {
    csv,
    media,
    skipped,
    counts: { folders: folders.length, notes: notes.length, reminders: reminders.length },
    exportId: newExportId()
  };
}

/**
 * The PC relay destination (optional legacy since v28): runs only when the
 * user has saved a GitHub backup token, and talks to nothing but GitHub.
 * Its verdict is returned, never thrown, so it cannot mask or block the
 * Google Drive destination.
 */
async function runRelayBackup(bundle) {
  // Reported in the catch too, so a failure still shows the pickup line.
  let prefix = "";
  try {
    // Report the PREVIOUS export's pickup before starting a new one, so the
    // loop "sent -> the PC took it" is visible without any timer.
    const previous = await getSetting("syncLastExportId");
    if (previous) {
      const ack = await checkPickedUp(previous);
      if (ack.pickedUp) {
        prefix = `Previous backup was picked up by the PC${ack.at ? ` at ${ack.at}` : ""}. `;
        await setSetting("syncLastExportId", "");
      } else if (ack.checked === false) {
        prefix = "(Could not check the previous backup's pickup just now.) ";
      } else {
        prefix = "(Previous backup has not been picked up yet — it will be.) ";
      }
    }
    setSyncStatus(`PC relay: ${prefix}sending backup + ${bundle.media.length} attachment(s)…`);
    const result = await syncSubmit({
      csv: bundle.csv,
      media: bundle.media,
      meta: {
        counts: bundle.counts,
        exportId: bundle.exportId,
        appVersion: String(APP_VERSION)
      }
    });
    await setSetting("syncLastExportId", result.exportId);
    const skipNote = bundle.skipped.length
      ? ` Skipped on this device (still safe here): ${bundle.skipped.join(", ")}.` : "";
    return { dest: "relay", state: "success",
      text: `PC relay: ${prefix}Sent — backup + ${result.mediaCount} attachment(s) `
        + `(${Math.round(result.bytes / 1024)} KB) are on the private GitHub branch; `
        + `the PC's optional watcher picks them up when it runs.${skipNote}` };
  } catch (error) {
    return { dest: "relay", state: "failed",
      text: `PC relay: ${prefix}Sync failed: ${friendlySyncError(error)}` };
  }
}

/**
 * One tap = one backup to every configured destination, independently (v28).
 * Google Drive is the primary full backup and needs nothing but a connected
 * Google account (v29: one Client ID in config.js, each user connects their
 * own account) — no GitHub token, no PC, no watcher, no GitHub at all. The PC
 * relay runs only when its own token is saved. Neither destination can block
 * or mask the other: both run side by side, each returns its own verdict,
 * and only a bundle that cannot be collected stops both.
 *
 * driveAllowed:false is the auto-run path (the panel just opened): Google's
 * sign-in window may not open on its own, so the Drive copy then waits for
 * a tap.
 */
async function runBackupAll({ driveAllowed }) {
  if (syncBusy) return;
  const driveOn = hasDriveClientId();
  const relayOn = hasToken();
  if (!driveOn && !relayOn) {
    renderBackupStatus([], { drive: false, relay: false });
    return;
  }
  // Google's consent window must open inside the tap's user-activation
  // window: once the click has crossed an await, browsers refuse the popup.
  if (driveAllowed && driveOn) kickOffDriveToken();
  syncBusy = true;
  const button = $("#sync-now-btn");
  if (button) button.disabled = true;
  try {
    const bundle = await collectBackupBundle();
    const jobs = [];
    if (relayOn) jobs.push(runRelayBackup(bundle));
    if (driveOn) jobs.push(runDriveBackup(bundle, { allowSignIn: Boolean(driveAllowed) }));
    // Both destinations catch their own failures and settle on their own, so
    // Promise.all cannot reject and neither verdict can mask the other's.
    renderBackupStatus(await Promise.all(jobs), { drive: driveOn, relay: relayOn });
  } catch (error) {
    // Collection itself failed (e.g. storage unavailable) — no destination
    // was reached, so both lines say the same plain thing.
    const text = "Backup could not collect the data on this device: "
      + `${error?.message || String(error)}`;
    setDriveStatus(text);
    setSyncStatus(text);
    const overall = $("#backup-overall");
    if (overall) overall.textContent = "Backup failed — nothing reached any destination.";
  } finally {
    syncBusy = false;
    if (button) button.disabled = false;
  }
}

/**
 * Writes each destination's own line and one aggregate verdict. The verdict
 * can only fail when every configured destination failed, and can never
 * blame Google Drive for a PC relay failure or the other way round.
 */
function renderBackupStatus(results, configured) {
  const outcome = { drive: null, relay: null };
  for (const result of results) outcome[result.dest] = result;
  setDriveStatus(outcome.drive ? outcome.drive.text : DRIVE_NOT_CONFIGURED);
  setSyncStatus(outcome.relay ? outcome.relay.text : RELAY_NOT_CONFIGURED);
  const overall = $("#backup-overall");
  if (!overall) return;
  if (!configured.drive && !configured.relay) {
    overall.textContent = "No backup destination is configured. Google Drive "
      + "needs the app's Client ID in config.js (the site owner sets it once); "
      + "the PC relay needs a backup token below (one-time setup).";
    return;
  }
  const failed = results.filter(result => result.state === "failed");
  const succeeded = results.filter(result => result.state === "success").length;
  const destName = dest => (dest === "drive" ? "Google Drive" : "the PC relay");
  if (failed.length && !succeeded) {
    overall.textContent = "Backup failed — "
      + failed.map(result => destName(result.dest)).join(" and ") + " failed.";
  } else if (failed.length) {
    overall.textContent = "Backup completed with a warning — "
      + failed.map(result => destName(result.dest)).join(" and ")
      + " failed; the other destination's backup still succeeded.";
  } else if (succeeded) {
    overall.textContent = "Backup completed.";
  } else {
    overall.textContent = "Nothing was backed up just now — Google Drive "
      + "signs in on your next tap of the Back up button.";
  }
}

/** The sync button: the only path allowed to open Google's consent window. */
function runSync() {
  return runBackupAll({ driveAllowed: true });
}

function saveSyncToken() {
  if (hasToken()) {
    setSyncStatus("A token is already saved. Tap Remove first if you want to replace it.");
    return;
  }
  const input = $("#sync-token");
  const value = (input?.value || "").trim();
  if (!value) {
    setSyncStatus("Paste the token into the box first, then Save.");
    return;
  }
  // The phone failure of 2026-10-04: an invisible stray character in the
  // paste made fetch refuse the request before it left the device. The
  // stored token is sanitised, and the status says what was removed.
  const cleaned = sanitizeToken(value);
  if (!cleaned) {
    setSyncStatus("That paste doesn't contain a token. Copy the whole "
      + "github_pat_… string from github.com and paste it here.");
    return;
  }
  setToken(cleaned);
  const removed = value.length - cleaned.length;
  if (input) {
    input.value = "";
    input.placeholder = "Token saved on this device";
  }
  refreshSyncTokenUi();
  setSyncStatus((removed > 0
    ? `Token saved — ${removed} stray character${removed === 1 ? "" : "s"} removed from the paste. `
    : "Token saved on this device. ")
    + "Tap “Back up notes + photos now”.");
}

function removeSyncToken() {
  clearToken();
  refreshSyncTokenUi();
  const input = $("#sync-token");
  if (input) {
    input.value = "";
    input.placeholder = "GitHub backup token (optional, paste once)";
  }
  setSyncStatus("Token removed from this device.");
}

/* ---- Google Drive destination (primary v28; per-user since v29) ---------- */
/* The connecting user's own Google Drive (drive.js) receives one ZIP per
 * backup — the backup CSV plus every photo/video — into a "Notes Backup"
 * folder. v29 made the destination MULTI-USER: the app ships ONE public
 * Client ID in config.js and every visitor just taps "Connect Google Drive"
 * and picks THEIR OWN account in Google's window. The token that comes back
 * belongs to that account only, so each user's Drive gets its own folder and
 * backups — one user can never see or overwrite another user's. There is no
 * Google ID or password anywhere in this flow; the access token stays IN
 * MEMORY ONLY (never persisted; page reload forgets it by design), and
 * Disconnect clears only this browser's token state — notes, reminders,
 * photos, settings and the relay token are untouched. */

let driveToken = "";
let driveTokenAt = 0;
let driveTokenPromise = null;
// Disconnect bumps this; anything that resolves AFTER a disconnect must not
// resurrect the cleared token — "clear only that browser's state" is exact.
let driveGen = 0;

function setDriveStatus(message) {
  const el = $("#drive-status");
  if (el) el.textContent = message;
}

const hasDriveClientId = () => hasClientId();

// The real token lives about an hour; retiring at 50 minutes keeps the last
// sync of a session from dying mid-upload.
const driveTokenLive = () => Boolean(driveToken)
  && (Date.now() - driveTokenAt) < 50 * 60 * 1000;

/**
 * Called only from the tap path, synchronously, before runBackupAll's first
 * await — browsers only permit the sign-in popup inside the click's
 * user-activation window.
 */
function kickOffDriveToken() {
  if (!hasDriveClientId() || driveTokenPromise || driveTokenLive()) return;
  driveTokenPromise = requestToken(getClientId());
  // The PC sync runs first; until the Drive step consumes this promise, a
  // fast popup-close must not surface as an unhandled rejection.
  driveTokenPromise.catch(() => {});
}

/**
 * The Drive destination (primary since v28). Takes the SAME bundle the relay
 * got — no second collection pass — opens the sign-in only when allowSignIn
 * is true (a real tap), and returns its own verdict without ever touching the
 * relay's status line. On the auto-run path a dead token means "waits", never
 * a popup.
 */
async function runDriveBackup(bundle, { allowSignIn }) {
  const gen = driveGen;
  try {
    // Work on a local token: if the user disconnects while this backup is in
    // flight, the upload still finishes with the token it already had, but
    // the cleared device state is never resurrected afterwards.
    let token = driveTokenLive() ? driveToken : "";
    if (!token) {
      const pending = driveTokenPromise;
      driveTokenPromise = null;
      token = pending ? await pending : null;
      if (!token) {
        if (!allowSignIn) {
          return { dest: "drive", state: "waits",
            text: "Google Drive: not signed in — the sign-in window will open "
              + "on your next tap of the Back up button." };
        }
        const error = new Error("no google sign-in yet");
        error.code = "NOT_SIGNED_IN";
        throw error;
      }
      if (gen === driveGen) { driveToken = token; driveTokenAt = Date.now(); }
    }
    setDriveStatus("Google Drive: copying the same backup…");
    const entries = [{ name: "backup.csv", bytes: utf8(bundle.csv) }].concat(
      bundle.media.map(item => ({ name: `media/${item.name}`, bytes: item.bytes })));
    const zipBytes = buildZip(entries);
    const folderId = await ensureBackupFolder(token);
    const file = await uploadBackup({
      token,
      zipBytes,
      folderId,
      name: `notes-backup-${bundle.exportId}.zip`
    });
    return { dest: "drive", state: "success",
      text: `Google Drive: Copied ${file.name} to your Drive.` };
  } catch (error) {
    // The app never renews the sign-in on its own: on expiry the token is
    // dropped and the next tap reconnects. No popup without a tap, ever.
    if (error?.code === "TOKEN_EXPIRED" && gen === driveGen) driveToken = "";
    if (error?.code === "NOT_SIGNED_IN") {
      return { dest: "drive", state: "failed",
        text: "Google Drive: backup failed — no sign-in came back. Tap the "
          + "Back up button again and complete Google's window." };
    }
    return { dest: "drive", state: "failed",
      text: `Google Drive: backup failed: ${friendlyDriveError(error)}` };
  }
}

/**
 * Connect Google Drive (v29): the ONE button every visitor uses. The app's
 * public Client ID already ships in config.js — nobody pastes anything — so
 * this just runs the sign-in inside this tap and waits for it: Google's own
 * window lets the current user pick their own account, and the token that
 * comes back is that account's alone, so backups land in that user's Drive
 * only (drive.js holds the client id resolution and the token flow).
 */
async function connectDrive() {
  if (!hasDriveClientId()) {
    setDriveStatus(DRIVE_NOT_CONFIGURED);
    return;
  }
  if (driveTokenLive()) {
    setDriveStatus("Google Drive: connected — the Back up button copies "
      + "notes, photos and videos to your Drive.");
    return;
  }
  // Marks this sign-in. Disconnect bumps the counter, so a window that
  // finishes after a disconnect cannot restore the token behind it.
  const gen = ++driveGen;
  kickOffDriveToken();
  const pending = driveTokenPromise;
  // Consumed here; a later sign-in must start a fresh one, not re-await an
  // already-resolved promise (that is how a stale reconnect could beat the
  // 401-clear and report "connected" without Google being asked anything).
  driveTokenPromise = null;
  if (!pending) return;
  setDriveStatus("Google Drive: Google's sign-in window is opening — choose "
    + "your own Google account.");
  try {
    const token = await pending;
    if (gen !== driveGen) return;
    driveToken = token;
    driveTokenAt = Date.now();
    setDriveStatus("Google Drive: connected — tap “Back up notes + photos "
      + "now” to copy everything to your Drive.");
  } catch (error) {
    if (gen !== driveGen) return;
    setDriveStatus(`Google Drive: ${friendlyDriveError(error)}`);
  }
}

/**
 * Disconnect (v29): clears ONLY this browser's Google sign-in state — the
 * memory token, its timestamp, and any sign-in window still opening. It
 * never revokes the grant on Google's side (that would be every device at
 * once) and it touches no local data: notes, reminders, photos, settings
 * and the relay token all stay exactly as they were.
 */
function disconnectDrive() {
  driveGen += 1;
  driveToken = "";
  driveTokenAt = 0;
  driveTokenPromise = null;
  setDriveStatus("Google Drive: disconnected on this device — the notes, "
    + "reminders and photos here are unchanged. To cut the app's access off "
    + "everywhere too, remove it at Google's own permissions page.");
  refreshDriveUi();
}

/* Connect is exactly as usable as the configuration allows: with no Client
 * ID anywhere (config.js empty, no device override) the button renders
 * disabled — a tap on a dead button teaches nothing; the status line is the
 * explanation. States are re-read on every panel open via showExportPanel.
 */
function refreshDriveUi() {
  const connect = $("#drive-connect");
  if (!connect) return;
  const ready = hasDriveClientId();
  connect.disabled = !ready;
  connect.title = ready ? ""
    : "Connect works once the app's Client ID is set in config.js";
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
    '<p class="muted">Restoring deletes everything on this device first. Attached files are not included in a backup. Your settings are kept.</p>'
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
  clearRenameState();

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
  on("#folder-ratio-btn", "click", cycleFolderRatio);
  on("#export-btn", "click", showExportPanel);
  on("#import-btn", "click", showImportPanel);
  on("#copy-csv-btn", "click", copyExportCsv);
  on("#backup-email", "change", saveBackupEmail);
  on("#sync-now-btn", "click", runSync);
  on("#sync-token-save", "click", saveSyncToken);
  on("#sync-token-remove", "click", removeSyncToken);
  on("#drive-connect", "click", connectDrive);
  on("#drive-disconnect", "click", disconnectDrive);
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
  on("#media-input", "change", async event => {
    const input = event.target;
    if (input?.files?.length) await addNoteMedia(input.files);
    // Reset only after the attach finishes, so picking the same file again
    // still fires a change event.
    input.value = "";
  });
  on("#media-close", "click", closeMedia);
  on("#media-save-btn", "click", saveMediaToDevice);
  // Escape (a native dialog "cancel") routes here too: the bytes' URL must be
  // released whichever way the viewer closes.
  on("#media-dialog", "close", closeMedia);
  on("#new-folder-name", "keydown", event => {
    if (event.key === "Escape") {
      els.newFolderName.value = "";
      els.newFolderForm.classList.add("hidden");
    }
  });

  // The visible release number, filled before any await so the dialog can
  // never open with an empty chip -- knowing which revision runs is the
  // whole point of showing it. static_check.py pins the value to sw.js.
  const versionChip = $("#app-version");
  if (versionChip) versionChip.textContent = APP_VERSION;
  // The home screen carries its own chip so the running release is
  // visible without opening Settings.
  const homeChip = $("#home-version");
  if (homeChip) homeChip.textContent = "v" + APP_VERSION;
  refreshSyncTokenUi();
  refreshDriveUi();
}

/* The token field is write-once: while a token is stored the input is read
 * only and Save is disabled, so a stray tap cannot overwrite a working
 * credential. Replacing one is a deliberate act: Remove, then paste.
 * Requested by the user 2026-10-05 after the token was live.
 */
function refreshSyncTokenUi() {
  const input = $("#sync-token");
  if (!input) return;
  const stored = hasToken();
  input.readOnly = stored;
  input.placeholder = stored
    ? "Token saved on this device — Remove to replace"
    : "Backup token (paste once)";
  const save = $("#sync-token-save");
  if (save) {
    save.disabled = stored;
    save.title = stored ? "Remove the token first to paste a new one" : "";
  }
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

  // Device preferences must be in place before the first paint: the pane split,
  // the folder/contents split, and the last address the backup was exported to.
  applyPaneRatio(await getSetting("paneRatio", DEFAULT_RATIO));
  applyFolderRatio(await getSetting("folderRatio", DEFAULT_FOLDER_RATIO));
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
