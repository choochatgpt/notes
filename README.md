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
│ Notes  [New folder] [New note] [New reminder] [Settings]
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

**The split is half and half by default — the notes region above, the agenda
below.** The agenda is never scrolled off: a reminder coming due stays in view
while you edit a note above it. The even split is the default because the agenda
is where a reminder actually gets read, and a third of the screen cut the list
short while the notes region had room to spare. It is a default rather than a
rule: Settings → *Notes/Reminders panel display ratio* cycles the top pane
through 20%, 40%, 60% and 80% of the screen and back around, and remembers the
choice on the device. If the stored choice cannot be read, the app falls back to
the even split — never to a broken layout.

**Every agenda row is exactly one line**, reading left to right as clock, title,
how often it repeats, then when it is next due:

```
🕐  Standup   [Weekly · Tue, Thu]   in 2 hours · Mon 29 Sep 15:00
```

The title is the only part that flexes; it ellipsises rather than pushing the
recurrence or the due time off the row. On a narrow screen the **date is the part
that stays**: the row drops the relative "in 2 hours" and keeps the absolute
date, because that is the part that was asked for — one line, date visible —
whereas the desktop row shows both.

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

Deleting a note or a reminder asks for confirmation naming the item. Deleting a
note that carries photos names them too — its attached photos are removed with
it, bytes and all:

```
Delete "Receipts"? Its 2 attached photos will be removed too. This cannot be undone.
```

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

## Settings: the ratio, export, import

The **Settings** button in the top bar opens one dialog with three controls —
and the release number at the bottom ("Version 19"), so on any device you can
see which revision is running. The number is not free-floating decoration:
`tools/static_check.py` pins it to `sw.js`'s cache name (`notes-shell-v19`) and
fails the build if the two drift, and the browser check compares what the
dialog shows against the version this checkout carries (and, on the deployed
site, against the live `sw.js` bytes). Bump `APP_VERSION` in `view.js` and
`CACHE_NAME` in `sw.js` together, every release.

**Notes/Reminders panel display ratio.** Each click moves the split to the next
top-pane share — `20% → 40% → 60% → 80%` and back around, so the notes region
claims a fifth, two fifths, three fifths, or four fifths of the screen. The
choice is stored on the device and applied again on the next launch (the browser
check proves this by reloading the app and re-measuring the panes). A fresh
device starts at the even split (50%), which is the value the chip shows until
the first click — the four shares are the whole offer, and 50% is the fallback
an unreadable stored value degrades to.

**Export notes/reminders to email.** Enter your own address, and the app builds
the whole database — nested folders with their parenting, notes, and reminders —
as one text CSV under a versioned `# notes-backup v2` header, then offers it
exactly two ways:

- **Share CSV for backup** puts the exact text on the clipboard (with the
  selected text as a fallback when the browser blocks programmatic copying).
  It is named for what the copy is *for* — the paste into the relay inbox or
  an email is the share. This is the phone → relay-inbox path's first step.
- **Export CSV** opens your mail app with the CSV in the message body, addressed
  to the address you entered. `mailto:` cannot attach files, and long bodies are
  silently truncated by some mail clients, so a backup that would exceed a
  conservative 1800-character limit is refused outright rather than cut short —
  the panel says so and points at Share CSV for backup instead. The app never
  sends anything itself; you press Send.

*Download .csv* and *Share .csv* were removed on request (2026-10-01): a saved
file still needed a manual attach step, and the phone's share sheet had already
refused a send in the wild — copy + mailto covered every route the two of them
served. The app now has no `navigator.share` anywhere; the static check bans it
outright.

The address is remembered in the same local database as your notes — it never
leaves the device either. The backup is **text only**: photos ride along as an
id list (`mediaIds`) and never as bytes. On the same device a restore therefore
reattaches the pictures; on a new device the notes come back without them. A
v1 backup (no mediaIds column) still restores. Reminder due dates are
deliberately left out — they are recomputed from the start date and the rule on
restore, so a due date can never be imported stale from another machine.

**Emailing a backup from the PC.** `tools/email_backup.py` is the PC half: it
SMTPs a downloaded CSV as a real attachment, so the mailbox credentials live on
your machine in `%APPDATA%\notes-email\config.json` or in environment variables
(`NOTES_SMTP_HOST`, `NOTES_SMTP_PASS`, …) — never in this repository, never in
the app:

```sh
python tools/email_backup.py notes-backup-2026-09-30.csv --to you@example.com
python tools/email_backup.py --watch "C:\Users\you\Downloads"  # auto-send each new backup
python tools/email_backup.py --list-config                     # what is set; passwords never shown
```

`--watch` polls for `notes-backup-*.csv`, sends each one, and moves it to
`sent/` so it cannot go out twice; `--dry-run` builds the message without
sending and needs no configuration. It refuses files that do not carry the
`# notes-backup` header (override with `--force`).

**Phone → PC via the relay inbox.** When the phone's share sheet refuses the
file (`NotAllowedError` — Chrome on Android on a Honor Magic V5 did exactly
this in the wild, in more than one browser), the backup still moves by
copy-paste through the **private** relay repo — never the public Pages repo,
and text backups only:

