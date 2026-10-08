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
│ (Notes) (Reminders)                          │  <- upper half:
│ ┌──────────────────────────────────────────┐ │     ONE drill-down list
│ │ Notes › Work › Projects                  │ │
│ ├──────────────────────────────────────────┤ │
│ │ HR                              ⋯        │ │  <- subfolders first
│ │ IT                              ⋯        │ │
│ │ Apollo kickoff       3d ago      📎2      │ │  <- then notes
│ │ Budget draft         1w ago               │ │
│ └──────────────────────────────────────────┘ │
├──────────────────────────────────────────────┤
│ Upcoming reminders         7 · soonest first  │  <- lower half:
│  🕐 Standup   [Weekly·Tue,Thu]  in 2h · Mon 29 Sep 15:00
│  🕐 Passport  [Every 5 years]   in 3y · Fri 2 Oct 2027
└──────────────────────────────────────────────┘
```

The notes screen is **one drill-down list** (v38): a breadcrumb bar on top
naming the path to the folder being browsed, then a single scrollable list in
which the folder's subfolders come first and its notes after. Tapping a folder
row drills in; tapping a crumb jumps back up. *Notes* is always the first crumb
and the way home. Tapping a note swaps the whole list for its full-screen
editor — the crumbs stay above it (while a reminder is edited the crumbs give
way, since reminders have no path).

Since v36 an open **note** editor also steps the agenda aside entirely (a
pure-CSS rule keyed on the editor-open state), so the editor gets the whole
shell — and it returns the moment you leave the editor. Reminder edits keep the
agenda, and while you browse it is always there.

**The split is half and half by default — the notes region above, the agenda
below.** While you browse, the agenda is never scrolled off: a reminder coming
due stays in view. The even split is the default because the agenda
is where a reminder actually gets read, and a third of the screen cut the list
short while the notes region had room to spare. It is a default rather than a
rule: Settings → *Notes/Reminders panel display ratio* cycles the top pane
through 10%, 20%, 30% … up to 90% of the screen and back around, and remembers
the choice on the device. If the stored choice cannot be read, the app falls
back to the even split — never to a broken layout.

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

**Inside the notes half there is one list, not two panels** (v38). The stacked
folder tree that sat above the note list — and its Settings ratio cycle — are
gone. The folder being browsed is *everywhere on the screen at once*: its name
ends the breadcrumb path, its subfolders and notes fill the list below it, and
drilling in is simply tapping a subfolder row. The pane itself, notes above the
agenda, is the one split left.

**Opening a note is full-screen.** The editor takes over the whole list —
there is no tree to keep beside it now — with the crumbs holding your place
above it. The editor's top line states what the open note carries:
`2 · 1 pdf · 1 png` — the same badge the rows show, and it recounts the
moment a file is attached or removed, before Save.

**The chrome is deliberately thin.** Small gaps, small padding, compact buttons —
every pixel spent on margin is a folder name that gets cut off.

**The top bar is one row that never wraps.** On a narrow screen the button
labels collapse and the icons carry the action. The note mark at the top left
is a reload button as of v36: tapping it restarts the app in place — how a
phone picks up a new release without re-pasting the URL, and the recovery path
when a startup died (a reload re-runs boot against the fresh, network-first
shell). Like every navigation here, it silently discards unsaved edits.

**The upper half browses, then edits in place.** Tapping a note swaps the whole
drill-down list into its editor, full height, under the still-visible crumbs;
tapping a reminder swaps that whole area into its editor with a back arrow.

**Two tabs in the upper area.** *Notes* is the drill-down list. *Reminders* is
where reminders are managed — created, edited and deleted, with no folders and
no crumbs. The agenda below is read-only, which keeps exactly one place able to
change a reminder.

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

Branches nest to any depth and you walk them by drilling in, exactly like a
file manager: tap a folder row to enter it, tap a crumb to leave it. The
crumbs always read the path you are inside (`Notes › Work › Projects`), so you
always know both where you are and how to get back. Notes filed at no folder
live under **Unfiled**, and the first crumb is always *Notes* — the pseudo-root
every path starts from.

**Folders are renamed, moved and deleted through one ⋯ menu (v38).** Every
folder row carries an always-visible ⋯ button — same at 380px as at 900px,
never hidden behind hover — that opens a small menu with the three actions:

- **Rename** edits the row in place: prefilled input, **Save** (or Enter)
  commits, **✕** (or Escape) cancels, an empty name just keeps the old one, and
  the crumbs update with it.
- **Move** opens a bottom sheet listing every folder, indented, with *Unfiled*
  at the top — except the folder's own subtree, which cannot contain itself.
  Moving keeps the folder's contents and writes a new parent; this is new in
  v38: before, a folder could only be re-ordered within its own siblings, never
  re-parented.
- **Delete** is the same counted recursive confirmation it has always been.

Re-order by hand is gone. The small ↑/↓ arrows were the fiddliest controls on
the screen and never worked well on a phone; folders now simply sort
alphabetically. (Folders created by an older release may still carry the
arrangement that release stored — they are shown in that order, and the
`order` column still rides the backup — but nothing in the app re-arranges
them any more.) A new folder is created with the **New folder** button in the
top bar, and files into the folder you are browsing — no parent picker to
reason about.

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

While the editor is open, the path beside the tabs shows where the note lives,
so it updates the moment a move is saved. If you move a note out of the folder
you were browsing, it leaves that folder's list the moment you go back — which
is what the path is telling you.

## Deleting

Deleting a note or a reminder asks for confirmation naming the item. Deleting a
note that carries attachments names them too — its attached files are removed
with it, bytes and all:

```
Delete "Receipts"? Its 2 attachments will be removed too. This cannot be undone.
```

Deleting a **folder** deletes the folder, its subfolders and every note inside
them — the whole subtree, in one action. The confirmation counts what is about to
go before it goes:

```
Delete "Work"? This also deletes 3 subfolders and 12 notes. This cannot be undone.
```

There is no undo and no trash, so the counts are stated first rather than
discovered afterwards.

Folder actions live behind the ⋯ button that sits on every folder row **at
rest** — always on screen, never revealed only on hover. It is quiet grey
against the row and darkens on hover or focus, and Delete is one tap inside its
menu. It used to be a hover-revealed delete control, which made it impossible
to find on a desktop and impossible to reach at all on a touch screen, where
there is no hover. Two checks now hold that open, one static and one in the
browser, because a control that exists but cannot be found is a control that
does not work.

## Settings: the ratio, export, import

The **Settings** button in the top bar opens one dialog with two controls —
and the release number at the bottom ("Version 38"), so on any device you can
see which revision is running. The number is not free-floating decoration:
`tools/static_check.py` pins it to `sw.js`'s cache name (`notes-shell-v38`) and
fails the build if the two drift, and the browser check compares what the
dialog shows against the version this checkout carries (and, on the deployed
site, against the live `sw.js` bytes). Bump `APP_VERSION` in `view.js` and
`CACHE_NAME` in `sw.js` together, every release.

**Notes/Reminders panel display ratio.** The one split this app still has —
notes above, agenda below. Each click moves the split to the next top-pane
share — `10% → 20% → … → 90%` and back around, in ten-percent steps (the
2026-10-05 revision; it previously cycled 20/40/60/80). The choice is stored on
the device and applied again on the next launch (the browser check proves this
by reloading the app and re-measuring the panes). A fresh device starts at the
even split (50%), which is the value the chip shows until the first click, and
50% is the fallback an unreadable stored value degrades to. The second ratio —
the folder/content split inside the notes pane — died with the folder tree
itself in v38: there is no second panel to size.

**Export notes/reminders to email.** The panel builds the whole database —
nested folders with their parenting, notes, and reminders — as one text CSV
under a versioned `# notes-backup v3` header, and the primary action is the
one-tap **Back up notes + photos now**: it collects the bundle once and hands
it to every configured destination independently (since v28) — each
destination reports its own status line, one destination's failure never
blocks or masks the other, and with nothing configured the panel says so.

