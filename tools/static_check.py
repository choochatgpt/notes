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
dynamic = {"active", "current", "past", "hidden"}
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

# --- 5b. The brand mark is the reload button (v36) ----------------------------
# Tapping the top-left note icon reloads the app, so a phone picks up a new
# release without re-pasting the URL -- and it is the recovery path when
# startup fails. The button must wrap the mark only (a heading cannot live
# inside a button, so the <h1> stays a sibling), be a real type="button", and
# call location.reload() through the guarded on() helper.
brand_btn = re.search(
    r'<button class="brand-btn" id="reload-btn" type="button"[^>]*>', html)
if not brand_btn:
    fails.append("the brand mark is not a #reload-btn: the top-left icon must "
                 "reload the app so a phone picks up updates without re-pasting "
                 "the URL (v36)")
else:
    if "aria-label=" not in brand_btn.group(0):
        fails.append("#reload-btn has no aria-label -- a button without a name")
    brand_chunk = re.search(r'<button class="brand-btn" id="reload-btn".*?</button>',
                            html, re.S)
    if not brand_chunk or 'class="brand-mark"' not in brand_chunk.group(0) \
            or "<h1" in brand_chunk.group(0):
        fails.append("#reload-btn must wrap the .brand-mark only -- the <h1> stays "
                     "outside the button")
    if 'on("#reload-btn", "click", () => location.reload());' not in app:
        fails.append('app.js never wires #reload-btn to location.reload() -- '
                     "the brand mark cannot reload the app")
brand_css = re.search(r"\.brand-btn\s*\{([^}]*)\}", css)
if not brand_css:
    fails.append("no .brand-btn rule -- the reload button renders with UA chrome")
elif not all(token in brand_css.group(1)
             for token in ("cursor: pointer", "border: 0", "padding: 0")):
    fails.append(".brand-btn must shed the UA button chrome (padding/border/"
                 "background) so the .brand-mark tile paints exactly as before")
if not re.search(r"\.brand-btn:focus-visible\s*\{[^}]*outline:", css):
    fails.append(".brand-btn has no :focus-visible outline -- a keyboard user "
                 "cannot see where the reload button is")
else:
    print("brand mark: a real #reload-btn that calls location.reload()")

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
# The offered ratios are a product decision, not a style detail: the user's
# ten-percent steps from 10% through 90% (2026-10-05 revision; before it was
# 20/40/60/80), in order, wrapping. The default 50% is the CSS fallback and
# the chip's starting value, checked separately below; since this revision it
# is also one of the offered stops.
ratio_match = re.search(r"RATIOS\s*=\s*\[([^\]]+)\]", view)
offered = re.findall(r'"([^"]+)"', ratio_match.group(1)) if ratio_match else []
required = ["10%", "20%", "30%", "40%", "50%", "60%", "70%", "80%", "90%"]
if offered != required:
    fails.append(f"view.js RATIOS must be {required} in order "
                 f"(the requested 10%-step top shares), found {offered}")
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

# Since v38 the pane ratio is the ONE split left: the folder tree and its own
# ratio cycle went with the tree when the notes screen became one drill-down
# list. If the folder-ratio machinery ever returns (a Settings control, a
# --folder-track variable), it must be a deliberate user-visible re-shipping,
# not an accretion -- the same failure the tree itself died of being "always
# more".
if 'id="folder-ratio-btn"' in html or "FOLDER_RATIOS" in view or "--folder-track" in css:
    fails.append("v38 removed the folder tree and its ratio cycle; "
                 "folder-ratio machinery has reappeared (see v38 plan, notes screen)")

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
if 'els.crumbs.classList.toggle("hidden", !notes)' not in app:
    fails.append("app.js does not hide the crumbs bar off the reminders tab -- "
                 "folders are a notes concept (v38)")
if 'els.browseNotes.classList.toggle("hidden", !notes)' not in app:
    fails.append("app.js does not hide the notes browser with the notes tab alone: "
                 "since v35 the notes browser stays on screen while a note is "
                 "open, with only the list slot swapping to the editor")
if 'els.noteList.classList.toggle("hidden", editing)' not in app:
    fails.append("app.js never swaps the note list out of its slot while a note "
                 "is open, so the editor has nowhere to appear in its place")
if 'els.noteEditor.classList.toggle("hidden"' not in app:
    fails.append("app.js never toggles the note editor itself")
if 'els.editorHost.classList.toggle("hidden", !remindersEditing)' not in app:
    fails.append("app.js does not scope the editor host to reminder edits: since "
                 "v35 it holds only the reminder editor (the note editor moved "
                 "into the notes browser)")
if 'els.editorBack.classList.toggle("hidden"' not in app:
    fails.append("app.js never toggles the back arrow, so the editor cannot swap in place")
else:
    print("browse/edit swap: the crumbs bar stays, the list slot swaps, "
          "reminders keep the full-pane swap")
list_col = re.search(r'<div class="list-col">(.*?)</div>\s*</div>', html, re.S)
if not list_col or 'id="note-list"' not in list_col.group(1) \
        or 'id="note-editor"' not in list_col.group(1):
    fails.append("the note editor is not inside the list column beside the note "
                 "list -- v38 keeps the drill-down list and the editor in one panel")
if 'id="editor-attach"' not in html:
    fails.append("no editor-attach line: the open note must state its attachment "
                 "count at the top of the panel")
