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
│ Notes  [New folder] [New note] [New reminder] [Backup]
├──────────────────────────────────────────────┤
│ (Notes) (Reminders)          Work / Projects │  <- upper half:
│ ┌───────────┬──────────────────────────────┐ │     browse or edit
│ │ Folders   │ Notes in the selected folder │ │
│ │  Work  12 │ Apollo kickoff      3d ago   │ │
│ │  Home   4 │ Budget draft        1w ago   │ │
│ └───────────┴──────────────────────────────┘ │
├──────────────────────────────────────────────┤
│ Upcoming reminders         7 · soonest first  │  <- lower half:
│  🕐 Standup   [Weekly·Tue,Thu]  in 2h · Mon 29 Sep 15:00
│  🕐 Passport  [Every 5 years]   in 3y · Fri 2 Oct 2027
└──────────────────────────────────────────────┘
```

**The split is half and half — the notes region above, the agenda below.** The
agenda is never scrolled off: a reminder coming due stays in view while you edit
a note above it. It is an even split rather than a ratio because the agenda is
where a reminder actually gets read, and a third of the screen cut the list short
while the notes region had room to spare.

**Every agenda row is exactly one line**, reading left to right as clock, title,
how often it repeats, then when it is next due:

```
🕐  Standup   [Weekly · Tue, Thu]   in 2 hours · Mon 29 Sep 15:00
```

The title is the only part that flexes; it ellipsises rather than pushing the
recurrence or the due time off the row. On a narrow screen the absolute time is
dropped so the row stays one line — "in 2 hours" is the part that decides whether
you act now, and the exact timestamp is a tap away in the reminder's editor.

The recurrence label always names **both the period and the frequency**:
`Weekly · Tue, Thu`, not just `Tue, Thu`. The weekday list alone says which days
but leaves you to infer whether it repeats weekly or fortnightly, which is the
inference the label exists to remove. A one-off reads `Once`; a spent one-off
reads `Past` and carries no due time, because it does not have one.

**Inside the notes half, the folders and the note list are half each.** The
folder column carries the names and the delete control, so it gets an equal
share rather than whatever is left over.

**The chrome is deliberately thin.** Small gaps, small padding, compact buttons —
every pixel spent on margin is a folder name that gets cut off.

**The top bar is one row that never wraps.** On a narrow screen the button
labels collapse and the icons carry the action.

**The upper half browses, then edits in place.** Tapping a note (or a reminder)
swaps that area into its editor with a back arrow.

**Two tabs in the upper area.** *Notes* shows the folder tree beside the note
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
and each row is one line: title, how it repeats, then a relative due ("in 3 days")
and the absolute time. A one-off whose moment has passed has no future
occurrence, so it sorts to the bottom and is labelled **Past** rather than jumping
to the top. The agenda shows only what is still upcoming, and says in a footer how
many past reminders it is holding back.

**The bottom of the screen is empty unless something has failed.** There used to be
a status bar carrying two lines of small print — local storage usage, and the next
reminder. Both are gone: the agenda directly above already says what is coming up,
and the storage figure answered a question nobody was asking. The bar itself stays
in the markup, hidden, because it is the only thing that makes a startup failure
visible — an app that cannot start looks exactly like one whose every button is
broken, which is an outage this project has already had. So it stays, taking no
space, and any failure brings it back tinted red. Two checks hold both halves of
that: it occupies nothing at rest, and forcing a failure brings it back.

Recurrence covers once / every N days / weekly on chosen weekdays / every N
months / every N years — which is every schedule originally asked for: Tue + Thu
at 15:00, a yearly birthday, and every 5 years for passport renewal.

## Moving a note

Open a note and change its **Folder** field, then save. The picker lists every
folder, indented to show nesting, with *Unfiled* at the top for no folder at all.
A note is created in whichever folder you were browsing, and this is the only way
it changes folder afterwards.

The context chip above the editor shows where the note lives, so it updates the
moment a move is saved. If you move a note out of the folder you are browsing,
it leaves that list — which is what the chip is telling you.

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

The delete control sits on every folder row **at rest** — always on screen, never
revealed only on hover. It is quiet grey against the row and turns red on hover
or focus. It used to appear only on hover, which made it impossible to find on a
desktop and impossible to reach at all on a touch screen, where there is no
hover. Two checks now hold that open, one static and one in the browser, because
a control that exists but cannot be found is a control that does not work.

## Not built yet

**Attachments do not exist.** The *Add photo/video* button is a stub: pressing it
shows a placeholder message and stores nothing. PDFs are not accepted at all, and
no file of any kind is saved today. Text is the only thing a note can hold — which
does cover a label like "2025 medical reports" (that is just the note's title or
body), but not the report itself.

- Photo/video/PDF attachment bytes (the button is a stub).
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
node tests/view.test.mjs ../view.js             # 90 tests
python tools/static_check.py                    # wiring and structural invariants
python tools/browser_check.py                   # 34 checks in real Chrome
```