- **Google Drive — the primary full backup, per user.** Every tap places one
  ZIP — the backup CSV plus every photo/video — in a `Notes Backup` folder in
  the connected user's own Google Drive, as `notes-backup-<exportId>.zip`, a
  fresh file per backup. Since v29 the backup is **per person**, not per
  device: the app ships ONE public OAuth Client ID in `config.js`, and each
  visitor taps **Connect Google Drive** and picks their own account in
  Google's window. The token that comes back belongs to that account only, so
  every account gets its own `My Drive/Notes Backup/` and no user can ever
  see or overwrite another user's backup — the isolation is Google's own
  Drive-per-account semantics behind a per-user token, not an app-side flag.
  No GitHub token, no home PC, no watcher, no GitHub at all. If the token has
  expired by the next tap, the app says one line and that tap signs in again;
  it never opens a popup on its own. **Disconnect** clears only this
  browser's sign-in state (token in memory; nothing persisted) — notes,
  reminders, photos and the relay token are untouched, and the other device
  stays connected; to withdraw access everywhere, remove the app at Google's
  permissions page. Power users can override the configured Client ID per
  device via `localStorage.setItem("notes.drive.client", "<client id>")`
  (useful for testing a new one before a redeploy).

  Site-owner setup (once): in [console.cloud.google.com](https://console.cloud.google.com)
  create a project, add the **OAuth consent screen** (External, yourself as a
  test user), then **Credentials → OAuth client ID → Web application** with
  authorised JavaScript origin `https://choochatgpt.github.io`, and paste that
  Client ID into `config.js` (`driveClientId`) — it ships with the app, so
  nobody who uses your shared URL pastes anything. While your project is in
  **Testing mode**, only the accounts you listed as test users can connect;
  move the consent screen to **Production** and any Google user who opens the
  shared URL can connect their own Drive. No password is ever involved: the
  app opens **Google's own sign-in window**, the current user signs in there,
  and the app receives only a short-lived access token (`drive.file` scope —
  it can touch just the files this app created in that one account's Drive).
  The token lives in memory for under an hour and is never written anywhere;
  the Client ID, by contrast, is public by design — it is not a secret.
  Duplicate `Notes Backup` folders are possible if two of a user's devices
  create one at the same moment — harmless; both still work.
- **PC relay — optional legacy.** The 2026-10-03 route: the same tap pushes
  the backup CSV *and every photo/video* to the PC — the whole export lands
  in `C:\notes_backups` and the CSV is emailed as before, with no manual step
  on the phone — but only when its own token is saved, and never at the cost
  of the Google Drive backup. One-time setup: create a fine-grained GitHub
  token (Settings → Developer settings → Fine-grained tokens → *Only select
  repositories* → `choochatgpt/ask-ai-relay` → Permissions → Contents: Read
  and write) and paste it into the panel's token box. The token lives in this
  browser's localStorage only — never in the app's public source (the
  transport is the one authorised for JScan on 2026-09-21). The bytes ride a
  private branch of the **private** relay repo, the PC watcher verifies every
  SHA256, archives the photos, emails the CSV, then rewrites the branch so
  the bytes leave GitHub entirely; the next backup reports the previous
  bundle's pickup. The relay is NOT needed for the Google Drive backup
  above; with the watcher off, nothing on the PC expects it.
- **Share CSV for backup** (fallback) puts the exact text on the clipboard,
  for the relay-inbox paste or any email.
- **Export CSV** (fallback — the emergency text backup) opens your mail app with the CSV in the message
  body, addressed to the address you entered. `mailto:` cannot attach files,
  and long bodies are silently truncated by some mail clients, so a backup that
  would exceed a conservative 1800-character limit is refused outright rather
  than cut short — the panel says so and points at Share CSV for backup
  instead. The app never sends email itself; you press Send.

*Download .csv* and *Share .csv* were removed on request (2026-10-01): a saved
file still needed a manual attach step, and the phone's share sheet had already
refused a send in the wild — copy + mailto covered every route the two of them
served. The app now has no `navigator.share` anywhere; the static check bans it
outright.

The address is remembered in the same local database as your notes — it never
leaves the device either. The backup is **text only**: attachments ride along
as an id list (`mediaIds`) and never as bytes. On the same device a restore
therefore reattaches the files; on a new device the notes come back without
them.
Folders carry their hand arrangement back too: an `order` column rides along
(v3). A v1 backup (no mediaIds column) and a v2 backup (no order column) still
restore — older files parse, alphabetically arranged as their releases showed
them. Reminder due dates are
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

**The pickup watcher (optional legacy — currently disabled).**
`tools/relay_backup_watch.py` loops in the background (it was registered as a
Windows scheduled task, "Notes backup watch", at sign-in): every 5 minutes it
fetches the relay clone once and serves both
inboxes — a pasted backup CSV goes to
`relay_pull_backup.py --archive-dir C:\notes_backups` (dated copy + email),
and photos waiting in the media inbox go to `relay_pull_media.py` (archive +
clear). So a paste on the phone
lands on the PC as a dated copy in `C:\notes_backups` *and* an emailed
attachment, and a photo upload lands in `C:\notes_backups\media` — with no
PC-side step to remember. Failures are logged to
`C:\notes_backups\relay_backup_watch.log` and retried on a later cycle; the
repo is only ever touched by the pickup tools' own success paths. `--once`
runs a single cycle for testing. **Status since 2026-10-05: disabled on this
PC.** Its every-5-minute git cycle repeatedly spawned visible console
windows, and the Google Drive backup above has removed the need for it —
Drive is the primary backup and needs no PC at all. Re-enabling is a manual
decision (re-register the scheduled task); nothing in the app depends on it,
and relay syncs still work with the token saved.

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
(exported 2026-09-29T03:09:36.905Z). Attached files are not included in a
backup. Your settings are kept. This cannot be undone.
```

The replace is a single database transaction: it lands completely or not at all —
a cancelled confirmation, a re-parse failure or a mid-write error leaves the data
exactly as it was. **"Your settings are kept" is structural, not a promise**: the
restore writes only folders, notes and reminders, so neither the pane ratio nor
the saved email address can be changed by an import — and any attachments that
exist later would sit in a store the restore does not touch.

## Attachments on notes

Open a note and tap the one **Attach** button (v38): a small menu offers
**Photo or video (gallery)** / **Take photo or video (camera)** / **Document**.
Gallery opens the gallery-style picker (images and videos), Document is the
original broad picker (images, videos **and PDFs**), and Camera opens the
camera applet on a phone — desktop Chrome ignores the capture hint and behaves
like the gallery. The three menu items fire the same three hidden pickers that
the old button row did, so all three feed the same pipeline — only the trigger
shrank from two rows of buttons to one button. Images land as thumbnails on a
strip above the actions; a PDF gets a labelled tile ("PDF") instead — the strip has
no picture to show for a document, by design. The change is applied immediately
(no need to press Save first, and nothing is orphaned if you close without
saving). Tapping a thumbnail or tile opens it full-size in a viewer: pictures
inline, videos in a player, and PDFs in an embedded frame. Some engines —
Chrome on Android notably — embed PDFs nowhere; the viewer states this and
points at **Save to device**, which writes a copy of the full-size bytes into
the phone's downloads/gallery under the original file name — the one way
attachments leave the app, built for the backup route below. The **×** on a
tile removes that one attachment.

The note list shows what a note carries without opening it: a small paperclip
line under the title — `2 png` for one kind, `7 · 5 jpg · 2 pdf` (total first)
for a mix — plus the full breakdown in a hover title. It counts the records
that are actually on the device, so it never shows a ghost. Since v35 the
same badge sits at the top of the open editor (v35), where the list used to
be, so the swap announces what it is carrying — and it recounts live when a
file is attached or removed while the note is open.

The storage is split so that neither half is heavier than it needs to be:

- A small record per attachment — id, file name, type, size and a bounded
  JPEG thumbnail (documents have none) — in the IndexedDB `media` store.
- The full-size bytes in the browser's **OPFS** (`media/` directory, keyed by
  the same id), read only when the viewer opens.

Everything is origin-scoped device storage: attachments stay on the device
unless you deliberately save one out, and
the text backup carries only the id list. `replaceAll` — the restore's one
transaction — touches neither the media store nor OPFS, so a restore can never
destroy an attachment; a same-device restore reattaches them via the ids, and a
different device simply shows notes without them. Deleting a note deletes its
attachments (the confirmation names the count first), and the browser check
proves the bytes actually leave OPFS when they should.

**Backing photos up (one-tap backup, asked for 2026-10-03).** The primary
route is no longer manual: tapping **Back up notes + photos now** in the
export panel sends every photo/video *with* the backup CSV. Since v28 the
Google Drive ZIP is the primary destination and always available; the PC
route (see the export section above — JScan's authorised transport: your own
token in this browser, bytes on a private branch of the **private** relay
repo, PC archives into `C:\notes_backups\media\<transfer id>\` with SHA256
manifests and then rewrites the branch so the bytes leave GitHub) runs only
when its token is saved. The PC side is automatic via the optional watcher
when it is enabled.

The manual fallbacks remain for a phone with no token saved yet:

1. Phone: open the note → tap the attachment → **Save to device**.
2. Phone: github.com/choochatgpt/ask-ai-relay → `gitway/transfer_inbox/notes/media/`
   → **Add file → Upload files** → pick the saved file(s) → Commit changes
   (commit straight to main — the two mobile-web traps are written up in that
   folder's README).
3. PC: nothing — the watcher picks it up.

Rules: private repo only; per-file cap 24 MB (the sync refuses bigger and
names the file); the text backup CSV stays text-only in its own column.

The editor's actions are one row as of v38 — **Attach** (the 📎 menu above),
Delete, Save — pinned to one line at every width: the row never wraps, and the
labels compact down instead. The three pickers it replaced are the same three
hidden inputs, unchanged and still pinned by the checks. While the editor is
open the agenda below steps aside (see the split above), so the row has the
room.

## Not built yet

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
| `storage.js` | IndexedDB wrapper (`notes-local`, v1) + the OPFS byte store for attached files |
| `reminder.js` | Recurrence engine and `nextDueAt` calculation |
| `sw.js` | Offline cache |

## Tests

```sh
node tests/recurrence.test.mjs ../reminder.js   # 23 tests
node tests/view.test.mjs ../view.js             # 169 tests
node tests/backup.test.mjs ../backup.js         # 89 tests
node tests/sync.test.mjs ../sync.js             # 47 tests (stubbed GitHub API)
node tests/drive.test.mjs ../drive.js           # 79 tests (stubbed Google API)
python tools/static_check.py                    # wiring and structural invariants (incl. the version pin)
python tools/browser_check.py                   # 254 checks in real Chrome (225 desktop + 29 at 380px)
```

`recurrence.test.mjs` covers every rule family, the Feb-29 leap-year case, and
rule normalisation. `view.test.mjs` covers reminder ordering (including that a
spent one-off sorts last rather than as an epoch date), recurrence labels, the
relative-time buckets, folder paths, HTML escaping, subtree collection for a
recursive folder delete (including that a parent cycle terminates), the wording
of the delete confirmation, the v38 breadcrumb chain (`folderChain`: root-first
segments, a looped parent chain terminates, a missing parent stops cleanly),
the attachment badge text its rows and editor share, the
folder picker's contents — tree order,

`backup.test.mjs` covers the CSV both directions: build → parse round-trips
(nesting, commas/quotes/newlines in note bodies, weekday rules, the v2
`mediaIds` column, the v3 folder `order` column — zero orders included, since
0 is a real position), CRLF and LF input, a leading BOM, malformed rows /
missing sections / duplicate ids refused with line numbers, dangling folder
references repaired *and reported*, a folder whose parent is gone lifted to the
top with a warning, the confirmation wording's counts and its
attachments-not-included clause, **v1 and v2 files still parsing** (only a file
newer than the app is refused), junk order cells (`3.5`, `0x2`, words) staying
unordered, and the mailto ceiling refusing rather than truncating.

All five take the module path as an argument and default to the `../source/` layout.

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
the editor actions are one nowrap row since v38 (Attach's menu button first,
then Delete, Save), the narrow layout keeps the reminder date and
stands the relative time down, attachments have their strip/viewer/OPFS
plumbing (v34: a PDF tile, the document iframe branch, and
`attachmentBadge` both defined and used), and backup.js is v3 with a mediaIds
+ folder-order columns and a parse gate
that accepts older files. The v38 pins hold the drill-down screen to its shape:
`view.js` exports `folderChain` and app.js renders it into `#crumbs` (with the
bar hidden off the reminders tab), index.html carries the `#folder-menu` /
`#folder-move-sheet` dialogs and every menu button they need, the ⋯ trigger
(`.folder-menu`) is visible at rest in both widths — the same no-hover-reveal
rule the delete once broke — no `state.folderChip` survived the chips'
removal, and the note list stays one delegated click contract
(`on("#note-list", "click", …)`) rather than a listener per row. The old v32
rename/reorder pins and the v33 stacked-layout pins died with the controls and
the layout they described.

`tools/browser_check.py` is the only check that runs the app for real. It serves
the app, opens it in headless Chrome, clicks every control and inspects the
resulting DOM — including moving a note and confirming it left the folder it was
in (not just that it arrived in the new one), deleting a folder through the v38
⋯ menu: that the ellipsis button is visible on the row without hovering
(desktop AND 380px widths), that opening it really opens the menu dialog, that
cancelling the confirmation keeps the folder, that the confirmation names the
folder and counts the notes inside it, and that confirming removes both — and
the v38 organise flow: folder rows first inside the browsed list, drilling in
updates the crumbs, two folders start alphabetical, the rename menu edits a row
in place (prefill, save, Escape-cancel, empty-name no-op), the move sheet offers
every folder *except the moved folder itself*, a sheet move re-parents a folder
under another one, moving out of the browsed folder updates the crumbs, a new
folder files into the folder being browsed, and the renames + moves survive a
restore and a reload. It also measures the two panes' rendered
heights, because equal rows in the source do not prove equal panes on screen —
a `min-height` on either one breaks the split without touching the rule — and
after enough notes are seeded the list scrolls inside its own panel while the
crumb bar's screen position stays exactly where it was. v36 adds the
editor-open pair: an open note editor measured to have collapsed the agenda, a
reminder edit measured to have kept it, and the agenda measured back on close.
Since
v37 an untitled note's row reads the name of the folder it lives in ("Unfiled"
for the pseudo-folder — a note titles itself after its home, exactly the way
the screenshot the user sent asked for): the desktop pass reads that title
before and after a move to prove the label follows the CURRENT folder rather
than being a one-time copy, and the placeholder survives only where the folder
cannot be named. The
brand-mark reload runs last in the whole flow, deliberately — a reload kills
every stub in the frame — and its headless Chrome asserts a genuinely fresh
document whose header chip reads this checkout's version. Static
checks can prove an id exists and a listener is attached in the source; they
cannot prove a click *does anything*, which is the failure this project actually
hit — see "Releasing" below.