if ".editor-attach" not in css or ".list-col .editor" not in css:
    fails.append("the editor-in-list-slot and its attach line have no CSS rules, "
                 "so the swap would render unstyled")

# --- 7b. The breadcrumb bar and the drill-down list (v38) --------------------
# The notes screen is ONE list now: crumbs on top, subfolders then notes in
# the single scrolling panel. The pieces are small, so they are pinned: the
# helpers must exist in view.js, app.js must render the crumbs on the notes
# tab, and the list markup must carry the folder-row + menu contract.
if "export function folderChain(" not in view:
    fails.append("view.js has no folderChain, so the crumbs have no testable "
                 "source of truth for their segments")
if "folderChain(" not in app:
    fails.append("app.js never calls folderChain, so the crumbs would not render")
if "function renderCrumbs(" not in app or 'els.crumbs.innerHTML' not in app:
    fails.append("app.js has no renderCrumbs writing els.crumbs, so the breadcrumb "
                 "bar stays empty")
if 'els.crumbs.classList.toggle("hidden", !notes)' not in app:
    fails.append("renderCrumbs visibility is not wired in syncUpper -- the crumbs "
                 "must show on the notes tab and hide on reminders")
if 'id="crumbs"' not in html or 'class="crumbs"' not in html:
    fails.append("index.html has no #crumbs breadcrumb bar in the notes browser")
if ".crumbs" not in css or ".crumbs .crumb" not in css:
    fails.append("the breadcrumb bar renders unstyled -- .crumbs has no CSS rule")
if "function loadFolders(" not in app:
    fails.append("app.js has no loadFolders -- renderFolders (the tree) must be gone")
if "async function renderFolders(" in app:
    fails.append("app.js still defines renderFolders -- the v38 drill-down list "
                 "rendered folder rows in renderNoteList instead")
if "state.collapsed" in app or "folderChip" in app or "renamingFolderId" not in app:
    if "state.collapsed" in app:
        fails.append("state.collapsed survived -- the tree's collapse state died with it (v38)")
    if "folderChip" in app:
        fails.append("state.folderChip survived -- browsing shows crumbs now, not a chip (v38)")
if "renderFolders()" in app or "els.folderTree" in app:
    fails.append("app.js still calls renderFolders()/els.folderTree -- v38 removed the tree")
browse = app.split("async function renderNoteList", 1)
if len(browse) > 1:
    body = browse[1].split("\nasync function ", 1)[0]
    if "folderRowHtml(" not in body:
        fails.append("renderNoteList does not render folder rows (folderRowHtml) -- "
                     "the drill-down list must lead with subfolders")
    if "sortFoldersSiblings(" not in body:
        fails.append("renderNoteList does not order subfolders with sortFoldersSiblings")
    if "noteDisplayTitle(" not in body:
        fails.append("renderNoteList stopped using noteDisplayTitle -- untitled notes "
                     "must keep naming their folder (v37)")
else:
    fails.append("app.js has no renderNoteList")
if 'on("#note-list", "click", onNoteListClick)' not in app:
    fails.append("the drill-down list has no delegated click handler -- it must be "
                 'wired as on("#note-list", "click", onNoteListClick)')
for marker, why in (
    ("[data-menu]", "the folder rows carry no menu trigger"),
    ("function openFolderMenu(", "no openFolderMenu -- the ellipsis button would be inert"),
    ("function openMenuDialog(", "no openMenuDialog helper -- the v38 dialogs would not show"),
    ("function closeMenuDialog(", "no closeMenuDialog helper -- the v38 dialogs would never close"),
    ("function moveFolderTo(", "no moveFolderTo -- the move sheet would be inert"),
    ("collectSubtree(folderId, folders).includes(target)", "moveFolderTo does not refuse to move a folder into its own subtree"),
):
    if marker not in app:
        fails.append(f"app.js: {why}")
if 'id="folder-menu"' not in html or 'id="folder-move-sheet"' not in html:
    fails.append("index.html lost #folder-menu / #folder-move-sheet -- the folder "
                 "menu and move sheet must exist as dialogs")
if 'id="attach-menu"' not in html:
    fails.append("index.html lost #attach-menu -- the editor's single attach button "
                 "opens a menu, not three buttons")
print("drill-down: crumbs bar + subfolder rows + menus wired (v38)")

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
elif "noteDisplayTitle(" not in body_of("deleteSelectedNote"):
    fails.append("the note's delete confirmation no longer names what the row names "
                 "-- it must go through noteDisplayTitle (v37)")
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
if "data-menu=" not in app:
    fails.append("folder rows carry no menu trigger -- v38 moves rename/move/delete "
                 "behind the row's ellipsis button")
if ".folder-menu" not in css:
    fails.append(".folder-menu has no CSS rule, so the folder menu trigger would be invisible")
else:
    # It used to be hover-revealed (the .folder-del bug the user hit): the
    # control existed but could not be found, and on a touch screen (no hover)
    # it could not be reached at all. It must be on screen at rest.
    menu_rule = re.search(r"\.folder-menu\s*\{([^}]*)\}", css).group(1)
    for hidden in ("opacity: 0", "opacity:0", "pointer-events: none", "visibility: hidden",
                   "display: none"):
        if hidden in menu_rule:
            fails.append(
                f".folder-menu is hidden at rest ({hidden}); a folder menu that only "
                f"appears on hover is undiscoverable and unreachable on a touch screen")
    if "color:" not in menu_rule:
        fails.append(".folder-menu sets no resting colour, so it would be invisible")

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
               "sync.js", "drive.js", "config.js"):
    source = read(module)
    for banned in ("XMLHttpRequest", "sendBeacon", "navigator.share", "canShare"):
        if banned in source:
            network_clean = False
            fails.append(f"{module} contains {banned}; the app must never send "
                         "anything from the device")