`recurrence.test.mjs` covers every rule family, the Feb-29 leap-year case, and
rule normalisation. `view.test.mjs` covers reminder ordering (including that a
spent one-off sorts last rather than as an epoch date), recurrence labels, the
relative-time buckets, folder paths, HTML escaping, subtree collection for a
recursive folder delete (including that a parent cycle terminates), the wording
of the delete confirmation, that every recurrence label names its period as well as its frequency, and the folder picker's contents — tree order,
indent depth, and that a folder whose parent is missing is still offered, since
a folder the picker cannot name is one no note can be moved out of.

Both take the module path as an argument and default to the `../source/` layout.

`tools/static_check.py` proves names line up: every icon reference resolves,
every `$("#id")` has an element, no emitted class is unstyled, the service worker
caches every imported module. It also asserts the structural rules this project
has already broken once — controls wired before the first `await`, a guarded
`on()` rather than raw `addEventListener`, and code served network-first.

`tools/browser_check.py` is the only check that runs the app for real. It serves
the app, opens it in headless Chrome, clicks every control and inspects the
resulting DOM — including moving a note and confirming it left the folder it was
in (not just that it arrived in the new one), and deleting a folder: that the
control is on screen without hovering, that cancelling the confirmation keeps the
folder, that the confirmation names the folder and counts the notes inside it,
and that confirming removes both. It also measures the two panes' rendered
heights, because equal rows in the source do not prove equal panes on screen —
a `min-height` on either one breaks the split without touching the rule. Static
checks can prove an id exists and a listener is attached in the source; they
cannot prove a click *does anything*, which is the failure this project actually
hit — see "Releasing" below.

It stubs `alert()` and `confirm()` inside the frame — a real modal blocks headless
Chrome forever — but answers `confirm()` from a variable, so the destructive path
can be cancelled and then taken, and the wording it showed can be inspected.
Every run wipes the browser profile first: IndexedDB lives in the profile, and a
leftover one would carry notes into the next run and make every count meaningless.

```sh
python tools/browser_check.py --compare-stale <commit>
```

rebuilds the broken pairing (current markup + that revision's `app.js`) and runs
both, to confirm the harness still detects the fault it was written for. A check
that can no longer fail is not a check.

```sh
python tools/browser_check.py --url https://choochatgpt.github.io/notes/
```

drives the deployment itself, which is the only way to test what a device
actually downloads. Confirming the published bytes match the local copy proves
the upload landed; it does not prove the deployed app works.

## Releasing

**Bump `CACHE_NAME` in `sw.js` on every release.** The service worker deletes old
caches on activate, so a new version reaches an installed device as soon as it is
online — but only if the cache name changes.

**Markup and script must come from the same release.** They are separate
requests, so they can be answered by different versions, and the app is written
by hand with no build step to keep them in step. That went wrong once: a new
`index.html` ran against a still-cached `app.js` that queried two ids the new
markup had dropped. The mismatch threw during startup, and because the controls
were wired *after* the first render, every button on the page went dead with
nothing on screen explaining why.

Three things now hold that shut, and they are worth keeping:

- **The service worker serves markup, scripts and styles network-first**, with
  the cache only as the offline fallback. Images stay cache-first; they do not
  change. Serving code cache-first is what allowed the mismatch.
- **`wireControls()` runs before the first `await` in `init()`.** A failure in
  the data layer must leave the app degraded, never inert.
- **Every handler goes through `on()`**, which tolerates a missing element and
  reports a rejected handler instead of letting it vanish.

**Before pushing, run the browser check.** A green static check does not mean the
buttons work.

**And drive the deployment, not just the upload.** Byte-identity between the live
site and the working copy proves the publish landed; it does not prove the app
runs. `--url` checks the real thing.

Icons are generated, not hand-drawn:

```sh
python tools/make_icons.py    # requires Pillow
```

## Deploying

Pushing to `main` publishes. GitHub Pages serves the repo root of `main`; there
is no build step and no CI.