1. Phone: Settings → **Share CSV for backup**.
2. Phone: github.com/choochatgpt/ask-ai-relay → `gitway/transfer_inbox/notes/`
   → `inbox.csv` → Edit (pencil) → select all → paste → Commit changes.
3. PC: `python tools/relay_pull_backup.py`.

`tools/relay_pull_backup.py` fast-forwards the local clone, reads the inbox
blob out of git (exact repo bytes, immune to autocrlf rewriting), refuses the
placeholder or anything that lacks the `# notes-backup` header or exceeds the
size cap, copies it into the GitWay transfer inbox with a SHA256 manifest,
emails it through `email_backup.py`, and on send success resets the inbox to
its placeholder and pushes. A failed send leaves the repo untouched — the
payload waits for a fixed config rather than being lost to one.

**The pickup runs itself.** `tools/relay_backup_watch.py` loops in the
background (registered as a Windows scheduled task, "Notes backup watch", at
sign-in): every 5 minutes it fetches the relay clone once and serves both
inboxes — a pasted backup CSV goes to
`relay_pull_backup.py --archive-dir C:\notes_backups` (dated copy + email),
and photos waiting in the media inbox go to `relay_pull_media.py` (archive +
clear). So a paste on the phone
lands on the PC as a dated copy in `C:\notes_backups` *and* an emailed
attachment, and a photo upload lands in `C:\notes_backups\media` — with no
PC-side step to remember. Failures are logged to
`C:\notes_backups\relay_backup_watch.log` and retried on a later cycle; the
repo is only ever touched by the pickup tools' own success paths. `--once`
runs a single cycle for testing.

**Import notes/reminders.** Paste the CSV from your email backup into the box and
press **Preview import**. The preview states what the backup holds against what
this device currently holds, plus every repair the parser had to make — a note
pointing at a folder the file does not contain is filed to Unfiled and *listed*,
never silently dropped. Garbage, truncated files, wrong versions and malformed
rows are refused before anything is touched, and leave Restore disabled.

**Restore (overwrite all)** is enabled only for the exact text that was
previewed — editing the box after previewing revokes it — and then asks once
more with the counts stated before anything runs:

```
Replace everything? This permanently deletes all 2 folders, 2 notes, 1 reminder
on this device and restores 2 folders, 1 note, 1 reminder from the backup
(exported 2026-09-29T03:09:36.905Z). Photos are not included in a backup.
Your settings are kept. This cannot be undone.
```

The replace is a single database transaction: it lands completely or not at all —
a cancelled confirmation, a re-parse failure or a mid-write error leaves the data
exactly as it was. **"Your settings are kept" is structural, not a promise**: the
restore writes only folders, notes and reminders, so neither the pane ratio nor
the saved email address can be changed by an import — and any photos that exist
later would sit in a store the restore does not touch.

## Photos on notes

Open a note, press **Add photo/video**, and pick images or videos. The
thumbnails appear on a strip above Delete / Add photo/video / Save, the change
is applied immediately (no need to press Save first, and nothing is orphaned if
you close without saving), and tapping a thumbnail opens it full-size in a
viewer. The **×** on a thumbnail removes that one photo. The viewer's **Save to
device** button writes a copy of the full-size bytes into the phone's
downloads/gallery under the original file name — the one way photos leave the
app, built for the backup route below.

The storage is split so that neither half is heavier than it needs to be:

- A small record per attachment — id, file name, type, size and a bounded
  JPEG thumbnail — in the IndexedDB `media` store.
- The full-size bytes in the browser's **OPFS** (`media/` directory, keyed by
  the same id), read only when the viewer opens.

Everything is origin-scoped device storage: photos stay on the device unless
you deliberately save one out, and
the text backup carries only the id list. `replaceAll` — the restore's one
transaction — touches neither the media store nor OPFS, so a restore can never
destroy a photo; a same-device restore reattaches them via the ids, and a
different device simply shows notes without them. Deleting a note deletes its
photos (the confirmation names the count first), and the browser check proves
the bytes actually leave OPFS when they should.

**Backing photos up (the GitWay media route, chosen 2026-10-02).** The
authorised route for photo bytes is the **private** relay repo — never the
public Pages repo. Three steps, and the last leg is automatic:

1. Phone: open the note → tap the photo → **Save to device**.
2. Phone: github.com/choochatgpt/ask-ai-relay → `gitway/transfer_inbox/notes/media/`
   → **Add file → Upload files** → pick the saved photo(s) → Commit changes.