for module in ("app.js", "view.js", "backup.js", "storage.js", "reminder.js",
               "config.js"):
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
# drive.js (2026-10-05): the second sanctioned speaker, scoped to exactly one
# origin (Google's), one scope (drive.file), a public-by-design Client ID from
# localStorage, and a memory-only access token. Deliberate widening of the
# 2026-10-03 exception; everything else keeps the old ban.
drive_source = read("drive.js")
if drive_source:
    if "https://www.googleapis.com" not in drive_source:
        network_clean = False
        fails.append("drive.js calls fetch but is not pinned to "
                     "https://www.googleapis.com -- the exception is scoped to "
                     "exactly one origin")
    if "https://www.googleapis.com/auth/drive.file" not in drive_source:
        network_clean = False
        fails.append("drive.js must request exactly the drive.file scope -- "
                     "nothing wider may ship")
    if "refresh_token" in drive_source:
        network_clean = False
        fails.append("drive.js must never hold a refresh token -- the access "
                     "token stays in memory only and expires")
    if re.search(r"GOCSPX-[A-Za-z0-9_-]{10,}", drive_source):
        network_clean = False
        fails.append("drive.js contains what looks like a client secret -- "
                     "only the public Client ID may exist")
    if "notes.drive.client" not in drive_source:
        network_clean = False
        fails.append("drive.js must keep its Client ID in localStorage "
                     "(notes.drive.client)")

# config.js (v29): the ONE application-config file and the only place the
# public Client ID may ship. It must carry the config drive.js reads; it
# must never carry a secret or an identity. Client ID, when present, is
# public by design (it names the app, not any user).
config = read("config.js")
if "NOTES_APP_CONFIG" not in config or "driveClientId" not in config:
    fails.append("config.js must set globalThis.NOTES_APP_CONFIG.driveClientId "
                 "-- the configured Client ID every visitor's Connect uses")
else:
    cfg_value = re.search(r'driveClientId\s*:\s*"([^"]*)"', config)
    if not cfg_value:
        fails.append('config.js has no quoted driveClientId value')
    elif cfg_value.group(1) and not cfg_value.group(1).endswith(
            ".apps.googleusercontent.com"):
        fails.append('config.js driveClientId is neither "" nor a Client ID '
                     'ending in .apps.googleusercontent.com')
if re.search(r"GOCSPX-[A-Za-z0-9_-]{10,}|refresh_token|ya29\.", config):
    network_clean = False
    fails.append("config.js carries a secret-shaped value (client-secret "
                 "prefix, refresh token or access token) -- only the public "
                 "Client ID may ship")
# config.js must be LOADED by the shell, not merely cached: a pasted Client ID
# is invisible to the app if nothing wires the file into the page (found on
# deployment day 2026-10-06 -- the app.js module tag was the only script tag,
# so globalThis.NOTES_APP_CONFIG never existed in the browser despite every
# file-level check passing).
index_html = read("index.html")
if '<script src="config.js"></script>' not in index_html:
    fails.append('index.html must carry <script src="config.js"></script> -- '
                 "drive.js reads globalThis.NOTES_APP_CONFIG, set by that "
                 "file; a cached-but-unloaded config.js is never configured")
# The per-user identity must never hardcode: no Client ID literal outside
# config.js, no access-token literal (Google's implicit-flow tokens start
# "ya29."), no account-id-shaped 21-digit number in any shipped file.
for module in ("app.js", "view.js", "sw.js", "sync.js", "drive.js",
               "storage.js", "reminder.js", "backup.js", "index.html"):
    source = read(module)
    if "apps.googleusercontent.com" in source:
        network_clean = False
        fails.append(f"{module} names a Client ID literal -- config.js is the "
                     "only place the app's Client ID may live")
    if re.search(r"ya29\.", source):
        network_clean = False
        fails.append(f"{module} contains an access-token literal (ya29...) -- "
                     "tokens are per user, memory-only, never in code")
    if re.search(r"(?<![\w-])\d{21}(?![\w-])", source):
        network_clean = False
        fails.append(f"{module} contains a 21-digit literal shaped like a "
                     "Google account id -- backups are per user; no account "
                     "id may be baked into the source")
if "accounts.google.com" in app:
    network_clean = False
    fails.append("app.js mentions accounts.google.com -- Google is reached "
                 "only through drive.js")
if re.search(r"<script[^>]*gsi", html):
    network_clean = False
    fails.append("index.html loads the GIS script tag up front -- drive.js "
                 "must inject it on demand, so nothing touches Google until "
                 "the user opts in")
if network_clean:
    print("network: app modules stay silent except sync.js (api.github.com) "
          "and drive.js (googleapis.com, drive.file scope), each pinned")

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

# 15b. The editor actions are ONE nowrap row since v38: Attach (the menu
# trigger), Delete, Save. Three pickers could not fit beside Delete and Save
# at phone width, which is why v36 used two rows; collapsing the pickers
# behind one paperclip fits, so the two-row exception is gone with it.
editor_actions = re.search(r"\.editor-actions\s*\{([^}]*)\}", css)
if not editor_actions:
    fails.append("no .editor-actions rule found")
