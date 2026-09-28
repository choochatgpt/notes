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
