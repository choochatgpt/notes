package com.joseph.notes

import android.content.Intent
import android.net.Uri
import android.util.Base64
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.widget.Toast
import androidx.core.content.FileProvider
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * The only native surface exposed to JavaScript.
 *
 * The bridge is called on a WebView worker thread, so transfers are
 * synchronized and activity work is posted to the main thread. Email files
 * arrive in 32 KB base64 chunks; no single JS call holds a whole video.
 */
class NotesBridge(
    private val activity: MainActivity,
    private val webView: WebView
) {
    private companion object {
        const val MAX_TOTAL_BYTES = 512L * 1024 * 1024
        val ALLOWED_MIMES = setOf(
            "text/csv",
            "application/zip",
            "application/pdf",
            "image/jpeg",
            "image/png",
            "video/mp4",
            "application/octet-stream"
        )
    }

    private val lock = Any()
    private val emailRoot = File(activity.cacheDir, "email").apply { mkdirs() }

    private var batchDir: File? = null
    private var address: String = ""
    private var subject: String = "Notes backup"
    private var currentOut: FileOutputStream? = null
    private var currentFile: File? = null
    private var currentName: String? = null
    private var currentMime: String? = null
    private val completed = ArrayList<File>()
    private val completedMimes = ArrayList<String>()
    private var totalBytes = 0L

    @JavascriptInterface
    fun beginEmail(to: String, mailSubject: String): Boolean = synchronized(lock) {
        closeCurrent()
        cleanupBatch()
        completed.clear()
        completedMimes.clear()
        totalBytes = 0

        // Old staged attachments are disposable cache, not user data.
        emailRoot.listFiles()?.forEach { it.deleteRecursively() }

        val dir = File(emailRoot, "${System.currentTimeMillis()}-${System.nanoTime()}")
        if (!dir.mkdirs()) return false
        batchDir = dir
        address = to.trim()
        subject = mailSubject.trim().ifBlank { "Notes backup" }
        true
    }

    @JavascriptInterface
    fun addAttachment(name: String, mime: String): Boolean = synchronized(lock) {
        if (batchDir == null || currentOut != null) return false
        val dir = batchDir ?: return false
        val safeName = sanitizeName(name)
        val safeMime = if (mime in ALLOWED_MIMES) mime else "application/octet-stream"
        val file = File(dir, safeName)
        return try {
            currentOut = FileOutputStream(file)
            currentFile = file
            currentName = safeName
            currentMime = safeMime
            true
        } catch (e: Exception) {
            false
        }
    }

    @JavascriptInterface
    fun appendAttachmentChunk(base64: String): Boolean = synchronized(lock) {
        val out = currentOut ?: return false
        val bytes = try {
            Base64.decode(base64, Base64.NO_WRAP)
        } catch (e: IllegalArgumentException) {
            return false
        }
        if (bytes.isEmpty()) return true
        if (totalBytes + bytes.size > MAX_TOTAL_BYTES) return false
        return try {
            out.write(bytes)
            totalBytes += bytes.size
            true
        } catch (e: Exception) {
            false
        }
    }

    @JavascriptInterface
    fun endAttachment(): Boolean = synchronized(lock) {
        val out = currentOut ?: return false
        val file = currentFile
        val name = currentName
        val mime = currentMime
        return try {
            out.flush()
            out.close()
            currentOut = null
            if (file == null || name == null || mime == null) {
                false
            } else {
                completed.add(file)
                completedMimes.add(mime)
                currentFile = null
                currentName = null
                currentMime = null
                true
            }
        } catch (e: Exception) {
            closeCurrent()
            false
        }
    }

    @JavascriptInterface
    fun sendEmail(): Boolean = synchronized(lock) {
        closeCurrent()
        val files = ArrayList(completed)
        val types = ArrayList(completedMimes)
        if (files.isEmpty()) return false

        val uris = files.map {
            FileProvider.getUriForFile(activity, "${activity.packageName}.fileprovider", it)
        }
        val mime = if (types.distinct().size == 1) {
            types.firstOrNull() ?: "application/octet-stream"
        } else {
            "*/*"
        }

        val intent = if (uris.size == 1) {
            Intent(Intent.ACTION_SEND).apply {
                type = mime
                putExtra(Intent.EXTRA_STREAM, uris[0])
            }
        } else {
            Intent(Intent.ACTION_SEND_MULTIPLE).apply {
                type = "*/*"
                putParcelableArrayListExtra(Intent.EXTRA_STREAM, ArrayList(uris))
            }
        }
        if (address.isNotBlank()) {
            val recipients = address.split(',', ';')
                .map { it.trim() }
                .filter { it.isNotEmpty() }
                .toTypedArray()
            if (recipients.isNotEmpty()) intent.putExtra(Intent.EXTRA_EMAIL, recipients)
        }
        intent.putExtra(Intent.EXTRA_SUBJECT, subject)
        intent.putExtra(
            Intent.EXTRA_TEXT,
            "Notes backup generated on this device. The attached file contains the text data"
                + (if (mime == "application/zip") " plus every attached photo and video." else ". Photos and videos are not included.")
        )
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        val clip = ClipDataFactory.forUris(uris)
        if (clip != null) intent.clipData = clip

        val latch = CountDownLatch(1)
        var started = false
        activity.runOnUiThread {
            started = try {
                activity.startActivity(Intent.createChooser(intent, "Email Notes backup"))
                true
            } catch (e: Exception) {
                false
            }
            latch.countDown()
        }
        latch.await(5, TimeUnit.SECONDS)
        if (started) {
            completed.clear()
            completedMimes.clear()
            totalBytes = 0
            batchDir = null
        }
        started
    }

    @JavascriptInterface
    fun cancelEmail() = synchronized(lock) {
        closeCurrent()
        completed.clear()
        completedMimes.clear()
        totalBytes = 0
        cleanupBatch()
    }

    /**
     * Android stores the active list and schedules one alarm per future due
     * instant. The web app remains the source of truth: every render resends
     * the list, and a notification opens the app so it can advance a recurring
     * reminder and reschedule.
     */
    @JavascriptInterface
    fun syncReminders(json: String): Boolean {
        return try {
            val input = JSONArray(json)
            val clean = JSONArray()
            for (i in 0 until input.length()) {
                val row = input.optJSONObject(i) ?: continue
                val key = row.optString("key")
                val reminderId = row.optString("reminderId")
                val trigger = row.optLong("triggerAt", 0L)
                if (key.isBlank() || reminderId.isBlank() || trigger <= 0L) continue
                clean.put(JSONObject().apply {
                    put("key", key)
                    put("reminderId", reminderId)
                    put("title", row.optString("title", "Untitled reminder"))
                    put("body", row.optString("body", ""))
                    put("triggerAt", trigger)
                })
            }
            ReminderScheduler.sync(activity, clean)
            true
        } catch (e: Exception) {
            false
        }
    }

    @JavascriptInterface
    fun toast(message: String) {
        activity.runOnUiThread {
            Toast.makeText(activity, message, Toast.LENGTH_SHORT).show()
        }
    }

    @JavascriptInterface
    fun openExactAlarmSettings() {
        ReminderScheduler.openExactAlarmSettings(activity)
    }

    private fun closeCurrent() {
        try { currentOut?.flush() } catch (_: Exception) {}
        try { currentOut?.close() } catch (_: Exception) {}
        currentOut = null
        currentFile?.delete()
        currentFile = null
        currentName = null
        currentMime = null
    }

    private fun cleanupBatch() {
        val dir = batchDir ?: return
        batchDir = null
        try { dir.deleteRecursively() } catch (_: Exception) {}
    }

    private fun sanitizeName(name: String): String {
        val base = File(name).name
            .replace(Regex("[^A-Za-z0-9._ -]"), "_")
            .trim()
            .take(120)
        return if (base.isBlank()) "notes-backup.bin" else base
    }

    private object ClipDataFactory {
        fun forUris(uris: List<Uri>): android.content.ClipData? {
            if (uris.isEmpty()) return null
            val clip = android.content.ClipData.newRawUri(null, uris[0])
            for (i in 1 until uris.size) clip.addItem(android.content.ClipData.Item(uris[i]))
            return clip
        }
    }
}