elif "nowrap" not in editor_actions.group(1):
    fails.append(".editor-actions does not pin flex-wrap: nowrap, so an editor "
                 "action row can wrap onto a second row")
note_form = re.search(r'<form id="note-editor"[^>]*>(.*?)</form>', html, re.S)
if not note_form:
    fails.append('index.html lost <form id="note-editor">')
else:
    rows = re.findall(r'<div class="editor-actions">(.*?)</div>',
                      note_form.group(1), re.S)
    if len(rows) != 1:
        fails.append("the note editor must have exactly one action row (Attach, "
                     f"Delete, Save), found {len(rows)}")
    else:
        row = rows[0]
        if ('id="attach-menu-btn"' not in row or 'id="delete-note-btn"' not in row
                or 'id="save-note-btn"' not in row or 'id="add-' in row):
            fails.append("the editor-actions row must hold exactly Attach "
                         "(#attach-menu-btn), Delete and Save -- Save stays the "
                         "form's submit")
        elif '>Attach</span>' not in row or '>Delete</span>' not in row:
            fails.append('the action labels "Attach" and "Delete" must survive')
        else:
            print("editor actions: one nowrap row -- Attach (menu), Delete, Save")

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

# 15d. Attachments (photos/videos + PDFs since v34): strip markup, paperclip
# glyph, per-type tiles, the row's attachment-count line, OPFS helpers, cleanup.
# Since v38 the THREE pickers fire from ONE menu (#attach-menu); the hidden
# inputs and the change ritual are unchanged. "Save to device" in the viewer
# is sanctioned (2026-10-02: the user chose the GitWay media route, which needs
# bytes to reach the phone's downloads for a manual upload to the PRIVATE
# relay repo); the CSV download button remains gone -- 15a still refuses
# #download-csv-btn.
for required in ('id="media-strip"', 'id="media-input"', 'id="media-dialog"',
                 'id="media-view"', 'id="media-video"', 'id="media-frame"',
                 'id="media-hint"', 'id="media-close"',
                 'id="media-save-btn"', 'id="attach-menu-btn"',
                 'id="menu-gallery-btn"', 'id="menu-camera-btn"', 'id="menu-doc-btn"',
                 'id="gallery-input"', 'id="camera-input"'):
    if required not in html:
        fails.append(f"index.html lost {required}, so attachments have nowhere to render")
if '<symbol id="i-clip"' not in html:
    fails.append("index.html has no i-clip sprite symbol for the attachments layer")
if 'href="#i-clip"' not in html:
    fails.append("nothing uses the i-clip glyph -- the Attach button, the Document "
                 "menu item and the attachment-count lines carry it")
if '<symbol id="i-image"' not in html or 'href="#i-image"' not in html:
    fails.append("the gallery menu item has no i-image sprite symbol in use")
if '<symbol id="i-camera"' not in html or 'href="#i-camera"' not in html:
    fails.append("the camera menu item has no i-camera sprite symbol in use")
if 'on("#add-media-btn"' in app or 'id="add-media-btn"' in html \
        or 'id="add-gallery-btn"' in html or 'id="add-gallery-btn"' in app \
        or 'id="add-doc-btn"' in html or 'id="add-camera-btn"' in html:
    fails.append("the three picker buttons survived -- v38 replaced them with the "
                 "single Attach menu")
if 'accept="image/*,video/*,application/pdf"' not in html:
    fails.append('#media-input no longer accepts image/*,video/*,application/pdf')
if html.count('accept="image/*,video/*,application/pdf"') != 1:
    fails.append("the broad document accept must stay on #media-input only -- the "
                 "gallery and camera pickers are media-only")
gallery_input = re.search(r'<input id="gallery-input"[^>]*>', html)
if not gallery_input:
    fails.append("the gallery input vanished -- the attach menu's gallery item has "
                 "nowhere to pick into")
elif ('accept="image/*,video/*"' not in gallery_input.group(0)
      or "multiple" not in gallery_input.group(0) or "hidden" not in gallery_input.group(0)):
    fails.append("#gallery-input must accept image/*,video/* (the gallery picker) "
                 "and stay hidden")
camera_input = re.search(r'<input id="camera-input"[^>]*>', html)
if not camera_input:
    fails.append("the camera input vanished -- the attach menu's camera item has "
                 "nowhere to pick into")
elif ('capture="environment"' not in camera_input.group(0)
      or 'accept="image/*,video/*"' not in camera_input.group(0)
      or "hidden" not in camera_input.group(0)):
    fails.append('#camera-input must pin capture="environment" so Chrome on Android '
                 "opens the camera itself, and stay hidden")
for wired in ('on("#attach-menu-btn", "click", () => openMenuDialog("#attach-menu"));',
              'on("#menu-gallery-btn", "click", () => { closeMenuDialog("#attach-menu"); $("#gallery-input")?.click(); });',
              'on("#menu-camera-btn", "click", () => { closeMenuDialog("#attach-menu"); $("#camera-input")?.click(); });',
              'on("#menu-doc-btn", "click", () => { closeMenuDialog("#attach-menu"); $("#media-input")?.click(); });',
              'on("#gallery-input", "change", attachFromInput);',
              'on("#media-input", "change", attachFromInput);',
              'on("#camera-input", "change", attachFromInput);'):
    if wired not in app:
        fails.append(f"app.js lost the picker wiring: {wired}")
