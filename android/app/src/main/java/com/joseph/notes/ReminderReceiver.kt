package com.joseph.notes

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

/** Shows one reminder notification and opens the app when tapped. */
class ReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        ReminderScheduler.createChannel(context)

        val id = intent.getStringExtra("reminder_id").orEmpty()
        val title = intent.getStringExtra("reminder_title")
            ?.takeIf { it.isNotBlank() } ?: "Untitled reminder"
        val body = intent.getStringExtra("reminder_body").orEmpty()

        val open = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra("reminder_id", id)
        }
        val openPending = PendingIntent.getActivity(
            context,
            ReminderScheduler.requestCode(id),
            open,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val notification = NotificationCompat.Builder(context, ReminderScheduler.CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(body.ifBlank { "Reminder due" })
            .setStyle(NotificationCompat.BigTextStyle().bigText(body.ifBlank { "Reminder due" }))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setAutoCancel(true)
            .setContentIntent(openPending)
            .build()

        try {
            NotificationManagerCompat.from(context)
                .notify(ReminderScheduler.requestCode(id), notification)
        } catch (e: SecurityException) {
            // Notification permission was denied. The reminder is still in the
            // app and on the next launch the alarm is rescheduled.
        }
    }
}
