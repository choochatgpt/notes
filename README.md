# Notes

A local-first HTML5/PWA app for notes and reminders.

**All note data stays in your browser.** There is no backend, no account, and no
upload. The app contains no `fetch`, no `XMLHttpRequest` and no `sendBeacon` —
nothing you type can leave the device.

## Use it

Open <https://choochatgpt.github.io/notes/> and use your browser's
*Add to Home Screen*. It installs as an app and works offline.

## How the screen is laid out

```
┌──────────────────────────────────────────────┐
│ Notes   [New folder] [New note] [New reminder] [Backup]
├──────────────────────────────────────────────┤
│ (Notes) (Reminders)          Work / Projects │  <- upper half:
│ ┌──────────┬───────────────────────────────┐ │     browse or edit
│ │ Folders  │ Notes in the selected folder  │ │
│ │  Work 12 │ Apollo kickoff       3d ago   │ │
│ │  Home  4 │ Budget draft         1w ago   │ │
│ └──────────┴───────────────────────────────┘ │
├──────────────────────────────────────────────┤
│ Upcoming reminders            7 · soonest first
│  🕐 Standup        in 2 hours   Mon 29 Sep 15:00
│  🕐 Passport       in 3 years   Fri 2 Oct 2027  │  <- lower half:
│                                              │     always visible
└──────────────────────────────────────────────┘
```

**The top bar is one row that never wraps.** On a narrow screen the button
labels collapse and the icons carry the action.

**The upper half browses, then edits in place.** Tapping a note (or a reminder)
swaps that half into its editor with a back arrow. The reminder agenda below
never scrolls away, so a reminder coming due stays in view while you type.

**Two tabs in the upper half.** *Notes* shows the folder tree beside the note
list. *Reminders* is where reminders are managed — created, edited and deleted.
The agenda below is read-only, which keeps exactly one place able to change a
reminder.

## How the data is organised

**Notes are hierarchical.** Folders nest to any depth and belong to notes only:

```
Work
  HR
  IT
  Projects
    Apollo
    Borealis
Home
  Friends
  Relatives
```

Branches expand and collapse, each folder shows how many notes are directly in
it, and a breadcrumb chip shows the path (`Work / Projects / Apollo`). Notes
filed at no folder live under **Unfiled**.

**Reminders are a flat list.** They are deliberately not filed into folders. Both
the agenda and the Reminders tab are ordered by the soonest upcoming reminder,
and each row shows both a relative due ("in 3 days") and the absolute time. A
one-off whose moment has passed has no future occurrence, so it sorts to the
bottom and is labelled **Past** rather than jumping to the top. The agenda shows
only what is still upcoming, and says in a footer how many past reminders it is
holding back.

Recurrence covers once / every N days / weekly on chosen weekdays / every N
months / every N years — which is every schedule originally asked for: Tue + Thu
at 15:00, a yearly birthday, and every 5 years for passport renewal.

## Deleting

Deleting a note or a reminder asks for confirmation naming the item.

Deleting a **folder** deletes the folder, its subfolders and every note inside
them — the whole subtree, in one action. The confirmation counts what is about to
go before it goes:

```
Delete "Work"? This also deletes 3 subfolders and 12 notes. This cannot be undone.
```

There is no undo and no trash, so the counts are stated first rather than
discovered afterwards.

## Not built yet

- Photo/video attachment bytes (the button is a stub).
- Undo or a trash for a deleted folder.
- Backup, export and restore.
- Email backup transport.
- Notification delivery, and marking a reminder as done.

A static PWA cannot guarantee an alarm that fires while the app is closed, and it
cannot send email silently without credentials embedded in a public page.

## Run it locally

```sh
python -m http.server 8080
# then open http://localhost:8080/
```

`index.html` must be served over HTTP. Opening it as a `file://` page gives a
blank screen, because browsers block ES module loading from `file://` origins.

Browser storage is bound to the exact origin, so `http://localhost:8080` and the
published `https://choochatgpt.github.io/notes/` hold **separate** databases.
Notes entered in one do not appear in the other.

## Layout

| File | Role |
|---|---|
| `index.html` | Markup, plus the SVG sprite every icon comes from |
| `app.css` | All styling. One palette defined twice: light and dark |
| `app.js` | State, rendering, and event wiring |
| `view.js` | Pure formatting and ordering helpers — no DOM, so Node can test it |
| `storage.js` | IndexedDB wrapper (`notes-local`, v1) |
| `reminder.js` | Recurrence engine and `nextDueAt` calculation |
| `sw.js` | Offline cache |

## Tests

```sh
node tests/recurrence.test.mjs ../reminder.js   # 23 tests
node tests/view.test.mjs ../view.js             # 64 tests
```

`recurrence.test.mjs` covers every rule family, the Feb-29 leap-year case, and
rule normalisation. `view.test.mjs` covers reminder ordering (including that a
spent one-off sorts last rather than as an epoch date), recurrence labels, the
relative-time buckets, folder paths, HTML escaping, subtree collection for a
recursive folder delete (including that a parent cycle terminates), and the
wording of the delete confirmation.

Both take the module path as an argument and default to the `../source/` layout.

## Editing

**Bump `CACHE_NAME` in `sw.js` on every release.** The service worker is
network-first for page loads and deletes old caches on activate, so a new version
reaches an installed device as soon as it is online — but only if the cache name
changes.

Icons are generated, not hand-drawn:

```sh
python tools/make_icons.py    # requires Pillow
```

## Deploying

Pushing to `main` publishes. GitHub Pages serves the repo root of `main`; there
is no build step and no CI.