if not re.search(r"await addNoteMedia\(input\.files\);.{0,120}input\.value = \"\";",
                 app, re.S):
    fails.append("the shared picker ritual is broken: addNoteMedia must run from the "
                 'files and "input.value = \\"\\"" must reset only after it finishes, '
                 "so re-picking the same file still fires a change event")
for helper in ("export async function opfsPut(", "export async function opfsGet(",
               "export async function opfsDelete("):
    if helper not in storage:
        fails.append(f"storage.js lost {helper.split('(')[0].replace('export async function ', '')}() "
                     "-- attachment bytes have nowhere to live")
for called in ("opfsPut(", "opfsGet(", "opfsDelete("):
    if called not in app:
        fails.append(f"app.js never calls {called}, so the OPFS layer is dead code")
delete_note = body_of("deleteSelectedNote")
if "opfsDelete(" not in delete_note or '"media"' not in delete_note:
    fails.append("deleteSelectedNote leaves the attachments behind: it must remove each "
                 "media record and its OPFS bytes with the note")
if "attachment" not in delete_note:
    fails.append('deleteSelectedNote no longer names the attachments in its warning '
                 '(the wording moved from "attached photos" to "attachment(s)" in v34)')
add_note_media = body_of("addNoteMedia")
if 'file.type === "application/pdf"' not in add_note_media:
    fails.append("addNoteMedia no longer accepts PDFs -- documents would die in the picker")
media_strip = body_of("renderMediaStrip")
if "media-file-tag" not in media_strip:
    fails.append("renderMediaStrip has no document tile -- a PDF would paint as a broken img")
view_media = body_of("viewMedia")
if 'media-frame' not in view_media or '"application/pdf"' not in view_media:
    fails.append("viewMedia lost the PDF iframe branch -- documents open to nothing")
close_media = body_of("closeMedia")
if "media-frame" not in close_media:
    fails.append("closeMedia never clears the document iframe, so the next view "
                 "could flash the last PDF's bytes")
if "attachmentBadge(" not in view or "attachmentBadge(" not in app:
    fails.append("attachmentBadge is not both defined (view.js) and used (app.js) -- "
                 "the note rows cannot render their attachment counts")
if "application/pdf" not in body_of("syncMediaName"):
    fails.append("syncMediaName lost the .pdf extension -- a nameless PDF relay-zips unrecognisably")
if "makeThumbnail(" not in app or "renderMediaStrip(" not in app:
    fails.append("app.js lost the thumbnail/strip pipeline")
if 'URL.revokeObjectURL' not in app:
    fails.append("app.js never revokes object URLs, so every photo view leaks its bytes")
save_media = body_of("saveMediaToDevice")
if 'on("#media-save-btn"' not in app:
    fails.append('app.js never wires #media-save-btn, so "Save to device" is a dead button')
if "link.download" not in save_media or "URL.createObjectURL" not in save_media:
    fails.append("saveMediaToDevice no longer hands the OPFS bytes to a download anchor")
if ".item-attach" not in css:
    fails.append("app.css has no .item-attach rule -- the note rows' attachment "
                 "count line would render unstyled")
if not re.search(r"#media-frame\s*\{[^}]*width:", css):
    fails.append("app.css never sizes #media-frame -- a document viewer with no box")
else:
    print("attachments: strip + PDF iframe wired; rows carry attachmentBadge; "
          "bytes in OPFS; delete and revoke paths pinned")

# 15e. Backup v3: the notes section carries mediaIds, folders carry order, and
# OLDER files (v1 and v2) parse.
backup = read("backup.js")
if 'SCHEMA_VERSION = 3' not in backup:
    fails.append("backup.js SCHEMA_VERSION is not 3 -- the folder order column needs "
                 "a version bump (v2 readers must refuse v3 files, never choke on them)")
if '"mediaIds"' not in backup:
    fails.append("backup.js has no mediaIds column in the notes section")
if '"order"' not in backup:
    fails.append('backup.js has no "order" column in the folders section (v32: '
                 "the folder list can be arranged, so an export must carry positions)")
folders_columns = re.search(r'name: "folders", columns: \[([^\]]+)\]', backup)
if not folders_columns or '"order"' not in folders_columns.group(1):
    fails.append('the folders SECTIONS entry lost the "order" column')
if "if (result.version > SCHEMA_VERSION)" not in backup:
    fails.append("parseBackupCsv does not accept OLDER backups (the gate must be "
                 "version > SCHEMA_VERSION, not !==: a v1 file has no mediaIds "
                 "column but is otherwise readable)")
if "Attached files are not " not in backup or "included in a backup." not in backup:
    fails.append("describeRestore no longer states that attached files are not "
                 "included, so a restore could look like it lost someone's files")
if "Attached files are not included in a backup." not in app:
    fails.append("the restore panel's own preview paragraph lost the attachment "
                 "exclusion sentence (v34: it must say attached FILES, not photos)")
else:
    print("backup: v3 with mediaIds + order columns; v1 and v2 files still parse; "
          "the attachment exclusion is stated in the restore confirmation")

