package com.joseph.notes

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Android forgets alarms across a reboot; restore the stored next occurrences. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        when (intent?.action) {
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED -> ReminderScheduler.rescheduleAll(context)
        }
    }
}