3. PC: nothing — `tools/relay_pull_media.py` (run automatically by the watcher)
   archives each upload into `C:\notes_backups\media\<transfer id>\` with a
   SHA256 manifest, skips exact duplicates (already-archived bytes are
   recognised and just cleared), copies each file into the GitWay transfer
   inbox with its manifest, then removes the uploaded files from the repo in
   one commit. A failure leaves the file in the repo and retries next cycle.

Rules: private repo only; per-file cap 24 MB (GitHub's web upload refuses
bigger anyway); the text backup CSV stays text-only and keeps its own inbox.

The editor's three buttons — Delete, Add photo/video, Save — are pinned to one
line at every width: the row never wraps, and the labels compact down instead.

## Not built yet

- PDFs and other non-image/video files (the picker accepts images and videos only).
- Undo or a trash for a deleted folder.
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
| `view.js` | Pure formatting, ordering and pane-ratio helpers — no DOM, so Node can test it |
| `backup.js` | Pure CSV backup serializer/parser and confirmation wording — no DOM, so Node can test it |
| `storage.js` | IndexedDB wrapper (`notes-local`, v1) + the OPFS byte store for photos |
| `reminder.js` | Recurrence engine and `nextDueAt` calculation |
| `sw.js` | Offline cache |

## Tests

```sh
node tests/recurrence.test.mjs ../reminder.js   # 23 tests
node tests/view.test.mjs ../view.js             # 127 tests
node tests/backup.test.mjs ../backup.js         # 76 tests
python tools/static_check.py                    # wiring and structural invariants (incl. the version pin)
python tools/browser_check.py                   # 101 checks in real Chrome, plus 14 in a 380px phone-width frame
```

`recurrence.test.mjs` covers every rule family, the Feb-29 leap-year case, and
rule normalisation. `view.test.mjs` covers reminder ordering (including that a
spent one-off sorts last rather than as an epoch date), recurrence labels, the
relative-time buckets, folder paths, HTML escaping, subtree collection for a
recursive folder delete (including that a parent cycle terminates), the wording
of the delete confirmation, that every recurrence label names its period as well as its frequency, the folder picker's contents — tree order,
indent depth, and that a folder whose parent is missing is still offered, since
a folder the picker cannot name is one no note can be moved out of — and the
pane-ratio cycle: the exact four top-share percentages, that the 50% default is
not among them, that four clicks wrap back to the first, and that an
unparseable or old-format stored ratio falls back to the even split.

`backup.test.mjs` covers the CSV both directions: build → parse round-trips
(nesting, commas/quotes/newlines in note bodies, weekday rules, the v2
`mediaIds` column), CRLF and LF input, a leading BOM, malformed rows / missing
sections / duplicate ids refused with line numbers, dangling folder references
repaired *and reported*, the confirmation wording's counts and its
photos-not-included clause, **a v1 file still parsing** (only a file newer than
the app is refused), and the mailto ceiling refusing rather than truncating.

All three take the module path as an argument and default to the `../source/` layout.

`tools/static_check.py` proves names line up: every icon reference resolves,
every `$("#id")` has an element, no emitted class is unstyled, the service worker
caches every imported module. It also asserts the structural rules this project
has already broken once — controls wired before the first `await`, a guarded
`on()` rather than raw `addEventListener`, and code served network-first — plus
the Settings invariants that are easy to regress silently: the exact ratio list
in cycle order, the `1fr` fallbacks on the grid, the confirmation on the restore
path, and that `replaceAll`'s transaction names folders, notes and reminders and
*not* settings or media. That last one is what makes "your settings are kept"
a fact rather than a promise. And it enforces the headline promise directly: no
app module may contain `fetch`, `XMLHttpRequest`, `sendBeacon` or
`navigator.share` — nothing ever leaves the device. The v19 pins are static
too: the export panel is exactly Share CSV for backup + Export CSV (download/
share gone; the clipboard button's label was renamed to "Share CSV for backup"
on 2026-10-02),
the editor action row is nowrap, the narrow layout keeps the reminder date and
stands the relative time down, photos have their strip/viewer/OPFS plumbing,
and backup.js is v2 with a mediaIds column and a parse gate that accepts
older files.

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

For Settings it walks the whole story: every ratio click measured against the
fraction of the screen it should claim, the export panel carrying exactly
Share CSV for backup + Export CSV with the removed buttons proven absent, the export CSV
built and read back (the mail link's attribute only — a clicked `mailto:` hangs
headless Chrome forever), a garbage paste refused, a previewed backup armed, an
edit after the preview revoked, the confirmation cancelled and then accepted
with its exact wording inspected, folder nesting restored by indentation, the
note that existed only on the device gone afterwards, and — after a full page
reload — the ratio, the email address and the restored data all still there.
That last sequence is the proof that an import replaces your notes and nothing
else.

The photo pipeline is driven end to end with synthetic files (canvas-built PNGs
handed over through a `DataTransfer`, exactly what a real picker produces):
two attaches land two thumbnails *and* two files in OPFS *and* two records in
IndexedDB; removing one takes its bytes out of OPFS; save + reopen brings the
remaining thumbnail back; deleting the note — whose confirmation is inspected
for the photo warning — empties OPFS and the media store completely. The
380px pass measures the things layout bugs hide in: the reminder row shows the
date with the relative time stood down and no leftover separator, all on one
line; and Delete / Add photo/video / Save sit on one line at phone width.

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