# 15f. One-tap backup (2026-10-03; destination-independent since v28): the
# export panel's primary action runs every configured destination. The relay
# token gates ONLY the relay; the transport pins live in the section 14
# network rules; this pins the UI so the promise is reachable.
for required in ('id="sync-now-btn"', 'id="sync-status"', 'id="sync-token"',
                 'id="sync-token-save"', 'id="sync-token-remove"',
                 'id="backup-overall"'):
    if required not in html:
        fails.append(f"index.html lost {required} -- the one-tap backup has no UI")
if ">Back up notes + photos now</button>" not in html:
    fails.append('the backup button is no longer labelled "Back up notes + photos now"')
sync_token_tag = re.search(r'<input[^>]*id="sync-token"[^>]*>', html)
if not sync_token_tag or 'type="password"' not in sync_token_tag.group(0):
    fails.append("#sync-token is not type=password -- the token must never "
                 "render in the clear on a shared screen")
for handler in ("runSync", "saveSyncToken", "removeSyncToken", "syncMediaName",
                "friendlySyncError"):
    if f"function {handler}(" not in app:
        fails.append(f"app.js lost {handler}() -- the backup flow is incomplete")
if 'on("#sync-now-btn"' not in app or 'on("#sync-token-save"' not in app \
        or 'on("#sync-token-remove"' not in app:
    fails.append("app.js does not wire the backup buttons -- dead controls")
if "checkPickedUp(" not in app or "syncSubmit(" not in app:
    fails.append("app.js no longer reports pickups or calls the sync transport")
if "hasToken()" not in app:
    fails.append("app.js no longer gates the PC relay on a saved token -- "
                 "it would start a relay run that can only fail")
else:
    print("sync: backup button + both destination rows wired; relay gated on "
          "the device token only")

# 15g. Google Drive destination (PRIMARY since v28; PER-USER since v29): the
# app ships ONE public Client ID in config.js and every visitor connects their
# OWN Google account with a Connect button -- no per-device paste, no client
# secret, token in memory only. With only the configured Client ID present a
# tap must reach Google and nothing else. The consent popup is allowed only
# on the explicit tap -- never on the export panel's auto-run. Transport pins
# live in section 14; this pins the UI, the per-user Connect/Disconnect flow,
# the destination independence shape and the no-popup-on-auto-run rule.
for required in ('id="drive-connect"', 'id="drive-disconnect"',
                 'id="drive-status"'):
    if required not in html:
        fails.append(f"index.html lost {required} -- the Drive backup has no UI")
for gone in ('id="drive-client-id"', 'id="drive-id-save"',
             'id="drive-id-remove"'):
    if gone in html:
        fails.append(f"index.html still has {gone} -- v29 replaced per-device "
                     "Client-ID pasting with the Connect/Disconnect buttons")
if ">Connect Google Drive</button>" not in html:
    fails.append('the Drive button is no longer labelled "Connect Google Drive"')
if ">Disconnect Google Drive</button>" not in html:
    fails.append('index.html lost the "Disconnect Google Drive" button')
for handler in ("connectDrive", "disconnectDrive", "refreshDriveUi",
                "runDriveBackup", "collectBackupBundle", "runRelayBackup",
                "runBackupAll", "renderBackupStatus", "kickOffDriveToken"):
    if f"function {handler}(" not in app:
        fails.append(f"app.js lost {handler}() -- the backup flow is incomplete")
if 'on("#drive-connect"' not in app or 'on("#drive-disconnect"' not in app:
    fails.append("app.js does not wire the Connect/Disconnect buttons -- dead controls")
if "hasClientId(" not in app:
    fails.append("app.js does not gate the Drive backup on the configured Client ID")
if "NOTES_APP_CONFIG" not in read("drive.js"):
    fails.append("drive.js no longer reads the app-config Client ID "
                 "(globalThis.NOTES_APP_CONFIG) -- every device would need "
                 "its own paste again")
if "NOTES_APP_CONFIG" in app:
    fails.append("app.js consults the app config directly -- the configured "
                 "Client ID is resolved in drive.js only")
if "friendlyDriveError(" not in app:
    fails.append("app.js no longer maps Drive failures through "
                 "friendlyDriveError")
backup_body = re.search(r"async function runBackupAll\(\{ driveAllowed \}\) \{"
                        r"([\s\S]*?)\n\}", app)
if not backup_body or "collectBackupBundle(" not in backup_body.group(1) \
        or "runDriveBackup(" not in backup_body.group(1) \
        or "runRelayBackup(" not in backup_body.group(1):
    fails.append("runBackupAll must collect the bundle once and hand it to "
                 "both destination runners -- the v28 independence shape")
if "No backup destination is configured." not in app:
    fails.append('app.js lost the "No backup destination is configured." '
                 "verdict -- a tap with nothing configured must say so")
if "Add your backup token below" in app:
    fails.append("app.js still gates everything on the relay token -- v28 "
                 "requires the Drive destination to run with no token at all")
panel_body = re.search(r"async function showExportPanel\(\) \{([^}]*)\}", app)
if not panel_body or "runBackupAll" not in panel_body.group(1) \
        or "driveAllowed: false" not in panel_body.group(1):
    fails.append("showExportPanel must run runBackupAll({driveAllowed: false}) "
                 "-- the auto-run may never open Google's sign-in window")
if "requestAccessToken" in app:
    fails.append("app.js touches the GIS token client directly -- sign-in "
                 "goes through drive.js only")
if "requestAccessToken" not in drive_source:
    fails.append("drive.js lost requestAccessToken() -- the GIS sign-in flow "
                 "is incomplete")
