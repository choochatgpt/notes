# Notes Android shell

A native Kotlin WebView app around the local-first Notes app in `../source/`.
There is no Capacitor/Cordova/npm runtime.

## Build

The machine already has a reusable Android toolchain in
`C:\python\projects\gitway_projects\working\jscan\.toolchain`:

```powershell
$env:JAVA_HOME='C:\python\projects\gitway_projects\working\jscan\.toolchain\jdk'
cd C:\python\projects\gitway_projects\2026-09-27-01-Notes\android
.\gradlew.bat assembleRelease
```

`local.properties` points at that SDK. The `assembleRelease` task stages
`../source/` into `app/src/main/assets/www` first; that directory is generated
and must not be edited by hand.

Output:

```text
app/build/outputs/apk/release/app-release.apk
```

The project copy is:

```text
..\build\android\Notes-1.0.0.apk
```

The release variant is non-debuggable but signed with this machine's Android
debug key for personal sideloading. Use a dedicated keystore before distributing
updates from another build machine.

## Native surface

- `MainActivity.kt` — WebView, WebViewAssetLoader on the stable app-private
  `https://appassets.androidplatform.net` origin, file/camera picker, JS
  dialogs, mailto handoff, notification permission, back handling.
- `NotesBridge.kt` — `NotesNative`: chunked CSV/ZIP email attachments and the
  reminder mirror handed over from JavaScript.
- `ReminderScheduler.kt` — AlarmManager scheduling, exact when permitted and
  inexact otherwise; stores the last mirror and reschedules on boot.
- `ReminderReceiver.kt` — due notification; tapping opens the app.
- `BootReceiver.kt` — restores alarms after reboot or package replacement.

## Invariants

- App data remains in the WebView's app-private storage; no notes or media are
  uploaded by the shell.
- `INTERNET` exists only for the existing optional Drive/GitHub backup
  destinations. External navigation is blocked.
- Email text backup attaches a CSV. Email text + photos attaches a ZIP, and only
  after that explicit tap.
- Photo/video bytes use OPFS when available; `storage.js` falls back to
  IndexedDB `mediaBlobs` when a WebView lacks OPFS.
- Notification permission may be denied; reminders remain in the app and the
  alarm mirror is refreshed on every launch.

## Not covered by a host build

Camera capture, SAF picker, notification delivery, boot rescheduling, email
chooser behaviour and WebView OPFS support need a device or emulator.
