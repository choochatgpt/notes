#!/usr/bin/env python3
"""Static wiring checks for the Notes app.

    python tools/static_check.py

Catches the naming mistakes that fail at runtime in a vanilla ES-module app: a
<use href="#i-x"> with no matching <symbol id="i-x">, a $("#x") lookup with no
element carrying that id, an emitted class with no CSS rule, an imported module
the service worker does not cache.

It also asserts the structural invariants this project has already broken once:
controls wired before the first await, a guarded on() helper rather than raw
addEventListener on the toolbar, and a service worker that treats code as
versioned. Those are the conditions that made one thrown error leave every
button on the page dead.

It proves an id exists and a listener is attached. It cannot prove a click does
anything -- for that, run tools/browser_check.py, which drives a real browser.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
fails: list[str] = []
notes: list[str] = []


def read(name: str) -> str:
    return (REPO / name).read_text(encoding="utf-8")


def body_of(name: str) -> str:
    """The source of one function, from its declaration to the next one."""
    chunk = app.split(f"function {name}(", 1)
    if len(chunk) < 2:
        return ""
    return chunk[1].split("\nasync function ", 1)[0].split("\nfunction ", 1)[0]


html = read("index.html")
app = read("app.js")
view = read("view.js")
sw = read("sw.js")

# --- 1. SVG sprite: every referenced symbol must be defined -------------------
symbols = set(re.findall(r'<symbol\s+id="([^"]+)"', html))
refs = set(re.findall(r'href="#(i-[A-Za-z0-9_-]+)"', html))
refs |= set(re.findall(r'href="#(i-[A-Za-z0-9_-]+)"', app))
print(f"symbols defined : {len(symbols):>2}  {sorted(symbols)}")
print(f"symbols used    : {len(refs):>2}  {sorted(refs)}")
for ref in sorted(refs - symbols):
    fails.append(f"index.html/app.js references #{ref} but no <symbol> defines it")
unused = sorted(symbols - refs)
if unused:
    notes.append(f"defined but never used: {unused}")
print()

# --- 2. Element ids: every $("#x") must exist in the markup ------------------
html_ids = set(re.findall(r'\bid="([^"]+)"', html))
js_ids = set(re.findall(r'\$\("#([^"]+)"\)', app))
print(f"DOM ids in html : {len(html_ids)}")
print(f"ids queried in js: {len(js_ids)}  {sorted(js_ids)}")
for missing in sorted(js_ids - html_ids):
    fails.append(f'app.js queries $("#{missing}") but no element has that id')
print()

# --- 3. Class names used by generated markup must exist in the stylesheet ----
css = read("app.css")
css_classes = set(re.findall(r'\.([a-zA-Z][A-Za-z0-9_-]*)', css))
# Classes emitted from template literals inside app.js.
emitted = set()
for match in re.findall(r'class="([^"$]*)"', app):
    emitted |= set(match.split())
# Classes present in the static markup too.
for match in re.findall(r'class="([^"]*)"', html):
    emitted |= set(match.split())
dynamic = {"active", "open", "leaf", "past", "hidden"}
missing_css = sorted(c for c in (emitted | dynamic) if c and c not in css_classes)
if missing_css:
    fails.append(f"classes emitted with no CSS rule: {missing_css}")
print(f"classes emitted : {len(emitted)} | unstyled: {missing_css or 'none'}")
print()

# --- 4. Service worker shell must list every runtime module ------------------
shell_block = sw.split("APP_SHELL = [", 1)[1].split("]", 1)[0]
shell = {ln.strip().strip(",").strip('"') for ln in shell_block.splitlines() if ln.strip()}
shell = {s.lstrip("./") for s in shell if s}
modules = set(re.findall(r'from "\./([^"]+)"', app))
missing_shell = sorted(modules - shell)
if missing_shell:
    fails.append(f"app.js imports {missing_shell} but sw.js APP_SHELL does not cache them")
print(f"app.js imports  : {sorted(modules)}")
print(f"APP_SHELL       : {len(shell)} entries | missing: {missing_shell or 'none'}")
cache = sw.split("CACHE_NAME =", 1)[1].split(";", 1)[0].strip()
print(f"CACHE_NAME      : {cache}")
print()

# --- 5. The top bar must be pinned to a single row ---------------------------
topbar = re.search(r"\.topbar\s*\{([^}]*)\}", css)
if not topbar:
    fails.append("no .topbar rule found")
else:
    if "nowrap" not in topbar.group(1):
        fails.append(".topbar does not set flex-wrap: nowrap")
actions = re.search(r"\.topbar-actions\s*\{([^}]*)\}", css)
if not actions:
    fails.append("no .topbar-actions rule found")
else:
    if "wrap" not in actions.group(1) or "nowrap" not in actions.group(1):
        fails.append(".topbar-actions does not pin flex-wrap: nowrap")
    else:
        print("top bar pinned to one row: .topbar and .topbar-actions both nowrap")

# --- 6. The stacked split: notes above, agenda below -------------------------
# The two rows read custom properties so the Settings ratio button can
# re-balance them (1:4 through 3:4). What must never drift: exactly two tracks,
# both driven by the ratio variables, and both falling back to 1fr -- the
# default even split lives in the CSS, so a failed settings read cannot change
# the layout at all.
shell = re.search(r"\.app-shell\s*\{([^}]*)\}", css)
if not shell:
    fails.append("no .app-shell rule found")
else:
    rows = re.search(r"grid-template-rows:\s*([^;]+);", shell.group(1))
    if not rows:
        fails.append(".app-shell has no grid-template-rows, so the split is not stacked")
    else:
        tracks = rows.group(1)
        if tracks.count("minmax(") != 2 or tracks.count("var(") != 2:
            fails.append(f".app-shell must be exactly two ratio-driven tracks, found {tracks!r}")
        elif "var(--pane-top" not in tracks or "var(--pane-bottom" not in tracks:
            fails.append(".app-shell rows must read --pane-top/--pane-bottom, "
                         "so the Settings ratio can vary the split")
        elif (not re.search(r"var\(--pane-top,\s*1fr\)", tracks)
              or not re.search(r"var\(--pane-bottom,\s*1fr\)", tracks)):
            fails.append(".app-shell ratio variables must fall back to 1fr, "
                         "so a missing setting still splits the screen evenly")
        else:
            print(f"stacked split: app-shell rows = {tracks.strip()} (default even)")
if "pane-agenda" not in html:
    fails.append("index.html has no agenda pane")
if 'id="agenda-list"' not in html:
    fails.append("index.html has no #agenda-list")

# --- 6b. Settings: the ratio cycle, export and import ------------------------
# The offered ratios are a product decision, not a style detail: the four
# top-share percentages the user asked for (2026-09-29 revision), in their
# order, wrapping. The default 50% is deliberately NOT in the cycle -- it is
# the CSS fallback and the chip's starting value, checked separately below.
ratio_match = re.search(r"RATIOS\s*=\s*\[([^\]]+)\]", view)
offered = re.findall(r'"([^"]+)"', ratio_match.group(1)) if ratio_match else []
required = ["20%", "40%", "60%", "80%"]
if offered != required:
    fails.append(f"view.js RATIOS must be {required} in order "
                 f"(the four requested top shares), found {offered}")
elif "function nextRatio(" not in view:
    fails.append("view.js has no nextRatio, so the ratio button cannot cycle")
elif "function ratioToTracks(" not in view:
    fails.append("view.js has no ratioToTracks, so a chosen ratio cannot reach the grid")
elif 'DEFAULT_RATIO = "50%"' not in view:
    fails.append('view.js must define DEFAULT_RATIO = "50%" -- the even split '
                 "is where a fresh device starts and what an unreadable value falls back to")
elif 'DEFAULT_RATIO' not in app:
    fails.append("app.js never uses DEFAULT_RATIO, so the chip and the store can drift from view.js")
elif '<span id="ratio-value" class="chip accent">50%</span>' not in html:
    fails.append("the ratio chip must START at 50%, so the dialog opens showing the "
                 "default before any click")
else:
    print(f"ratio cycle: {' -> '.join(offered)} -> (wrap); default 50% even")

if 'setProperty("--pane-top"' not in app or 'setProperty("--pane-bottom"' not in app:
    fails.append("app.js never writes --pane-top/--pane-bottom, so the chosen ratio never reaches the layout")
if 'setSetting("paneRatio"' not in app:
    fails.append("app.js never persists the pane ratio, so the choice resets on every launch")
if 'getSetting("paneRatio"' not in app:
    fails.append("app.js never reads the pane ratio back, so the stored choice is ignored")

for required_label in (
    "Notes/Reminders panel display ratio",
    "Export notes/reminders to email",
    "Import notes/reminders",
):
    if required_label not in html:
        fails.append(f'the Settings dialog is missing the "{required_label}" control')
if 'id="settings-dialog"' not in html:
    fails.append("index.html has no #settings-dialog, so Settings has nowhere to open")

# The agenda must be the upcoming-only view, soonest first.
agenda = app.split("async function renderAgenda", 1)
if len(agenda) < 2:
    fails.append("app.js has no renderAgenda")
else:
    body = agenda[1].split("\nasync function ", 1)[0]
    if "sortReminders" not in body:
        fails.append("renderAgenda does not sort soonest-first")
    if ".filter(reminder => reminder.nextDueAt)" not in body:
        fails.append("renderAgenda does not exclude reminders with no future occurrence")
    else:
        print("agenda: upcoming only, soonest first")

# --- 6b. Every reminder is one line, and it states how it repeats -----------
# Two things make the home-page row a single line and neither is visible in the
# markup: the row must be a flex line rather than a stacked grid, and the due
# block must lie along it rather than stacking inside itself. A row that stacks
# looks identical in the source -- it is one extra wrapping element.
row_fn = body_of("reminderRow") if "reminderRow" in app else ""
if not row_fn:
    fails.append("app.js has no reminderRow, so the agenda cannot be rendered")
else:
    if "reminder-row" not in row_fn:
        fails.append("reminderRow does not carry reminder-row, so the one-line layout never applies")
    if "item-main" in row_fn:
        fails.append("reminderRow still wraps the title in item-main, which stacks the "
                     "title above the recurrence and gives the row a second line")
    if "describeRule" not in row_fn:
        fails.append("reminderRow does not state the recurrence, so the home page never "
                     "shows the period or the frequency")
    if "chip" not in row_fn:
        fails.append("reminderRow emits no chip, so the recurrence has nowhere to appear")
    else:
        print("agenda rows: one line each, carrying the recurrence and the due time")

row_rule = re.search(r"\.item-row\.reminder-row\s*\{([^}]*)\}", css)
if not row_rule:
    fails.append(".item-row.reminder-row has no CSS rule, so the one-line row is not laid out")
else:
    body = row_rule.group(1)
    if "display: flex" not in body:
        fails.append(".item-row.reminder-row is not a flex line, so it inherits the grid "
                     "and the recurrence wraps onto a second line")
    if "column" in body:
        fails.append(".item-row.reminder-row stacks its children, so the row is not one line")

when_rule = re.search(r"\.item-row\.reminder-row\s+\.item-when\s*\{([^}]*)\}", css)
if not when_rule:
    fails.append("the due block has no rule inside a reminder row, so it would stack its "
                 "relative and absolute times and make the row two lines")
elif "flex-direction: row" not in when_rule.group(1):
    fails.append("the due block inside a reminder row does not lie along the row")

# --- 7. Folders are scoped to notes, and the editor swaps in place -----------
if 'document.body.dataset.kind' not in app:
    fails.append("app.js never sets body[data-kind]")
if 'els.browseNotes.classList.toggle("hidden"' not in app:
    fails.append("app.js never hides the notes browser, so folders are not scoped to notes")
if 'els.editorBack.classList.toggle("hidden"' not in app:
    fails.append("app.js never toggles the back arrow, so the editor cannot swap in place")
else:
    print("browse/edit swap in place: notes browser, editor and back arrow all toggled")

# --- 8. Every destructive path is behind a confirmation ----------------------
for fn in ("deleteFolder", "deleteSelectedNote", "deleteSelectedReminder"):
    chunk = body_of(fn)
    if not chunk:
        fails.append(f"app.js has no {fn}, but the UI offers a delete for it")
    elif "confirm(" not in chunk:
        fails.append(f"{fn} deletes without confirming first")
    elif "remove(" not in chunk:
        fails.append(f"{fn} never calls remove(), so it cannot actually delete")

if "describeDeletion(" not in body_of("deleteFolder"):
    fails.append("deleteFolder does not state the counts in its confirmation")
elif "collectSubtree(" not in body_of("deleteFolder"):
    fails.append("deleteFolder does not collect the subtree, so subfolders would be orphaned")
else:
    print("destructive paths: folder (recursive, counted), note and reminder all confirmed")

# The restore is the largest destructive path in the app -- it replaces every
# note and reminder on the device -- so it needs the same confirmation, and it
# must still be bound to the text the user actually previewed: pasting new
# content after a preview must not restore something the preview never showed.
restore = body_of("restoreBackup")
if not restore:
    fails.append("app.js has no restoreBackup, but the Settings dialog offers an import")
elif "confirm(" not in restore:
    fails.append("restoreBackup replaces everything without confirming first")
elif "replaceAll(" not in restore:
    fails.append("restoreBackup never calls replaceAll(), so it cannot actually restore")
elif "previewedText" not in restore:
    fails.append("restoreBackup does not re-check the previewed text, so editing the "
                 "textarea after previewing could restore something the preview never showed")
else:
    print("restore: bound to the previewed text, confirmed with counts, applied via replaceAll")

# replaceAll must be all-or-nothing over exactly the three stores a backup
# carries. settings and media are excluded by construction -- that is the
# guarantee that an import can neither change the pane ratio nor destroy photos.
storage = read("storage.js")
start = storage.find("export async function replaceAll(")
if start < 0:
    fails.append("storage.js has no replaceAll, so a restore cannot be atomic")
else:
    end = storage.find("\nexport ", start + 5)
    body = storage[start:] if end < 0 else storage[start:end]
    store_list = re.search(r"db\.transaction\(\[([^\]]+)\]", body)
    listed = re.findall(r'"([^"]+)"', store_list.group(1)) if store_list else []
    if listed != ["folders", "notes", "reminders"]:
        fails.append("replaceAll must run one transaction over exactly "
                     f'["folders", "notes", "reminders"], found {listed}')
    elif '"settings"' in body or '"media"' in body:
        fails.append("replaceAll names settings or media; a restore must never touch either")
    else:
        print("replaceAll: one transaction over folders+notes+reminders; settings and media excluded")

# Each delete button must be wired, or it would be silently inert.
for button, handler in (
    ("delete-note-btn", "deleteSelectedNote"),
    ("delete-reminder-btn", "deleteSelectedReminder"),
):
    if f'on("#{button}", "click", {handler})' not in app:
        fails.append(f"#{button} exists in the markup but is never wired to {handler}")
if "data-del=" not in app or ".folder-del" not in app:
    fails.append("folder rows carry no delete control")
if ".folder-del" not in css:
    fails.append(".folder-del has no CSS rule, so the folder delete would be invisible")
else:
    # It used to be hover-revealed. That was the bug the user hit: the control
    # existed but could not be found, and on a touch screen (no hover) it could
    # not be reached at all. It must be on screen at rest.
    del_rule = re.search(r"\.folder-del\s*\{([^}]*)\}", css).group(1)
    for hidden in ("opacity: 0", "opacity:0", "pointer-events: none", "visibility: hidden",
                   "display: none"):
        if hidden in del_rule:
            fails.append(
                f".folder-del is hidden at rest ({hidden}); a folder delete that only "
                f"appears on hover is undiscoverable and unreachable on a touch screen")
    if "color:" not in del_rule:
        fails.append(".folder-del sets no resting colour, so it would be invisible")

# --- 10. Controls must be wired before anything that can fail ---------------
# A publish can briefly pair a new index.html with a still-cached app.js. When
# init() wired its listeners after the first render, one thrown error left every
# top-bar button inert with nothing on screen explaining why.
init_body = app.split("async function init()", 1)
if len(init_body) < 2:
    fails.append("app.js has no init()")
else:
    init_body = init_body[1].split("\ninit().catch", 1)[0]
    wire_at = init_body.find("wireControls()")
    first_await = init_body.find("await ")
    if wire_at < 0:
        fails.append("init() never calls wireControls()")
    elif first_await >= 0 and wire_at > first_await:
        fails.append("init() wires controls after its first await, so one failure leaves every button dead")
    else:
        print("init(): controls wired before the first await")

    # A handler for an id that does not exist would be dropped silently.
    for selector in re.findall(r'on\("#([^"]+)"', app):
        if selector not in html_ids:
            fails.append(f'wireControls() targets "#{selector}" but no element has that id')

    # The top-bar actions must go through the guarded wiring.
    for button in ("new-folder-btn", "new-note-btn", "new-reminder-btn", "settings-btn"):
        if f'$("#{button}").addEventListener' in app:
            fails.append(f'#{button} is wired with a raw addEventListener, which throws if absent')
    if "function on(selector, event, handler)" not in app:
        fails.append("app.js has no guarded on() helper")

    # An async handler's rejection must not vanish.
    if "typeof result.catch" not in app:
        fails.append("on() does not catch a rejected handler, so a failed action would be silent")

# --- 11. The worker must not serve stale code alongside fresh markup --------
for destination in ('"document"', '"script"', '"style"'):
    if destination not in sw:
        fails.append(f"sw.js does not treat {destination} as versioned, so it can serve stale code")
if "networkFirst" not in sw:
    fails.append("sw.js has no networkFirst path")
else:
    print("sw.js: markup, scripts and styles are network-first with a cache fallback")

# --- 12. A note can be moved, which means the editor must offer a picker -----
# Moving is the only way a note changes folder, so the picker has to exist in the
# markup, be filled from the tree, and be the value saveNote actually writes.
if 'id="note-folder"' not in html:
    fails.append("index.html has no #note-folder, so a note cannot be moved")
if "folderOptions" not in app:
    fails.append("app.js never calls folderOptions, so the picker would stay empty")

save_note = body_of("saveNote")
if not save_note:
    fails.append("app.js has no saveNote")
else:
    if "els.noteFolder" not in save_note:
        fails.append("saveNote ignores the folder picker, so changing it would do nothing")

    # The value has to reach the stored object, not merely be read into a local:
    # a local named folderId that never lands in put() looks identical to a
    # passing check while changing the picker still does nothing.
    written = re.search(r'put\(\s*"notes"\s*,\s*\{(.*?)\}\s*\)', save_note, re.S)
    if not written:
        fails.append('saveNote does not put() the note, so nothing it reads would be saved')
    elif "folderId" not in written.group(1):
        fails.append("saveNote's put() omits folderId, so a move would not persist")

    # An empty <option> value means Unfiled, and the Unfiled list is read off the
    # folderId index. If "" reached the store those notes would vanish from both.
    if "|| null" not in save_note:
        fails.append("saveNote does not normalise the picker's empty value to null")

    if "els.noteFolder" in save_note and written and "folderId" in written.group(1):
        print("move: the editor offers a folder picker and saveNote writes its value to the store")

# --- 13. The status bar is for failures, and stays reachable ----------------
# Its two informational lines were removed on request, so it must take no space
# at rest. The element itself is the only thing that makes a startup failure
# visible -- an app that cannot start otherwise looks like one whose every button
# is broken, which is the outage this project already had. So it stays, hidden,
# and every path that writes to it has to reveal it.
if 'id="app-status"' not in html:
    fails.append("index.html has no #app-status, so a failure would have nowhere to go")
if 'class="statusbar" hidden' not in html:
    fails.append("the status bar is not hidden at rest, so it still takes the space its "
                 "informational lines were removed to reclaim")
for gone in ("storage-status", "reminder-status"):
    if gone in html or gone in app:
        fails.append(f"#{gone} is back: those two lines were removed on request")
bar_rule = re.search(r"\.statusbar\[hidden\]\s*\{\s*display:\s*none", css)
if not bar_rule:
    fails.append(".statusbar[hidden] does not set display:none, so the author display:flex "
                 "wins over [hidden] and the bar keeps its strip of the screen")
fatal_rule = re.search(r'body\[data-fatal="true"\]\s+\.statusbar\s*\{', css)
if not fatal_rule:
    fails.append("a startup failure no longer styles the status bar, so it would be invisible")
show_error = body_of("showError")
if not show_error:
    fails.append("app.js has no showError")
elif "hidden = false" not in show_error:
    fails.append("showError does not reveal the status bar, so every failure would be silent "
                 "now that the bar is hidden at rest")
elif bar_rule and fatal_rule and show_error:
    print("status bar: hidden at rest, revealed by showError, tinted on a fatal startup")

# --- 13b. The list preview honours the Enter keys the author pressed ---------
# A note typed with line breaks used to render in the list as one long line:
# the JS flattened \s+ to a space and the CSS pinned white-space:nowrap. Both
# halves must stay fixed -- fixing only the text (keeping nowrap) or only the
# CSS (keeping the flatten) reproduces the bug.
sub_rule = re.search(r"\.item-sub\s*\{[^}]*\}", css)
if not sub_rule:
    fails.append("app.css has no .item-sub rule for the note-list preview")
elif "white-space: pre-line" not in sub_rule.group(0):
    fails.append(".item-sub no longer sets white-space:pre-line, so the preview "
                 "collapses the author's line breaks back into one line")
elif re.search(r"text-overflow:\s*ellipsis", sub_rule.group(0)):
    fails.append(".item-sub still ellipsises; line-boundary cutting is noteSnippet's "
                 "job now, and the two disagree about where a line ends")
if "noteSnippet(" not in app:
    fails.append("app.js does not render the preview through noteSnippet, so the "
                 "one-line flatten is the live code again")
elif ".slice(0, 90)" in app:
    fails.append("the 90-char slice is back in the note row; bounds belong to "
                 "noteSnippet, which respects line boundaries")
if "noteSnippet" not in view:
    fails.append("view.js lost noteSnippet")
elif "white-space: pre-line" in (sub_rule.group(0) if sub_rule else "") \
        and "noteSnippet(" in app and "noteSnippet" in view:
    print("note preview: line breaks kept by noteSnippet + pre-line, bounds in one place")

# The title stacks as a block so the preview always starts on its own line
# regardless of inline whitespace in the markup; the preview itself stays
# inline so its pre-line line breaks are the only breaks it ever has.
stack_rule = re.search(r"\.item-main \.item-title\s*\{[^}]*\}", css)
if not stack_rule:
    fails.append("the note row's title no longer stacks as a block, so the "
                 "preview can end up beside the title instead of below it")
elif "display: block" not in stack_rule.group(0):
    fails.append("the .item-main .item-title rule lost display:block")

# The phone-width squeeze: the note row's date is a white-space:nowrap grid
# column that takes its full width BEFORE the 1fr preview column gets a share,
# so on a narrow list the preview dropped to a few characters and pre-line
# wrapped every word onto its own line. The narrow layout must drop the date.
narrow_when = re.search(r"\.item-row:not\(\.reminder-row\) \.when-abs\s*\{[^}]*\}", css)
if not narrow_when:
    fails.append("the narrow layout no longer drops the note row's date column, "
                 "which squeezed the preview to one word per line on a phone")
elif "display: none" not in narrow_when.group(0):
    fails.append("the note row's narrow date-column rule lost display:none")
elif narrow_when:
    print("narrow rows: the note date column stands down below 760px, "
          "so the preview owns the row")

# --- 13c. The visible version is the cache version ---------------------------
# The user asked to be able to see which revision is running. A number on
# screen is only an answer if it is the same number the offline cache is
# pinned to, so the two are locked here: view.js exports APP_VERSION, sw.js
# names its cache after it, and the dialog shows it. Bump both together --
# this section fails the build the moment they drift.
shown_version = re.search(r'export const APP_VERSION = "(\d+)";', view)
cached_version = re.search(r'CACHE_NAME = "notes-shell-v(\d+)";', sw)
if not shown_version:
    fails.append('view.js no longer exports APP_VERSION = "<number>", so the '
                 "app has no release number to show")
if not cached_version:
    fails.append("sw.js CACHE_NAME no longer reads notes-shell-v<number>")
if shown_version and cached_version:
    if shown_version.group(1) != cached_version.group(1):
        fails.append(f"the visible version (APP_VERSION {shown_version.group(1)}) "
                     f"and the shell cache (notes-shell-v{cached_version.group(1)}) "
                     "disagree -- bump them together, or the user reads one "
                     "number while another runs")
    else:
        print(f"version: the dialog shows {shown_version.group(1)} and sw.js "
              f"caches notes-shell-v{cached_version.group(1)} -- one number")
if 'id="app-version"' not in html:
    fails.append("index.html lost #app-version, so the release number is "
                 "visible nowhere")
if "APP_VERSION" not in app or "app-version" not in app:
    fails.append("app.js no longer writes APP_VERSION into #app-version")

# --- 14. Network rules, scoped (rewritten 2026-10-03) ------------------------
# The original rule was absolute: no fetch/XHR/sendBeacon/share in any app
# module, because "nothing you type can leave the device" except through the
# clipboard or the user's own mail app. On 2026-10-03 the user asked for the
# one-tap sync ("the moment i click the button export notes/reminders to email,
# it must sync all notes and jpg to my PC. just like how jscan does it"), which
# is impossible without one network call -- so sync.js is the single sanctioned
# exception, on exactly JScan's authorised terms (2026-09-21): fetch to
# https://api.github.com ONLY, the user's own PAT from localStorage ONLY, on an
# explicit user action ONLY. Everything else keeps the old ban, and sync.js
# itself is pinned so the exception cannot quietly widen.
network_clean = True
for module in ("app.js", "view.js", "backup.js", "storage.js", "reminder.js",
               "sync.js"):
    source = read(module)
    for banned in ("XMLHttpRequest", "sendBeacon", "navigator.share", "canShare"):
        if banned in source:
            network_clean = False
            fails.append(f"{module} contains {banned}; the app must never send "
                         "anything from the device")
for module in ("app.js", "view.js", "backup.js", "storage.js", "reminder.js"):
    source = read(module)
    if "fetch(" in source:
        network_clean = False
        fails.append(f"{module} contains fetch(; only sync.js may talk to the "
                     "network (the 2026-10-03 one-tap sync exception)")
sync_source = read("sync.js")
if sync_source:
    if "fetch(" in sync_source and "https://api.github.com" not in sync_source:
        network_clean = False
        fails.append("sync.js calls fetch but is not pinned to "
                     "https://api.github.com -- the exception is scoped to "
                     "exactly one origin")
    if "localStorage.getItem" not in sync_source \
            or "notes.sync.token" not in sync_source:
        network_clean = False
        fails.append("sync.js no longer reads its token from localStorage "
                     "(notes.sync.token) -- the token must stay on the device")
    if re.search(r"ghp_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}", sync_source):
        network_clean = False
        fails.append("sync.js contains what looks like a hardcoded GitHub "
                     "token -- tokens live in the user's localStorage, never "
                     "in the public source")
if network_clean:
    print("network: app modules stay silent except sync.js, which may fetch "
          "https://api.github.com only, with the user's own localStorage token")

# --- 15. The v19 pair: two export buttons, one-line editor actions, ----------
#        the visible reminder date, and real photo attachments.
#
# 15a. Export is exactly Share CSV for backup + Export CSV. Download and Share
# were removed on request (2026-10-01); their return would be a new decision.
# The clipboard button is labelled "share" because the paste into the relay
# inbox or an email is the share (label renamed 2026-10-02).
for gone in ("download-csv-btn", "share-csv-btn"):
    if f'id="{gone}"' in html:
        fails.append(f'#{gone} is back in the markup; the export panel is '
                     'Share CSV for backup + Export CSV only')
for label in (">Share CSV for backup</button>", ">Export CSV</a>"):
    if label not in html:
        fails.append(f"index.html lost the {label[1:].split('<')[0]} export button "
                     "(the user asked for exactly these two labels)")
if 'id="open-email-btn"' not in html:
    fails.append('index.html lost #open-email-btn -- Export CSV is the same '
                 'mailto anchor under its new label')
if 'id="i-download"' in html or 'href="#i-download"' in html or 'href="#i-download"' in app:
    fails.append("the i-download glyph is back, but nothing downloads anymore")

# 15b. The editor's Delete / Add photo/video / Save share one line.
editor_actions = re.search(r"\.editor-actions\s*\{([^}]*)\}", css)
if not editor_actions:
    fails.append("no .editor-actions rule found")
elif "nowrap" not in editor_actions.group(1):
    fails.append(".editor-actions does not pin flex-wrap: nowrap, so the three "
                 "editor buttons can wrap onto a second row")
else:
    print("editor actions: Delete / Add photo/video / Save pinned to one line")

# 15c. The narrow layout keeps the reminder DATE and drops the relative time --
# the reverse of what it used to do, per the 2026-10-01 request.
narrow_css = css.split("@media (max-width: 760px)", 1)
narrow_block = narrow_css[1] if len(narrow_css) > 1 else ""
if ".item-row.reminder-row .when-abs { display: none" in css:
    fails.append("the narrow layout hides the reminder's absolute date again; "
                 "the user asked for the date to stay and the relative time to go")
if ".item-row.reminder-row .when-rel { display: none" not in narrow_block:
    fails.append("the narrow layout does not stand down .when-rel, so the date "
                 "and the relative time would both fight for the phone row")
if ".item-row.reminder-row .when-abs::before { content: none" not in narrow_block:
    fails.append("the narrow layout leaves the · separator on .when-abs, which "
                 "leads the date with a dangling dot once .when-rel is hidden")
else:
    print("reminder rows: the date survives the narrow row; the relative time stands down")

# 15d. Photo attachments: strip markup, image glyph, OPFS helpers, cleanup.
# "Save to device" in the viewer is sanctioned (2026-10-02: the user chose the
# GitWay media route, which needs bytes to reach the phone's downloads for a
# manual upload to the PRIVATE relay repo); the CSV download button remains
# gone -- 15a still refuses #download-csv-btn.
for required in ('id="media-strip"', 'id="media-input"', 'id="media-dialog"',
                 'id="media-view"', 'id="media-video"', 'id="media-close"',
                 'id="media-save-btn"', 'id="add-media-btn"'):
    if required not in html:
        fails.append(f"index.html lost {required}, so photos have nowhere to render")
if '<symbol id="i-image"' not in html:
    fails.append("index.html has no i-image sprite symbol for the add-photo button")
if 'href="#i-image"' not in html:
    fails.append("the add-media button does not use the i-image glyph "
                 "(it still points at a folder icon)")
if 'accept="image/*,video/*"' not in html:
    fails.append('#media-input no longer accepts image/*,video/*')
for helper in ("export async function opfsPut(", "export async function opfsGet(",
               "export async function opfsDelete("):
    if helper not in storage:
        fails.append(f"storage.js lost {helper.split('(')[0].replace('export async function ', '')}() "
                     "-- photo bytes have nowhere to live")
for called in ("opfsPut(", "opfsGet(", "opfsDelete("):
    if called not in app:
        fails.append(f"app.js never calls {called}, so the OPFS layer is dead code")
delete_note = body_of("deleteSelectedNote")
if "opfsDelete(" not in delete_note or '"media"' not in delete_note:
    fails.append("deleteSelectedNote leaves the photos behind: it must remove each "
                 "media record and its OPFS bytes with the note")
if "makeThumbnail(" not in app or "renderMediaStrip(" not in app:
    fails.append("app.js lost the thumbnail/strip pipeline")
if 'URL.revokeObjectURL' not in app:
    fails.append("app.js never revokes object URLs, so every photo view leaks its bytes")
save_media = body_of("saveMediaToDevice")
if 'on("#media-save-btn"' not in app:
    fails.append('app.js never wires #media-save-btn, so "Save to device" is a dead button')
if "link.download" not in save_media or "URL.createObjectURL" not in save_media:
    fails.append("saveMediaToDevice no longer hands the OPFS bytes to a download anchor")
else:
    print("photos: strip + viewer wired; bytes in OPFS; delete and revoke paths pinned")

# 15e. Backup v2: the notes section carries mediaIds, and OLDER files parse.
backup = read("backup.js")
if 'SCHEMA_VERSION = 2' not in backup:
    fails.append("backup.js SCHEMA_VERSION is not 2 -- the mediaIds column needs "
                 "a version bump (v1 readers must refuse v2 files, never choke on them)")
if '"mediaIds"' not in backup:
    fails.append("backup.js has no mediaIds column in the notes section")
if "if (result.version > SCHEMA_VERSION)" not in backup:
    fails.append("parseBackupCsv does not accept OLDER backups (the gate must be "
                 "version > SCHEMA_VERSION, not !==: a v1 file has no mediaIds "
                 "column but is otherwise readable)")
if "Photos are not included" not in backup:
    fails.append("describeRestore no longer states that photos are not included, "
                 "so a restore could look like it lost someone's photos")
else:
    print("backup: v2 with a mediaIds column; v1 files still parse; the photo "
          "exclusion is stated in the restore confirmation")

# 15f. One-tap sync (2026-10-03): the export panel's primary action is the
# sync button, backed by the one-time token row. The transport pins live in
# the section 14 network rules; this pins the UI so the promise is reachable.
for required in ('id="sync-now-btn"', 'id="sync-status"', 'id="sync-token"',
                 'id="sync-token-save"', 'id="sync-token-remove"'):
    if required not in html:
        fails.append(f"index.html lost {required} -- the one-tap sync has no UI")
if ">Sync notes + photos to PC now</button>" not in html:
    fails.append('the sync button is no longer labelled "Sync notes + photos to PC now"')
sync_token_tag = re.search(r'<input[^>]*id="sync-token"[^>]*>', html)
if not sync_token_tag or 'type="password"' not in sync_token_tag.group(0):
    fails.append("#sync-token is not type=password -- the token must never "
                 "render in the clear on a shared screen")
for handler in ("runSync", "saveSyncToken", "removeSyncToken", "syncMediaName",
                "friendlySyncError"):
    if f"function {handler}(" not in app:
        fails.append(f"app.js lost {handler}() -- the sync flow is incomplete")
if 'on("#sync-now-btn"' not in app or 'on("#sync-token-save"' not in app \
        or 'on("#sync-token-remove"' not in app:
    fails.append("app.js does not wire the sync buttons -- dead controls")
if "checkPickedUp(" not in app or "syncSubmit(" not in app:
    fails.append("app.js no longer reports pickups or calls the sync transport")
if "hasToken()" not in app:
    fails.append("app.js does not gate the sync on a saved token -- it would "
                 "start an export that can only fail")
else:
    print("sync: button + token row wired; transport gated on the device token")

print()
if notes:
    print("NOTES:")
    for note in notes:
        print(f"  - {note}")
    print()
if fails:
    print("FAILURES:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL STATIC CHECKS PASSED")
