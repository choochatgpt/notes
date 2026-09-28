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
shell = re.search(r"\.app-shell\s*\{([^}]*)\}", css)
if not shell:
    fails.append("no .app-shell rule found")
else:
    rows = re.search(r"grid-template-rows:\s*([^;]+);", shell.group(1))
    if not rows:
        fails.append(".app-shell has no grid-template-rows, so the split is not stacked")
    else:
        # Count top-level tracks: minmax(a, b) is one track, not two.
        tracks = re.findall(r"minmax\([^()]*\)|\S+", rows.group(1))
        if len(tracks) != 2:
            fails.append(f".app-shell must be a two-row split, found {len(tracks)} tracks")
        else:
            # The two rows share the screen evenly. A ratio such as 2fr 1fr is a
            # different layout and nothing else here would notice it come back,
            # because it is still a two-row split with the notes row on top.
            shares = [re.search(r"([\d.]+)fr\b", track) for track in tracks]
            if not all(shares):
                fails.append(f".app-shell rows must both be fractional tracks, "
                             f"found {' '.join(tracks)}")
            elif shares[0].group(1) != shares[1].group(1):
                fails.append(f".app-shell must split the screen evenly, "
                             f"found {' '.join(tracks)}")
            else:
                print(f"stacked split: app-shell rows = {' '.join(tracks)} (even)")
if "pane-agenda" not in html:
    fails.append("index.html has no agenda pane")
if 'id="agenda-list"' not in html:
    fails.append("index.html has no #agenda-list")

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
    for button in ("new-folder-btn", "new-note-btn", "new-reminder-btn", "backup-btn"):
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