For Settings it walks the whole story: every pane-ratio click measured against
the fraction of the screen the top pane should claim, the export panel carrying exactly
Share CSV for backup + Export CSV with the removed buttons proven absent, the export CSV
built and read back (the mail link's attribute only — a clicked `mailto:` hangs
headless Chrome forever), a garbage paste refused, a previewed backup armed, an
edit after the preview revoked, the confirmation cancelled and then accepted
with its exact wording inspected, folder nesting restored by indentation, the
note that existed only on the device gone afterwards, and — after a full page
reload — the ratio, the email address and the restored data all still there.
That last sequence is the proof that an import replaces your notes and nothing
else.

The attachment pipeline is driven end to end with synthetic files (canvas-built
PNGs and a minimal PDF handed over through a `DataTransfer`, exactly what a
real picker produces): two attaches land two thumbnails *and* two files in OPFS
*and* two records in IndexedDB; removing one takes its bytes out of OPFS; save
+ reopen brings the remaining thumbnail back; a PDF attaches with its labelled
tile and its `application/pdf` record, and the v34 note-row badge is read off
the real list (`2 · 1 pdf · 1 png` with the full breakdown in the title);
tapping the PDF tile opens the document viewer on a `blob:` iframe and closing
empties it; deleting the note — whose confirmation is inspected for the
attachment warning — empties OPFS and the media store completely. The narrow
pass drives the picture picker (`#gallery-input`) through that same
`DataTransfer` route and asserts the three-input picker recipe statically —
the camera input is capture-pinned but never driven (headless Chrome has no
camera). The
380px pass measures the things layout bugs hide in: the reminder row shows the
date with the relative time stood down and no leftover separator, all on one
line; the attachment-count line renders inside the preview column (the v19
one-word-per-line trap, guarded); the editor's one action row keeps its line at
phone width (Attach's menu button, Delete, Save tops aligned, no wrap) with the
agenda collapsed under an open editor; the 📎 opens its menu dialog and
choosing gallery closes it and fires the picker; and browsing into a folder at
380px updates the crumbs — the drill-down is one layout at every width.

It stubs `alert()` and `confirm()` inside the frame — a real modal blocks headless
Chrome forever — but answers `confirm()` from a variable, so the destructive path
can be cancelled and then taken, and the wording it showed can be inspected.
The sync + Drive network never leaves the machine either: the probe installs a
selective `fetch` wrapper inside the frame that answers `api.github.com` (the
relay happy path) and `www.googleapis.com` (folder find-or-create, resumable
initiation, session PUT) from scripts, stubs the Google Identity Services token
client, and passes everything else to the real fetch — so even the sync and the
Drive upload run at zero real network.
Every run wipes the browser profile first: IndexedDB lives in the profile, and a
leftover one would carry notes into the next run and make every count meaningless.
The harness also sweeps any Chrome process still using the run's profile before
launching and after terminating — the Windows launcher chrome.exe exits the
instant it spawns the real browser, so `terminate()` used to orphan the browser
and the leaked singleton lock silently swallowed later runs' launches (the
2026-10-05 incident: three runs in a row "never reported back").

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
