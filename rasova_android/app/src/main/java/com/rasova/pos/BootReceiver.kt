package com.rasova.pos

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build

/**
 * Listens for BOOT_COMPLETED — fires once after the phone finishes starting up.
 * Reads the previously saved poll URL from SharedPreferences and restarts PrintService.
 * The user never needs to open the app after a reboot — printing just resumes.
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return

        val pollUrl = context
            .getSharedPreferences(JSBridge.PREFS, Context.MODE_PRIVATE)
            .getString(JSBridge.KEY_POLL_URL, null)
            ?: return  // no URL saved = never configured, nothing to do

        val serviceIntent = Intent(context, PrintService::class.java)
            .putExtra(PrintService.EXTRA_POLL_URL, pollUrl)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(serviceIntent)
        } else {
            context.startService(serviceIntent)
        }
    }
}
