# Notes

A local-first HTML5/PWA app for hierarchical notes and reminders.

**All note data stays in your browser.** There is no backend, no account, and no
upload. Nothing you type is ever sent anywhere or committed to this repository.

## Use it

Open <https://choochatgpt.github.io/notes/> and use your browser's
*Add to Home Screen*. It installs as an app and works offline.

## Features

- Notes with text, organised in named folders and nested subfolders.
- Reminders with real recurrence: every Tue + Thu at 15:00, yearly birthdays,
  every N years (e.g. passport renewal), daily, monthly, one-off.
- Persistent-storage request and quota display.

## Not built yet

- Photo/video attachment bytes.
- Backup, export and restore.
- Email backup transport.
- Notifications, and a due/overdue view.

A static PWA cannot guarantee an alarm that fires while the app is closed, and it
cannot send email silently without credentials embedded in a public page.

## Run it locally

```sh
python -m http.server 8080
# then open http://localhost:8080/
```

`index.html` must be served over HTTP. Opening it as a `file://` page gives a blank
screen, because browsers block ES module loading from `file://` origins.

Browser storage is bound to the exact origin, so `http://localhost:8080` and the
published `https://choochatgpt.github.io/notes/` hold **separate** databases.

## Tests

```sh
node tests/recurrence.test.mjs ../reminder.js
```

Covers once / daily / weekly+weekdays / monthly / yearly recurrence, including the
Feb-29 leap-year case and rule normalisation.

## Editing

`sw.js` caches the app shell. **Bump `CACHE_NAME` when you change anything**, or
devices will keep serving the previous version from cache.
