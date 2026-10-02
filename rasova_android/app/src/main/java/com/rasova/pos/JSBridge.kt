package com.rasova.pos

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import android.webkit.JavascriptInterface

/**
 * JavaScript bridge — methods here are callable from Django's JS as:
 *   Android.startPrintingWithKey(serverUrl, agentKey)
 *   Android.startPrinting(oldPollUrl)        (pages from before 1.1.0)
 *   Android.getPrintingStatus()
 *   Android.isNativeApp()
 *
 * @JavascriptInterface is a security annotation required by Android:
 * without it, JS cannot call the method even if it's public.
 */
class JSBridge(private val context: Context) {

    companion object {
        const val PREFS = "rasova_prefs"
    }

    /**
     * Called by Django on every page when running inside the native app.
     * Saves the server and the outlet's agent key, starts the print service,
     * and asks Android to exempt this app from battery optimization
     * (one-tap dialog). The key is sent in a header, never in a URL.
     */
    @JavascriptInterface
    fun startPrintingWithKey(serverUrl: String, agentKey: String) {
        if (serverUrl.isBlank() || agentKey.isBlank()) return
        start(AgentConfig.Target(serverUrl.trimEnd('/'), agentKey))
    }

    /** A page from before 1.1.0 hands over the old poll URL, key and all. */
    @JavascriptInterface
    fun startPrinting(pollUrl: String) {
        AgentConfig.fromOldPollUrl(pollUrl)?.let { start(it) }
    }

    private fun start(target: AgentConfig.Target) {
        // Skip if already polling this exact target: prevents duplicate loops on page navigation
        if (AgentConfig.load(context) == target && PrintService.status in listOf("active", "printing")) return

        // 1. Persist it; BootReceiver reads this on reboot
        AgentConfig.save(context, target)

        // 2. Start (or restart) the foreground print service
        val intent = Intent(context, PrintService::class.java)
            .putExtra(PrintService.EXTRA_SERVER_URL, target.serverUrl)
            .putExtra(PrintService.EXTRA_AGENT_KEY, target.agentKey)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(intent)
        } else {
            context.startService(intent)
        }

        // 3. Ask Android "don't kill this app" — shows a one-tap system dialog.
        //    We only show it if not already exempted (no repeated nagging).
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val pm = context.getSystemService(Context.POWER_SERVICE) as PowerManager
            if (!pm.isIgnoringBatteryOptimizations(context.packageName)) {
                Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).also {
                    it.data = Uri.parse("package:${context.packageName}")
                    it.flags = Intent.FLAG_ACTIVITY_NEW_TASK
                    context.startActivity(it)
                }
            }
        }
    }

    /**
     * Django can call this to show "Print Service Active ✓" in the UI.
     * Returns one of: "active", "printing", "error", "stopped", "no_url"
     */
    @JavascriptInterface
    fun getPrintingStatus(): String = PrintService.status

    /**
     * Lets Django JS know it's running inside the native app (not a browser).
     * Used to skip the post-install sheet and show "App Mode" messaging instead.
     */
    @JavascriptInterface
    fun isNativeApp(): Boolean = true
}
