// Bump CACHE_NAME on every release. The activate handler deletes any cache that
// is not the current name, so a new version cannot be masked by an old one.
const CACHE_NAME = "notes-shell-v12";

const APP_SHELL = [
  "./",
  "./index.html",
  "./app.css",
  "./app.js",
  "./view.js",
  "./storage.js",
  "./reminder.js",
  "./backup.js",
  "./manifest.webmanifest",
  "./icon-192.png",
  "./icon-512.png",
  "./apple-touch-icon.png"
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

// Markup, scripts, styles and the worker itself: everything that has to agree on
// a version. Serving these cache-first let a new index.html run against a stale
// app.js, and the mismatch threw during startup, which left every button on the
// page doing nothing. They are network-first now, with the cache as the offline
// fallback. Images are not in this set -- they are effectively immutable.
const VERSIONED_DESTINATIONS = new Set([
  "document", "script", "style", "worker", "sharedworker", "manifest"
]);

/** Fresh if the network answers; the cached copy only when it does not. */
async function networkFirst(request) {
  let response;
  try {
    response = await fetch(request);
  } catch (error) {
    const cached = await caches.match(request, { ignoreSearch: true })
      || (request.mode === "navigate"
        ? await caches.match("./index.html", { ignoreSearch: true })
        : null);
    if (cached) return cached;
    throw error;
  }

  // Fire-and-forget: a failed cache write must not turn a good response into a
  // network error.
  if (response && response.ok) {
    const copy = response.clone();
    caches.open(CACHE_NAME).then(cache => cache.put(request, copy)).catch(() => {});
  }
  return response;
}

/** Serve from cache, refresh in the background. For assets that never change. */
function cacheFirst(request) {
  return caches.match(request).then(hit => {
    const network = fetch(request)
      .then(response => {
        if (response && response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, copy)).catch(() => {});
        }
        return response;
      })
      .catch(() => hit);
    return hit || network;
  });
}

self.addEventListener("fetch", event => {
  const request = event.request;
  if (request.method !== "GET") return;
  if (new URL(request.url).origin !== self.location.origin) return;

  const versioned = request.mode === "navigate"
    || VERSIONED_DESTINATIONS.has(request.destination);

  event.respondWith(versioned ? networkFirst(request) : cacheFirst(request));
});