else:
    print("drive: Connect/Disconnect wired; Client ID ships in app config only "
          "(config.js); backup identity is per user; destinations independent; "
          "auto-run pinned to driveAllowed:false; sign-in confined to drive.js")

# 15h. Folder rename + move (v38): the per-row pencil/up/down chip row went
# with the tree; rename opens from the row's ellipsis menu and STAYS an inline
# editing form; moving is across parents now (moveFolderTo). The arranged
# order field and its one shared sibling sort survive untouched -- folders the
# user already arranged keep their order in the list and the picker.
if '<symbol id="i-pencil" viewBox="0 0 24 24">' not in html:
    fails.append('index.html lost the i-pencil symbol -- the rename menu item '
                 "renders an empty glyph without it")
for required in ('id="folder-menu-rename"', 'id="folder-menu-move"',
                 'id="folder-menu-delete"', 'id="folder-move-list"',
                 'id="folder-move-cancel"'):
    if required not in html:
        fails.append(f"index.html lost the folder-menu button {required!r}")
for required in (".folder-rename-form", "folder-rename-input",
                 "folder-rename-cancel", 'data-renaming="${esc(folder.id)}"'):
    if required not in app:
        fails.append(f"app.js lost the rename row piece {required!r}")
for function_name in ("onFolderMenuAction", "openFolderMenu", "openFolderMoveSheet",
                      "onFolderMoveListClick", "moveFolderTo", "beginFolderRename",
                      "submitRenameFolder", "cancelFolderRename", "clearRenameState",
                      "wireRenameRow"):
    if f"function {function_name}(" not in app:
        fails.append(f"app.js lost {function_name}() -- the rename/move flow "
                     "is incomplete")
if "renamingFolderId" not in app:
    fails.append("app.js lost state.renamingFolderId -- a rename edit would "
                 "vanish on the next re-render")
if "function sortFoldersSiblings(" not in view:
    fails.append("view.js lost sortFoldersSiblings() -- the drill list and the "
                 "picker must sort through one helper")
if "sortFoldersSiblings(" not in app:
    fails.append("app.js does not render through sortFoldersSiblings -- the "
                 "list would reorder itself back to alphabetical")
if "sortFoldersSiblings(children)" not in view or "sortFoldersSiblings(" not in view:
    fails.append("folderOptions does not mirror the arranged list through "
                 "sortFoldersSiblings")
if 'setSetting("folderOrder"' in app or 'getSetting("folderOrder"' in app:
    fails.append("folder order is a note-record field, not a device setting -- "
                 "it must ride the folders store and the backup CSV")
if re.search(r"\bfunction moveFolder\b\(", app):
    fails.append("the old sibling-shuffle moveFolder survived -- v38 replaced it "
                 "with moveFolderTo (move between parents)")
# The arrow glyphs never appear as row controls again.
if 'data-up="${esc(folder.id)}"' in app or 'data-down="${esc(folder.id)}"' in app \
        or ".folder-up" in css or ".folder-down" in css:
    fails.append("the up/down reorder controls survived -- v38 removed the arrows "
                 "(arranged order data still sorts, but nothing rewrites it)")
# The menu trigger repeats the .folder-del doctrine: never hover-revealed.
menu_rule = re.search(r"\.folder-menu\s*\{([^}]*)\}", css)
if menu_rule:
    for banned in ("opacity: 0", "opacity:0", "pointer-events: none",
                   "visibility: hidden", "display: none"):
        if banned in menu_rule.group(1):
            fails.append(f'the folder-menu rule hides its control at rest '
                         f'("{banned}") -- the .folder-del anti-hover-reveal doctrine')
for cleanup_call in ("cancelFolderRename();", "clearRenameState();"):
    if cleanup_call not in app:
        fails.append(f"app.js lost the {cleanup_call} cleanup call")
if ".folder-row.editing" in app and ".folder-row.editing" not in css:
    fails.append("the inline rename row has no CSS")
print("rename/move: rename stays an inline editing row (menu-opened); move "
      "crosses parents via the sheet, never its own subtree; arranged order "
      "sorts but only the sheet rewrites folders; no arrows (v38)")

# --- 16. The agenda steps aside while a NOTE is edited (v36) ------------------
# Pure CSS: syncUpper already writes body[data-kind]/[data-mode], so "a note
# editor is open" is exactly edit+notes. The collapse rule must replace the
# two-row split wholesale (making the inline --pane-top/--pane-bottom inert)
# and must appear AFTER the base .app-shell rule, because section 6 reads the
# first match. The border/shadow kill rides along so the collapsed agenda
# paints nothing.
collapse = re.search(
    r'body\[data-mode="edit"\]\[data-kind="notes"\] \.app-shell\s*\{([^}]*)\}',
    css)
if not collapse:
    fails.append("no agenda-collapse rule: while a note is edited the agenda must "
                 "give the editor the whole shell (v36)")
else:
    if "minmax(0, 1fr)" not in collapse.group(1) \
            or "minmax(0, 0fr)" not in collapse.group(1):
        fails.append("the agenda-collapse rule must pin the editor's track to full "
                     "and the agenda's to minmax(0, 0fr)")
    elif re.search(r'body\[data-mode="edit"\]\[data-kind="notes"\] \.app-shell\s*\{',
                   css).start() < re.search(r"\.app-shell\s*\{", css).start():
        fails.append("the agenda-collapse rule must come after the base .app-shell "
                     "rule -- section 6 reads the first match")
