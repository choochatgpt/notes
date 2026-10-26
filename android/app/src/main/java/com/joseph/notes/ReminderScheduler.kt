package com.joseph.notes

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import org.json.JSONArray
import org.json.JSONObject

/**
 * Owns the small native reminder mirror. The web app remains authoritative;
 * this class only knows the next due instant it was last handed.
 *
 * Exact alarms are used when Android has granted that permission. Otherwise
 * setAndAllowWhileIdle still delivers the notification, possibly a little late.
 * Every sync cancels the previous request codes before scheduling the new list,
 * so completing or deleting a reminder cannot leave a ghost alarm behind.
 */
object ReminderScheduler {
    const val CHANNEL_ID = "notes-reminders"
    private const val PREFS = "notes_reminders"
    private const val KEY_ITEMS = "items"
    private const val EXTRA_KEY = "reminder_key"
    private const val EXTRA_ID = "reminder_id"
    private const val EXTRA_TITLE = "reminder_title"
    private const val EXTRA_BODY = "reminder_body"

    fun createChannel(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        val channel = NotificationChannel(
            CHANNEL_ID,
            context.getString(R.string.notification_channel_reminders),
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = context.getString(R.string.notification_channel_reminders_description)
            enableVibration(true)
        }
        manager.createNotificationChannel(channel)
    }

    fun sync(context: Context, items: JSONArray) {
        cancelStored(context)
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_ITEMS, items.toString())
            .apply()
        scheduleAll(context, items)
    }

    fun rescheduleAll(context: Context) {
        createChannel(context)
        val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_ITEMS, "[]") ?: "[]"
        val items = try {
            JSONArray(raw)
        } catch (e: Exception) {
            JSONArray()
        }
        scheduleAll(context, items)
    }

    private fun cancelStored(context: Context) {
        val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_ITEMS, "[]") ?: "[]"
        val items = try {
            JSONArray(raw)
        } catch (e: Exception) {
            null
        } ?: return
        val alarm = context.getSystemService(AlarmManager::class.java) ?: return
        for (i in 0 until items.length()) {
            val row = items.optJSONObject(i) ?: continue
            val key = row.optString("key")
            if (key.isBlank()) continue
            alarm.cancel(
                pending(
                    context,
                    key,
                    row.optString("reminderId"),
                    row.optString("title"),
                    row.optString("body"),
                    true
                )
            )
        }
    }

    private fun scheduleAll(context: Context, items: JSONArray) {
        val alarm = context.getSystemService(AlarmManager::class.java) ?: return
        val now = System.currentTimeMillis()
        for (i in 0 until items.length()) {
            val row = items.optJSONObject(i) ?: continue
            val key = row.optString("key")
            val reminderId = row.optString("reminderId")
            val triggerAt = row.optLong("triggerAt", 0L)
            if (key.isBlank() || reminderId.isBlank() || triggerAt <= 0L) continue
            // A due time already in the past is still shown promptly; it is not
            // silently dropped, because that would hide an overdue reminder.
            val at = maxOf(triggerAt, now + 1_000L)
            val pi = pending(
                context,
                key,
                reminderId,
                row.optString("title", "Untitled reminder"),
                row.optString("body", ""),
                false
            )
            try {
                if (Build.VERSION.SDK_INT >= 31 && alarm.canScheduleExactAlarms()) {
                    alarm.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
                } else {
                    alarm.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
                }
            } catch (e: SecurityException) {
                alarm.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
            }
        }
    }

    private fun pending(
        context: Context,
        key: String,
        reminderId: String,
        title: String,
        body: String,
        cancelling: Boolean
    ): PendingIntent {
        val intent = Intent(context, ReminderReceiver::class.java).apply {
            action = "com.joseph.notes.REMINDER"
            putExtra(EXTRA_KEY, key)
            putExtra(EXTRA_ID, reminderId)
            putExtra(EXTRA_TITLE, title)
            putExtra(EXTRA_BODY, body)
        }
        var flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        if (cancelling) flags = flags or PendingIntent.FLAG_NO_CREATE
        val existing = PendingIntent.getBroadcast(context, requestCode(key), intent, flags)
        return existing ?: PendingIntent.getBroadcast(
            context,
            requestCode(key),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    fun requestCode(id: String): Int = id.hashCode() and 0x7fffffff

    fun openExactAlarmSettings(context: Context) {
        if (Build.VERSION.SDK_INT < 31) return
        val intent = Intent(
            Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
            Uri.parse("package:${context.packageName}")
        ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            context.startActivity(intent)
        } catch (e: Exception) {
            context.startActivity(
                Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        }
    }
}
