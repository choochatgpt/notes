const DB_NAME = "notes-local";
const DB_VERSION = 1;

let dbPromise;

function upgrade(db) {
  if (!db.objectStoreNames.contains("folders")) {
    const store = db.createObjectStore("folders", { keyPath: "id" });
    store.createIndex("parentId", "parentId", { unique: false });
    store.createIndex("updatedAt", "updatedAt", { unique: false });
  }

  if (!db.objectStoreNames.contains("notes")) {
    const store = db.createObjectStore("notes", { keyPath: "id" });
    store.createIndex("folderId", "folderId", { unique: false });
    store.createIndex("updatedAt", "updatedAt", { unique: false });
  }

  if (!db.objectStoreNames.contains("reminders")) {
    const store = db.createObjectStore("reminders", { keyPath: "id" });
    store.createIndex("folderId", "folderId", { unique: false });
    store.createIndex("nextDueAt", "nextDueAt", { unique: false });
  }

  if (!db.objectStoreNames.contains("media")) {
    const store = db.createObjectStore("media", { keyPath: "id" });
    store.createIndex("noteId", "noteId", { unique: false });
  }

  if (!db.objectStoreNames.contains("settings")) {
    db.createObjectStore("settings", { keyPath: "key" });
  }
}

export function openDatabase() {
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => upgrade(request.result);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error("Database upgrade blocked by another open tab."));
    });
  }
  return dbPromise;
}

function requestPromise(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function transaction(storeName, mode, work) {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, mode);
    const store = tx.objectStore(storeName);
    let result;

    try {
      result = work(store, tx);
    } catch (error) {
      tx.abort();
      reject(error);
      return;
    }

    tx.oncomplete = async () => {
      try {
        resolve(await result);
      } catch (error) {
        reject(error);
      }
    };
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("IndexedDB transaction aborted."));
  });
}

export async function put(storeName, value) {
  return transaction(storeName, "readwrite", store => requestPromise(store.put(value)));
}

export async function get(storeName, key) {
  return transaction(storeName, "readonly", store => requestPromise(store.get(key)));
}

export async function getAll(storeName) {
  return transaction(storeName, "readonly", store => requestPromise(store.getAll()));
}

export async function getAllByIndex(storeName, indexName, value) {
  return transaction(storeName, "readonly", store => requestPromise(store.index(indexName).getAll(value)));
}

export async function remove(storeName, key) {
  return transaction(storeName, "readwrite", store => requestPromise(store.delete(key)));
}

export async function setSetting(key, value) {
  return put("settings", { key, value, updatedAt: new Date().toISOString() });
}

export async function getSetting(key, fallback = null) {
  const row = await get("settings", key);
  return row ? row.value : fallback;
}

export function newId() {
  return crypto.randomUUID();
}

/**
 * Ask the browser not to evict this origin's data under storage pressure. Its
 * only caller used to be the status bar's usage line, which is gone, but the
 * request itself stays: it is what stops a browser quietly discarding the
 * notes, and it has nothing to do with whether we display a quota.
 */
export async function requestPersistentStorage() {
  if (!navigator.storage?.persist) {
    return { supported: false, persisted: false };
  }
  const persisted = await navigator.storage.persist();
  return { supported: true, persisted };
}

/**
 * The full-size bytes of an attached photo or video live in OPFS, keyed by the
 * media record's id. IndexedDB holds only the small thumbnail and the
 * metadata; keeping the two apart means listing a note's photos never reads
 * multi-megabyte files, and a restore can never destroy either store (replaceAll
 * touches neither "media" nor OPFS). Everything here degrades to a clean no when
 * the browser lacks the API -- photos are a Chrome/Android-first feature and
 * every caller treats a null read as "not there".
 */
async function mediaDir() {
  const root = await navigator.storage.getDirectory();
  return root.getDirectoryHandle("media", { create: true });
}

/** Store one attachment's bytes under its media id. */
export async function opfsPut(id, file) {
  const dir = await mediaDir();
  const handle = await dir.getFileHandle(id, { create: true });
  const writable = await handle.createWritable();
  try {
    await writable.write(file);
    await writable.close();
  } catch (error) {
    // A half-written file is worse than none: drop it so a failed add cannot
    // leave bytes the note does not reference.
    try { await dir.removeEntry(id); } catch (_) { /* nothing to clean up */ }
    throw error;
  }
}

/** The stored File for an id, or null when it is missing or unreadable. */
export async function opfsGet(id) {
  try {
    const dir = await mediaDir();
    const handle = await dir.getFileHandle(id);
    return await handle.getFile();
  } catch (error) {
    if (error && (error.name === "NotFoundError" || error.name === "TypeError")) return null;
    throw error;
  }
}

/** Delete one attachment's bytes. A missing entry is already the goal state. */
export async function opfsDelete(id) {
  try {
    const dir = await mediaDir();
    await dir.removeEntry(id);
  } catch (error) {
    if (error && error.name !== "NotFoundError") throw error;
  }
}

/**
 * Replace the folder, note and reminder stores in ONE transaction: either the
 * whole backup lands or nothing does. A half-applied restore -- new notes with
 * the old folders still present, or the reverse -- would leave dangling
 * folderId references that the UI papers over silently.
 *
 * `settings` and `media` are deliberately absent from the store list. Settings
 * (the pane ratio, the last-used export address) are the user's device
 * preferences, not note data, and a restore must never touch them; media bytes
 * are not part of the text backup at all.
 */
export async function replaceAll({ folders = [], notes = [], reminders = [] }) {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(["folders", "notes", "reminders"], "readwrite");
    try {
      // Clear first, then put: requests run in the order they are issued, so
      // each store's clear always precedes its writes within this transaction.
      for (const name of ["folders", "notes", "reminders"]) {
        tx.objectStore(name).clear();
      }
      for (const row of folders) tx.objectStore("folders").put(row);
      for (const row of notes) tx.objectStore("notes").put(row);
      for (const row of reminders) tx.objectStore("reminders").put(row);
    } catch (error) {
      tx.abort();
      reject(error);
      return;
    }
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("IndexedDB transaction aborted."));
  });
}