agenda_kill = re.search(
    r'body\[data-mode="edit"\]\[data-kind="notes"\] \.pane-agenda\s*\{([^}]*)\}',
    css)
if not agenda_kill:
    fails.append("the collapsed agenda still paints its border/shadow sliver -- "
                 "add the .pane-agenda border/shadow-kill companion rule")
elif "border: 0" not in agenda_kill.group(1) \
        or "box-shadow: none" not in agenda_kill.group(1):
    fails.append("the .pane-agenda companion rule must kill both the border and "
                 "the shadow while the agenda is collapsed")
else:
    print("agenda: steps aside while a note is edited (pure CSS on body[data-*])")

# --- 17. An untitled note is titled after its folder (v37) -------------------
title_helper_pos = view.find("export function noteDisplayTitle(")
if title_helper_pos == -1:
    fails.append("view.js has no noteDisplayTitle -- the untitled-after-folder "
                 "rule has no single owner")
else:
    # The helper's own chunk: from its signature to the next export, so the
    # branch literals cannot be argued to live somewhere else.
    next_export = view.find("export", title_helper_pos + 10)
    helper_chunk = view[title_helper_pos:
                        next_export if next_export != -1 else len(view)]
    if "noteDisplayTitle(" not in app:
        fails.append("app.js never calls noteDisplayTitle")
    elif "noteDisplayTitle(" not in body_of("renderNoteList"):
        fails.append("renderNoteList no longer titles rows through "
                     "noteDisplayTitle")
    elif '"Unfiled"' not in helper_chunk:
        fails.append("noteDisplayTitle lost the Unfiled branch -- an untitled "
                     "unfiled note must read Unfiled, the pseudo-folder's own "
                     "label")
    elif '"Untitled note"' not in helper_chunk:
        fails.append("noteDisplayTitle lost the honest Untitled-note "
                     "degradation for a folder that cannot be read")
    else:
        print("untitled note: titled after its folder -- one helper owns the "
              "row and the delete confirmation")

# --- 18. Notes are created in leaf folders only (v39) ------------------------
if "export function canHoldNotes(" not in view:
    fails.append("view.js has no canHoldNotes -- the leaf rule has no single "
                 "testable owner (v39)")
if "export function leafFolderOptions(" not in view:
    fails.append("view.js has no leafFolderOptions -- the picker's filter has "
                 "no single testable owner (v39)")
elif "canHoldNotes(" not in body_of("createNote"):
    fails.append("createNote has no leaf belt check -- a queued, stale-paired "
                 "or script-driven click could write a note into a folder that "
                 "cannot hold one (v39)")
elif "folderOptions(" in body_of("createNote"):
    fails.append("createNote must not walk the folder tree itself -- the rule "
                 "answers through canHoldNotes (v39)")
elif "function syncNewNoteGate(" not in app:
    fails.append("app.js has no syncNewNoteGate -- a disabled New note with no "
                 "explanation is the dead-button outage (v39)")
elif "syncNewNoteGate(" not in body_of("renderAll"):
    fails.append("renderAll never runs syncNewNoteGate -- the gate must re-read "
                 "on every render path, because create, move, delete and "
                 "restore all change what a folder holds (v39)")
elif not ("disabled" in body_of("syncNewNoteGate")
          and ".title" in body_of("syncNewNoteGate")):
    fails.append("syncNewNoteGate must set both disabled and the explanatory "
                 "title -- a dead button that teaches nothing is the outage "
                 "pattern (v39)")
elif "els.newNoteBtn" not in app:
    fails.append("app.js has no newNoteBtn element -- the gate has nothing to "
                 "act on (v39)")
elif not re.search(r'import\s*\{[^}]*canHoldNotes[^}]*\}\s*from "\./view\.js"', app) \
        or not re.search(r'import\s*\{[^}]*leafFolderOptions[^}]*\}\s*from "\./view\.js"', app):
    fails.append("app.js must import canHoldNotes and leafFolderOptions from "
                 "view.js -- a call of an unimported helper cannot start the "
                 "app at all (v39)")
elif "leafFolderOptions(" not in body_of("renderFolderPicker"):
    fails.append("the note picker must be built through leafFolderOptions -- "
                 "Unfiled, the leaves, and the note's own folder (v39)")
elif "folderOptions(state.folders)" in body_of("renderFolderPicker"):
    fails.append("renderFolderPicker still offers every folder -- the picker "
                 "must go through leafFolderOptions (v39)")
elif "subfolders)" not in body_of("renderFolderPicker"):
    fails.append("the picker's current-but-non-leaf entry must say why it is "
                 "there (v39)")
elif "data-leaf-hint" not in body_of("renderNoteList"):
    fails.append("renderNoteList renders no leaf hint -- a disabled button "
                 "needs a visible explanation on the same screen (v39)")
elif 'class="muted"' not in body_of("renderNoteList"):
    fails.append("the leaf hint must reuse the .muted line style -- no new CSS "
                 "for a line of text (v39)")
elif "canHoldNotes(" in body_of("restoreBackup"):
    fails.append("restore must not leaf-filter -- a restore writes back exactly "
                 "what the backup carried; the rule is a menu, not a migration "
                 "(v39)")
else:
    print("leaf folders: new notes go in Unfiled or a leaf folder -- the gate, "
          "its explanation and the picker's filter each have one owner (v39)")

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
